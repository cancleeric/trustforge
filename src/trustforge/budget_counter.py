"""#75：多實例安全的 budget 預留原子計數器（DynamoDB conditional counter）。

背景（承接 #1 的 DynamoDB 限流模式）：`budget_guard.BudgetReservation` 是
process-local 的 `threading.Lock` 計數器——單一 process 部署下足以擋住同
process 內多執行緒的並行 TOCTOU（codex HIGH，已修），但**多實例部署**
（多 process／多機器，例如 App Runner 自動擴縮、或多台 EC2 前掛 ALB）時，
各 process 各自的 `reserved` 互不可見，退化回原本的 race：`N` 個實例同時
看到「今日已花費=0」（彼此都還沒寫回），各自放行，每日 `$3` 硬上限被
並行撐爆成 `N` 倍。

本模組用 **DynamoDB 單一共享 item 的原子條件式遞增** 取代 process-local
計數：所有實例都對同一個 PK=`__budget_reserved__`、SK=`<UTC日期>` 的 item
做 `UpdateItem`，搭配 `ConditionExpression` 在「寫入當下」對該 item 的
`reserved_total` 做原子判斷——DynamoDB 對單一 item 的寫入本身被序列化，
不會出現「兩個實例各自讀到舊值再各自 +cost」的 race（比照
`rate_limit_store.py` `try_increment` 的 conditional-write 慣例、
`admin_config.py` `put_config()` 的 CAS 慣例）。

計數語意：
    `reserved_total` 是所有實例「目前正在飛行中、已預留但尚未 reconcile」
    的保守成本上界總和。判斷式（在 DynamoDB 端原子評估）：
        spent_daily + reserved_total + cost <= cap
    其中 `spent_daily` 由呼叫端讀帳本傳入（已完成的 run，跨實例共享來源），
    `cost` 是這次請求的保守上界，`reserved_total` 是 item 當下值。

    因 DynamoDB `ConditionExpression` **不支援**對屬性做四則運算
    （不能寫 `reserved_total + :cost <= :cap`），改為把「剩餘空間」
    `max_allowed = cap - spent_daily - cost` 在呼叫端算成常數值傳入，條件寫
    `attribute_not_exists(reserved_total) OR reserved_total <= :max_allowed`。
    - 首次（item 不存在）→ `attribute_not_exists` 成立 → 放行並建立 item。
    - 後續 → 只有「目前 reserved_total 還沒超過剩餘空間」才放行；超了就
      `ConditionalCheckFailedException` → 回 `False`（拒絕，呼叫端 degrade）。

後端不可用（憑證/網路/throttle/表不存在）時 raise `BudgetBackendError`，
呼叫端（`budget_guard.try_reserve_request_budget`/`release_request_budget`）
據此 fallback 回 process-local `BudgetReservation`，不讓預留整個 fail-open
（至少單 process 內仍會擋）。

沿用既有 `trustforge-connector-cache` 表的保留字慣例不適用（那是 source_id/
coin schema），這裡用獨立表（預設 `trustforge-budget-guard`，可用
`TRUSTFORGE_BUDGET_COUNTER_TABLE` 覆寫）+ TTL 自動回收過期日期的 item。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

_log = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 3.0
_READ_TIMEOUT_SECONDS = 3.0
_MAX_ATTEMPTS = 2
_TTL_BUFFER_SECONDS = 86_400  # 日期結束後多留一天才讓 DynamoDB TTL 回收
_PK = "__budget_reserved__"


class BudgetBackendError(Exception):
    """DynamoDB budget 計數器不可用（非「已達上限」）時拋出。

    代表底層後端真的連不上或操作失敗（憑證、網路、throttle、表不存在等），
    呼叫端應 fallback 回 process-local 預留，不要讓預留整個 fail-open。
    """


def _day_key(now: float | None = None) -> str:
    resolved = now if now is not None else time.time()
    return datetime.fromtimestamp(resolved, tz=timezone.utc).date().isoformat()


class DynamoDBBudgetCounter:
    """跨實例共享的原子 budget 預留計數器（DynamoDB 後端）。"""

    def __init__(self, table_name: str | None = None, region: str | None = None) -> None:
        self.table_name = table_name or os.getenv(
            "TRUSTFORGE_BUDGET_COUNTER_TABLE", "trustforge-budget-guard"
        )
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
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

    def try_reserve(
        self,
        *,
        spent_daily: float,
        cost: float,
        cap: float,
        now: float | None = None,
    ) -> bool:
        """原子地嘗試預留 `cost`（USD）這次請求的保守成本上界。

        回傳值語意：
            True  — 預留成功，應放行這次請求（已原子疊加到共享
                    `reserved_total`）。
            False — 剩餘空間不足以容納這次預留，應拒絕（**不**改動計數器）。
            raise `BudgetBackendError` — DynamoDB 操作本身失敗（非條件不成立），
                     呼叫端應 fallback 回 process-local 預留。
        """
        if cap <= 0:
            return False
        if not (cost > 0):
            # 非正成本（壞值/NaN）不預留——fail-closed 視為「沒有可用的預留
            # 空間」會直接擋住整個分析路徑，過於激進；這裡選擇「不預留、照常
            # 放行」但也不動計數器（與 process-local 路徑對 cost<=0 的處理
            # 語意一致：process-local 路徑對 cost<=0 也會因
            # `round(spent+reserved+cost,6) > cap` 在 cost<=0 時不會誤超額）。
            # 但為保守，cost<=0 視同「這次請求不佔用預算」，直接放行且不記。
            return True

        max_allowed = cap - spent_daily - cost
        # 剩餘空間本身已經 < 0（不含這次）→ 直接拒絕，不發 DynamoDB 呼叫
        # （省一次確定會失敗的寫入）。等於 0 仍放行（這次預留正好填滿 cap）。
        if max_allowed < 0:
            return False

        from boto3.dynamodb.conditions import Attr
        from botocore.exceptions import ClientError

        day = _day_key(now)
        ttl_epoch = int((now or time.time())) + _TTL_BUFFER_SECONDS
        condition = (
            Attr("reserved_total").not_exists()
            | Attr("reserved_total").lte(Decimal(str(round(max_allowed, 6))))
        )
        try:
            self._get_table().update_item(
                Key={"source_id": _PK, "coin": day},
                UpdateExpression=(
                    "SET reserved_total = if_not_exists(reserved_total, :zero) + :cost, "
                    "#ttl = if_not_exists(#ttl, :ttl_val)"
                ),
                ConditionExpression=condition,
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":cost": Decimal(str(cost)),
                    ":zero": Decimal("0"),
                    ":ttl_val": ttl_epoch,
                },
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise BudgetBackendError(f"budget counter 預留失敗: {exc}") from exc
        except Exception as exc:
            raise BudgetBackendError(f"budget counter 預留失敗: {exc}") from exc

    def release(self, amount: float, *, now: float | None = None) -> None:
        """釋放先前 `try_reserve()` 成功預留的 `amount`（USD）。

        原子地把共享 `reserved_total` 減去 `amount`，並在「不足以扣」時 clamp
        回 0（避免浮點/部分失敗導致負值——負值會讓下一輪條件 `reserved_total
        <= max_allowed` 永遠成立、悄悄 over-admit）。後端不可用只記 warning、
        不 raise（reconcile 失敗不該讓 pipeline 炸）。`now` 必須與對應的
        `try_reserve` 同屬一個 UTC 日期（預設用當下時間，production 中
        reserve/release 在一日內發生，天然一致）。
        """
        if amount is None or not (amount > 0):
            return
        from boto3.dynamodb.conditions import Attr
        from botocore.exceptions import ClientError

        day = _day_key(now)
        try:
            self._get_table().update_item(
                Key={"source_id": _PK, "coin": day},
                # DynamoDB `ADD` 對數值型態做代數相加：傳負值即等於減去 `amount`
                # （moto 不支援 `SET path = path - :amount` 的減法語法，故統一用
                # `ADD`；真人 DynamoDB 兩者都支援，這裡取 moto 相容的寫法）。
                UpdateExpression="ADD reserved_total :neg_amount",
                ConditionExpression=Attr("reserved_total").gte(Decimal(str(amount))),
                ExpressionAttributeValues={":neg_amount": Decimal(str(-amount))},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                # reserved_total 已經不足 amount（極可能是浮點部分失敗/重複
                # release），clamp 回 0 確保不會留下負值污染下一輪判斷。
                try:
                    self._get_table().update_item(
                        Key={"source_id": _PK, "coin": day},
                        UpdateExpression="SET reserved_total = :zero",
                        ExpressionAttributeValues={":zero": Decimal("0")},
                    )
                except Exception:
                    _log.warning("[budget_counter] release clamp 失敗", exc_info=True)
                return
            _log.warning("[budget_counter] release 失敗：%s", exc, exc_info=True)
        except Exception as exc:
            _log.warning("[budget_counter] release 失敗：%s", exc, exc_info=True)

    def current_reserved(self, now: float | None = None) -> float:
        """唯讀查詢目前共享 `reserved_total`（USD）。後端不可用時回 0.0
        （保守：把「看不到預留」當作「沒有預留」，讓呼叫端多放行一點點，
        但不會 over-admit——因為多放行只是多佔用 process-local 的寬鬆空間，
         真正原子判斷仍發生在後端恢復後的下一次 try_reserve）。主要供
         `/api/status` 觀測與測試斷言。"""
        try:
            resp = self._get_table().get_item(
                Key={"source_id": _PK, "coin": _day_key(now)}
            )
            item = resp.get("Item")
            if not item:
                return 0.0
            val = item.get("reserved_total")
            if val is None:
                return 0.0
            return float(val)
        except Exception as exc:
            _log.warning("[budget_counter] 讀取 reserved_total 失敗：%s", exc, exc_info=True)
            return 0.0


_default_counter_lock = threading.Lock()
_default_counter_instance: "DynamoDBBudgetCounter | None" = None


def _default_counter() -> "DynamoDBBudgetCounter":
    global _default_counter_instance
    if _default_counter_instance is None:
        with _default_counter_lock:
            if _default_counter_instance is None:
                _default_counter_instance = DynamoDBBudgetCounter()
    return _default_counter_instance


def set_default_counter_for_tests(counter: "DynamoDBBudgetCounter | None") -> None:
    """測試輔助：注入/重置預設 counter 單例。"""
    global _default_counter_instance
    with _default_counter_lock:
        _default_counter_instance = counter
