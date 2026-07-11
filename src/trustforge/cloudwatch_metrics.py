"""#104：dedup fail-open 頻率告警 — CloudWatch 數值監控 emitter。

背景（承接 #93/#102 的 dedup fail-open 頻率告警）：`web._record_dedup_prep_
failure` 目前只在「頻率達門檻」時升級成 `ERROR` 級 ALERT log（可 grep 前綴
`"ALERT: TrustForge dedup"` 建 log-based 告警）。但純 log 告警有盲點：若監控
端沒建 log 過濾規則、或 ALERT log 被採樣/遺失，重複計費/去重失效就沒人看見。

本模組把同一份「滑動視窗內的 dedup 準備失敗次數」（`recent_failures`）以
**數值 CloudWatch 自定義指標** `DedupFailOpenRecentFailures` 送出，讓維運直接
對這個數值建 CloudWatch Alarm（見 `deploy/put_dedup_alarm.sh`），而不依賴
log 解析——重複計費/去重失效變成「即時可見的線圖 + 閾值報警」。

設計原則（與 `ssm_params.py` / `rate_limit_store.py` 一致）：
- **opt-in + lazy**：模組頂層不 import boto3、不建 client。只有在 env
  `TRUSTFORGE_CW_METRICS=dynamodb`（或任意 truthy）時，`emit_*` 才真的發
  `put_metric_data`；未設定時 `emit_*` 是 no-op（零 AWS 呼叫、零依賴），完全
  不影響離線 demo 與既有 log 告警路徑。
- **fail-closed / 永不 raise**：指標上報是「觀測性旁路」，任何失敗（網路/
  憑證/throttle/Alarm 不存在）只記 warning log、絕不往上拋——不能讓「指標
  上報失敗」反過來打掛分析請求或 dedup 準備邏輯。
- **絕不記錄 token / 參數值**：送出的只有計數與維度（Service=trustforge），
  不含任何 request 內容或 token。
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "TrustForge"
METRIC_NAME = "DedupFailOpenRecentFailures"
_TRUTHY = frozenset({"1", "true", "yes", "on"})

_client: Any = None
_client_lock = threading.Lock()


def metrics_enabled() -> bool:
    """是否啟用 CloudWatch 指標上報（env `TRUSTFORGE_CW_METRICS` 為 truthy）。"""
    return os.getenv("TRUSTFORGE_CW_METRICS", "").strip().lower() in _TRUTHY


def _get_or_create_client() -> Any:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        import boto3
        from botocore.config import Config

        region = os.getenv("AWS_REGION", "us-east-1")
        config = Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"mode": "standard", "max_attempts": 2},
        )
        _client = boto3.client("cloudwatch", region_name=region, config=config)
        return _client


def set_client_for_tests(client: Any) -> None:
    """測試輔助：注入 mock CloudWatch client（傳 None 重置）。"""
    global _client
    with _client_lock:
        _client = client


def emit_dedup_fail_open_metric(
    recent_failures: int,
    *,
    namespace: str | None = None,
    now: datetime | None = None,
) -> bool:
    """把 dedup 準備失敗滑動視窗內的次數 `recent_failures` 作為數值指標送出。

    回傳：
        True  — 已送出（或 metrics 未啟用時視同成功 no-op）。
        False — 上報失敗（已記 warning，呼叫端無須處理）。

    失敗絕不 raise（觀測性旁路）。未啟用（`metrics_enabled()` 為 False）時
    直接 no-op 回 True。
    """
    if not metrics_enabled():
        return True
    if recent_failures is None or not isinstance(recent_failures, int) or recent_failures < 0:
        recent_failures = 0

    ts = now if now is not None else datetime.now(timezone.utc)
    metric_data = [
        {
            "MetricName": METRIC_NAME,
            "Dimensions": [{"Name": "Service", "Value": "trustforge"}],
            "Timestamp": ts,
            "Value": recent_failures,
            "Unit": "Count",
        }
    ]
    try:
        _get_or_create_client().put_metric_data(
            Namespace=namespace or DEFAULT_NAMESPACE,
            MetricData=metric_data,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[cloudwatch_metrics] 送出 dedup fail-open 指標失敗（觀測性旁路，不影響請求）: %s",
            exc,
            exc_info=True,
        )
        return False
