"""#1308：Lambda 上的分散式 Bedrock 1 RPS 全域鎖（DynamoDB conditional owner lock）。

背景：`bedrock.BedrockRpsLimiter` 用 host-local `flock` + 檔案時間戳把整台主機
的 Bedrock invoke 節流到 1 RPS 以內（競賽硬性規範 #1203）。該作法在單機
（EC2）成立，但 Lambda 的 `/tmp` 只在單一執行環境內共享，跨執行環境完全看不到
彼此的時間戳——所以原本的限制器在偵測到 `AWS_LAMBDA_FUNCTION_NAME` 時**直接
fail-closed 拒絕**（`"Lambda Bedrock calls require a real shared distributed
limiter"`），代價是 competition Lambda v3 完全無法做 live 分析。

本模組補上那個「real shared distributed limiter」：把節流狀態放到**既有的**
competition budget DynamoDB 表（`competition-trustforge-team11-budget`，
PK=`source_id`、SK=`coin`），沿用 `budget_counter.DynamoDBBudgetCounter` 的
保留字慣例，用一組保留 key 存放全域鎖：

    source_id = "__bedrock_rps_lock__"
    coin      = "global"

⚠️ 刻意不新增資料表、不新增 GSI、不改任何 IAM：本模組只用
`deploy/competition-lambda-live-contract.json` 既有授予的 `dynamodb:GetItem`
與 `dynamodb:UpdateItem`（同一張 budget 表的 ARN）。沒有 schema 異動、沒有
migration。

鎖語意（owner-conditional lease）：
    `available_at`（epoch 秒）— 下一個呼叫者最早可以取得鎖的時刻。
    `lock_owner`（隨機 token）— 目前持有者，release 時據此做條件式判斷。

    acquire：
        UpdateItem SET lock_owner=:owner, available_at=:guard
        ConditionExpression: attribute_not_exists(available_at)
                             OR available_at <= :now
      成功 → 取得鎖，並把 `available_at` 先推到 `now + hold_seconds`（guard）。
      這個 guard 是**故障保險**：萬一本次 release 沒跑到（process 被 Lambda
      殺掉、網路斷線），鎖不會永久卡死，但也不會立刻開放——維持 fail-closed。

    release：
        先用 owner 本機 monotonic clock 持鎖到 invoke 起始後至少 1 秒，再執行
        owner-conditional UpdateItem SET available_at=:released_at
        ConditionExpression: lock_owner = :owner
      monotonic 等待讓 cooldown 不依賴不同 Lambda execution environment 的 wall
      clock 是否同步；release 後的 wall timestamp 只決定較慢時鐘是否保守多等，
      不可能讓較快時鐘在真實一秒前取得鎖。條件綁 owner，確保不會把別人
      （guard 過期後接手的下一位）的鎖誤推遲或誤釋放。

fail-closed 三條路（規範要求）：
    1. DynamoDB 操作失敗（憑證/網路/throttle/表不存在）→ `BedrockLockBackendError`，
       呼叫端不得改走離線或無鎖路徑。
    2. 競爭等待超過 `contention_deadline_seconds` → `BedrockLockContentionError`。
    3. release 失敗（條件不成立或後端錯誤）→ raise；此時 `available_at` 仍停在
       guard，後續呼叫者被擋到 guard 過期為止，節流不會被放寬。

EC2 production 與 Lambda 共用本模組及同一 table/item；只有明確未選用
production DynamoDB backend 的本機開發環境保留 host-local flock。
"""
from __future__ import annotations

import logging
import os
import random
import secrets
import threading
import time
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Callable, Iterator

_log = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 3.0
_READ_TIMEOUT_SECONDS = 3.0
_MAX_ATTEMPTS = 2
_TTL_BUFFER_SECONDS = 86_400  # 讓 DynamoDB TTL 最終回收這筆鎖 item

_PK = "__bedrock_rps_lock__"
_SK = "global"

# guard 必須涵蓋最壞的 narrative invoke（10s connect + 60s read）與取鎖
# 後的排程緩衝，否則 lease 可在真實 invoke 仍進行時過期。live Lambda
# contract 的整體 timeout 是 90s，因此 guard 用同一上界；release 正常時仍會
# 立即收旂到 invoke start + 1s。
_DEFAULT_HOLD_SECONDS = 90.0
# 等待鎖的上限。超過即 fail-closed 拒絕本次 invoke，不無限排隊吃掉 Lambda timeout。
_DEFAULT_CONTENTION_DEADLINE_SECONDS = 20.0
_MIN_POLL_INTERVAL_SECONDS = 0.10
_MAX_POLL_INTERVAL_SECONDS = 1.0
_MAX_ACQUIRE_ATTEMPTS = 25


class BedrockLockError(RuntimeError):
    """分散式 Bedrock 節流鎖的基底錯誤（一律 fail-closed）。"""


class BedrockLockBackendError(BedrockLockError):
    """DynamoDB 後端不可用（憑證/網路/throttle/表不存在），非「鎖被佔用」。"""


class BedrockLockContentionError(BedrockLockError):
    """在 contention deadline 內始終搶不到鎖。"""


class DynamoDBBedrockRpsLock:
    """跨 EC2 process、worker 與 Lambda 執行環境共享的 1 RPS 全域鎖。"""

    def __init__(
        self,
        table_name: str | None = None,
        region: str | None = None,
        *,
        min_interval: float = 1.0,
        hold_seconds: float = _DEFAULT_HOLD_SECONDS,
        contention_deadline_seconds: float = _DEFAULT_CONTENTION_DEADLINE_SECONDS,
        now: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.table_name = table_name or os.getenv(
            "TRUSTFORGE_BUDGET_COUNTER_TABLE", "trustforge-budget-guard"
        )
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        # 與 flock 路徑一致：環境變數不得把節流放寬到 1 秒以下。
        self.min_interval = max(1.0, float(min_interval))
        # guard 至少要 >= min_interval，否則 release 失敗時的封鎖比正常
        # cooldown 還短，等於變相放寬節流。
        self.hold_seconds = max(self.min_interval, float(hold_seconds))
        self.contention_deadline_seconds = max(0.0, float(contention_deadline_seconds))
        self._now = now
        self._monotonic = monotonic
        self._sleep = sleep
        self._jitter = jitter
        self._table: Any = None
        self._table_lock = threading.Lock()

    def _get_table(self) -> Any:
        if self._table is None:
            with self._table_lock:
                if self._table is None:
                    import boto3
                    from botocore.config import Config

                    config = Config(
                        connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                        read_timeout=_READ_TIMEOUT_SECONDS,
                        retries={"max_attempts": _MAX_ATTEMPTS, "mode": "standard"},
                    )
                    resource = boto3.resource("dynamodb", region_name=self.region, config=config)
                    self._table = resource.Table(self.table_name)
        return self._table

    @contextmanager
    def slot(self) -> Iterator[float]:
        """取得全域鎖，yield 本次 invoke 的起始 epoch 秒，離開時收斂 cooldown。

        用法固定為「包住真正的 Bedrock invoke」，不可只在呼叫前 acquire 後就
        放掉——cooldown 的基準點是 invoke **開始**時刻，鎖必須持有到那之後
        一秒為止，才能保證任兩次 invoke 的起始間隔 >= 1 秒。
        """
        owner = secrets.token_hex(16)
        self._acquire(owner)
        # Cooldown 必須以「DynamoDB 確認取鎖後」的真實 invoke 起點計算。
        # 不可重用 UpdateItem 前的時間，否則取鎖 latency 會吃掉 1s 間隔。
        invoke_start_monotonic = self._monotonic()
        invoke_start = self._now()
        _log.info(
            "[bedrock_rps_gate] acquired backend=dynamodb table=%s start_epoch=%.6f",
            self.table_name,
            invoke_start,
        )
        body_failed = False
        try:
            yield invoke_start
        except BaseException:
            body_failed = True
            raise
        finally:
            try:
                self._release(owner, invoke_start_monotonic)
            except BedrockLockError:
                # release 失敗 → `available_at` 仍停在 guard，後續呼叫者被擋住
                # （fail-closed 的實質保證已經成立）。body 本身已經炸掉時不要
                # 用這個次要錯誤蓋掉原始例外，只記 log。
                if body_failed:
                    _log.warning(
                        "[bedrock_rps_lock] release 失敗，鎖將等待 guard 過期", exc_info=True
                    )
                else:
                    raise

    def _acquire(self, owner: str) -> None:
        from boto3.dynamodb.conditions import Attr
        from botocore.exceptions import ClientError

        table = self._get_table()
        deadline = self._monotonic() + self.contention_deadline_seconds
        attempts = 0
        while attempts < _MAX_ACQUIRE_ATTEMPTS:
            attempts += 1
            now = self._now()
            guard_until = now + self.hold_seconds
            condition = Attr("available_at").not_exists() | Attr("available_at").lte(
                _decimal(now)
            )
            try:
                table.update_item(
                    Key={"source_id": _PK, "coin": _SK},
                    UpdateExpression=(
                        "SET lock_owner = :owner, available_at = :guard, "
                        "#ttl = :ttl_val"
                    ),
                    ConditionExpression=condition,
                    ExpressionAttributeNames={"#ttl": "ttl"},
                    ExpressionAttributeValues={
                        ":owner": owner,
                        ":guard": _decimal(guard_until),
                        ":ttl_val": int(now) + _TTL_BUFFER_SECONDS,
                    },
                )
                return
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code != "ConditionalCheckFailedException":
                    raise BedrockLockBackendError(
                        f"Bedrock RPS 鎖取得失敗（DynamoDB 後端錯誤）: {exc}"
                    ) from exc
            except Exception as exc:  # noqa: BLE001 — 任何後端異常一律 fail-closed
                raise BedrockLockBackendError(
                    f"Bedrock RPS 鎖取得失敗（DynamoDB 後端錯誤）: {exc}"
                ) from exc

            remaining = deadline - self._monotonic()
            if remaining <= 0 or attempts >= _MAX_ACQUIRE_ATTEMPTS:
                raise BedrockLockContentionError(
                    "Bedrock RPS 鎖競爭超過期限，本次呼叫 fail-closed 拒絕"
                )

            # 先讀取現任 holder 的 available_at，避免每 50ms 盲打一次
            # conditional write。讀取失敗也不能降級成無鎖路徑。
            try:
                item = table.get_item(
                    Key={"source_id": _PK, "coin": _SK}, ConsistentRead=True
                ).get("Item", {})
                available_at = float(item.get("available_at", now))
            except Exception as exc:  # noqa: BLE001
                raise BedrockLockBackendError(
                    f"Bedrock RPS 鎖等待失敗（DynamoDB 後端錯誤）: {exc}"
                ) from exc

            exponential = min(
                _MAX_POLL_INTERVAL_SECONDS,
                _MIN_POLL_INTERVAL_SECONDS * (2 ** min(attempts - 1, 4)),
            )
            until_available = max(0.0, available_at - self._now())
            base_wait = max(exponential, min(until_available, _MAX_POLL_INTERVAL_SECONDS))
            wait = min(remaining, self._jitter(base_wait * 0.8, base_wait))
            self._sleep(max(_MIN_POLL_INTERVAL_SECONDS, wait))

    def _release(self, owner: str, invoke_start_monotonic: float) -> None:
        from boto3.dynamodb.conditions import Attr
        from botocore.exceptions import ClientError

        # Client wall clocks are not a safe distributed cooldown boundary: a
        # faster next Lambda could satisfy ``available_at <= its_now`` early.
        # Keep ownership until this process's monotonic clock proves that the
        # real interval elapsed.  Loop because injected/platform sleeps may
        # return early; a lost process leaves the long guard in place.
        while True:
            remaining = self.min_interval - (
                self._monotonic() - invoke_start_monotonic
            )
            if remaining <= 0:
                break
            self._sleep(remaining)

        released_at = self._now()
        try:
            self._get_table().update_item(
                Key={"source_id": _PK, "coin": _SK},
                UpdateExpression="SET available_at = :next",
                ConditionExpression=Attr("lock_owner").eq(owner),
                ExpressionAttributeValues={":next": _decimal(released_at)},
            )
            _log.info(
                "[bedrock_rps_gate] released backend=dynamodb table=%s elapsed_monotonic=%.6f",
                self.table_name,
                self._monotonic() - invoke_start_monotonic,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                raise BedrockLockError(
                    "Bedrock RPS 鎖 release 時已不屬於本呼叫者（guard 可能已過期）"
                ) from exc
            raise BedrockLockBackendError(
                f"Bedrock RPS 鎖 release 失敗（DynamoDB 後端錯誤）: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 — release 失敗一律 fail-closed
            raise BedrockLockBackendError(
                f"Bedrock RPS 鎖 release 失敗（DynamoDB 後端錯誤）: {exc}"
            ) from exc


def _decimal(value: float) -> Decimal:
    """epoch 秒轉 DynamoDB Decimal，不可四捨五入而放寬 1 RPS。"""
    return Decimal(str(value))
