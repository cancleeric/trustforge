"""DynamoDB 為底的跨行程/跨實例共享限流計數器（issue #1 CISO High 修復）。

修復「Lambda 多實例部署下 process 記憶體 dict 限流失效」問題：同一個來源 IP
可能打到不同的 Lambda 執行環境，每個環境各自用 process-local 的 dict 計數，
導致總量遠超限制（例如限制 5 req/min，部署 5 個並發實例就可能放大到 25）。

本模組改用「固定視窗（fixed window）」計數：把 `now` 依 `window_seconds` 落到
某個 `window_start`（`int(now // window_seconds) * window_seconds`），同一個
視窗內所有執行個體都對同一個 DynamoDB item 做原子條件式遞增（UpdateItem 的 ADD
搭配 ConditionExpression）。DynamoDB 對單一 item 的寫入本身是序列化的，不會
出現「兩個實例各自讀到舊值再各自 +1」的 race condition（比照 `admin_config.py`
`put_config()` 的 CAS 慣例、`ingestion/cache.py` `DynamoDBCache.set_if_newer()`
的條件式寫入慣例）。

沿用既有 `trustforge-connector-cache` 表（PK=`source_id`、SK=`coin`）的保留字
慣例，不是新建表、也不需要任何 IAM/schema 變更：
    source_id = f"__ratelimit_{bucket}__"
    coin      = f"{key}:{window_start}"

當 DynamoDB 讀寫失敗（憑證/網路/throttle 等真的連不上或操作失敗，**不是**
「條件不成立」）時 raise `RateLimitBackendError`，呼叫端（`web.py`
`_check_live_rate_limit`）據此 fallback 回 process-local 限流，避免整個限流
機制 fail-open（單機/DynamoDB 不可用時退化為「至少單機內仍會擋」，不是「完全
不擋」）。

⚠️ vp-eng review LOW（accepted trade-off，非 bug）：本模組的主路徑採用
DynamoDB 固定視窗計數，其語意與 fallback 路徑的 process-local 滑動視窗
不盡相同——固定視窗在邊界附近可能允許短時間內出現接近 2 倍 `max_requests`
的突發流量，滑動視窗則無此邊界效應。這是刻意接受的取捨：以固定視窗換取
「單一 DynamoDB item 上原子條件式遞增」的簡單性，真正的跨行程滑動視窗
需要更複雜的分散式資料結構，超出本次修復範圍。兩條路徑的限流嚴格度本不
必逐字相同，只要核心不變量「限流效果不會被多實例部署放大」成立即可。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

_log = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 3.0
_READ_TIMEOUT_SECONDS = 3.0
_MAX_ATTEMPTS = 2
_UNKNOWN_KEY_SENTINEL = "unknown"  # DynamoDB SK 不可為空字串，key 為空時用此代替
_TTL_BUFFER_SECONDS = 60  # 視窗結束後多留 60 秒才讓 DynamoDB TTL 回收，避免邊界誤刪


class RateLimitBackendError(Exception):
    """DynamoDB 限流計數器不可用（非「已達限額」）時拋出。

    代表底層後端真的連不上或操作失敗（憑證、網路、throttle 等），呼叫端
    應 fallback 回 process-local 限流，不要讓限流整個 fail-open。
    """


class RateLimitStore:
    """跨實例共享的固定視窗限流計數器（DynamoDB 後端）。"""

    def __init__(self, table_name: str | None = None, region: str | None = None) -> None:
        self.table_name = table_name or os.getenv("TRUSTFORGE_CACHE_TABLE", "trustforge-connector-cache")
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

    def try_increment(
        self,
        bucket: str,
        key: str,
        window_seconds: int,
        max_requests: int,
        *,
        now: float | None = None,
    ) -> bool:
        """原子地把這次請求計入固定視窗計數器。

        回傳值語意：
            True  — 這次請求已被原子計入且未超過 `max_requests`，應放行。
            False — 這個視窗已達 `max_requests`，應拒絕；這次請求**沒有**被計入，
                    計數器不會無界增長。
            raise `RateLimitBackendError` — DynamoDB 操作本身失敗（非條件不成立），
                    呼叫端應 fallback 回 process-local 限流。
        """
        # vp-eng review LOW：max_requests<=0 是 kill-switch 語意，
        # not_exists() 分支不能讓它的第一次請求恆真放行，故前置直接擋下。
        if max_requests <= 0:
            return False

        resolved_now = now if now is not None else time.time()
        window_start = int(resolved_now // window_seconds) * window_seconds
        norm_key = key if key else _UNKNOWN_KEY_SENTINEL
        source_id = f"__ratelimit_{bucket}__"
        coin = f"{norm_key}:{window_start}"
        ttl_epoch = window_start + int(window_seconds) + _TTL_BUFFER_SECONDS

        from boto3.dynamodb.conditions import Attr
        from botocore.exceptions import ClientError

        condition = Attr("request_count").not_exists() | Attr("request_count").lt(max_requests)

        try:
            self._get_table().update_item(
                Key={"source_id": source_id, "coin": coin},
                UpdateExpression="ADD request_count :incr SET #ttl = if_not_exists(#ttl, :ttl_val)",
                ConditionExpression=condition,
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={":incr": 1, ":ttl_val": ttl_epoch},
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise RateLimitBackendError(f"rate limit store 更新失敗: {exc}") from exc
        except Exception as exc:
            raise RateLimitBackendError(f"rate limit store 更新失敗: {exc}") from exc


_default_store_lock = threading.Lock()
_default_store_instance: RateLimitStore | None = None


def _default_store() -> RateLimitStore:
    global _default_store_instance
    if _default_store_instance is None:
        with _default_store_lock:
            if _default_store_instance is None:
                _default_store_instance = RateLimitStore()
    return _default_store_instance


def try_increment(
    bucket: str,
    key: str,
    window_seconds: int,
    max_requests: int,
    *,
    store: RateLimitStore | None = None,
    now: float | None = None,
) -> bool:
    """對外入口：把這次請求嘗試計入固定視窗限流計數器。

    `store` 未傳時使用模組級預設 singleton。回傳值語意同
    `RateLimitStore.try_increment`：`True` 放行、`False` 已達限額、
    raise `RateLimitBackendError` 代表後端不可用應 fallback。
    """
    resolved = store if store is not None else _default_store()
    return resolved.try_increment(bucket, key, window_seconds, max_requests, now=now)
