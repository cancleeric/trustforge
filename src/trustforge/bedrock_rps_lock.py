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
        UpdateItem SET available_at=:invoke_start_plus_interval
        ConditionExpression: lock_owner = :owner
      把 `available_at` **收斂**回「invoke 起始時刻 + 1 秒」——這正是規範要求的
      「cooldown 持續到 invoke 開始後一秒」。條件綁 owner，確保不會把別人
      （guard 過期後接手的下一位）的鎖誤推遲或誤釋放。

fail-closed 三條路（規範要求）：
    1. DynamoDB 操作失敗（憑證/網路/throttle/表不存在）→ `BedrockLockBackendError`，
       呼叫端不得改走離線或無鎖路徑。
    2. 競爭等待超過 `contention_deadline_seconds` → `BedrockLockContentionError`。
    3. release 失敗（條件不成立或後端錯誤）→ raise；此時 `available_at` 仍停在
       guard，後續呼叫者被擋到 guard 過期為止，節流不會被放寬。

非 Lambda 部署完全不走本模組，`bedrock.BedrockRpsLimiter` 的 flock 行為原封不動。
"""
from __future__ import annotations

import logging
import os
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

# guard 必須涵蓋一次真實 invoke 的最長時間（live contract 的 narrative read
# timeout 是 20s），否則 release 還沒跑到 guard 就過期，兩個呼叫者會同時認為
# 自己持有鎖。取 25s 留 5s 緩衝；同時它也是 release 失敗時的最長封鎖時間，
# 遠小於 Lambda 的 90s timeout，不會讓 live demo 永久卡死。
_DEFAULT_HOLD_SECONDS = 25.0
# 等待鎖的上限。超過即 fail-closed 拒絕本次 invoke，不無限排隊吃掉 Lambda timeout。
_DEFAULT_CONTENTION_DEADLINE_SECONDS = 20.0
_POLL_INTERVAL_SECONDS = 0.05


class BedrockLockError(RuntimeError):
    """分散式 Bedrock 節流鎖的基底錯誤（一律 fail-closed）。"""


class BedrockLockBackendError(BedrockLockError):
    """DynamoDB 後端不可用（憑證/網路/throttle/表不存在），非「鎖被佔用」。"""


class BedrockLockContentionError(BedrockLockError):
    """在 contention deadline 內始終搶不到鎖。"""


class DynamoDBBedrockRpsLock:
    """跨 Lambda 執行環境共享的 Bedrock 1 RPS 全域鎖。"""

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
        invoke_start = self._acquire(owner)
        body_failed = False
        try:
            yield invoke_start
        except BaseException:
            body_failed = True
            raise
        finally:
            try:
                self._release(owner, invoke_start)
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

    def _acquire(self, owner: str) -> float:
        from boto3.dynamodb.conditions import Attr
        from botocore.exceptions import ClientError

        table = self._get_table()
        deadline = self._monotonic() + self.contention_deadline_seconds
        while True:
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
                return now
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

            if self._monotonic() >= deadline:
                raise BedrockLockContentionError(
                    "Bedrock RPS 鎖競爭超過期限，本次呼叫 fail-closed 拒絕"
                )
            self._sleep(_POLL_INTERVAL_SECONDS)

    def _release(self, owner: str, invoke_start: float) -> None:
        from boto3.dynamodb.conditions import Attr
        from botocore.exceptions import ClientError

        next_available = invoke_start + self.min_interval
        try:
            self._get_table().update_item(
                Key={"source_id": _PK, "coin": _SK},
                UpdateExpression="SET available_at = :next",
                ConditionExpression=Attr("lock_owner").eq(owner),
                ExpressionAttributeValues={":next": _decimal(next_available)},
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
    """epoch 秒轉 DynamoDB 可存的 Decimal（毫秒精度即足夠，避免浮點尾數爆長）。"""
    return Decimal(f"{value:.3f}")
