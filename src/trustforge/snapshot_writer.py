"""Snapshot 寫入工具模組。

從 fetch_scheduler.py 的 --snapshot 邏輯抽出可 import 的方法，
供 daemon（run_analysis_flow.py）和其他需要寫 trust snapshot 的地方使用。

Issue: #328
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .ingestion.cache import (
    CacheBackend,
    cache_key,
    cache_set_if_newer,
    get_cache_backend,
    trust_snapshot_history_key,
    snapshot_history_date,
    TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS,
    TRUST_SNAPSHOT_HISTORY_TTL_SECONDS,
    TRUST_SNAPSHOT_SOURCE,
)
from .pipeline import run as pipeline_run
from .schema import COIN_POOL, QuestionType, iso_utc

logger = logging.getLogger(__name__)

_SNAPSHOT_QUERY = "分析該幣種近期市場狀況，整合多源資料"


def _snapshot_dict(coin: str, report: Any, evidence: list | None = None) -> dict:
    """Report → 快照精華 dict（精簡版，不含 manip/reputation/authors）。"""
    snap = {
        "coin": coin,
        "trust_score": round(float(report.confidence), 4),
        "direction": report.direction,
        "calibrated_confidence": round(float(report.calibrated_confidence), 4),
        "decision_state": report.decision_state,
        "generated_at": report.generated_at,
    }
    return snap


def write_trust_snapshots(
    coins: list[str] | None = None,
    *,
    backend: CacheBackend | None = None,
    dry_run: bool = False,
) -> dict[str, str]:
    """對指定幣種各跑一次 real-off pipeline.run()，寫入 trust snapshot。

    回傳 {coin: "ok"|"skipped"|"failed:<reason>"} 的狀態摘要。

    這是從 fetch_scheduler.py run_snapshot() 精簡抽取出的 daemon 用版本：
    - 不含 source_snapshot 預寫（daemon 的 refresh_once 已經做了 create_snapshot）
    - 不寫 overview HTML blob（那是 fetch_scheduler 專屬的顯示層功能）
    - 只寫 history + latest 兩個 key
    """
    coins = coins or list(COIN_POOL)
    backend = backend or get_cache_backend()
    results: dict[str, str] = {}

    if dry_run:
        for coin in coins:
            results[coin] = "dry-run"
        return results

    run_now = time.time()

    for coin in coins:
        if coin not in COIN_POOL:
            results[coin] = "failed:invalid_coin"
            continue

        try:
            report, evidence, _log = pipeline_run(
                coin, _SNAPSHOT_QUERY, QuestionType.MULTI_SOURCE,
                data_mode="live", llm_mode="off",
                run_scope_id=f"snapshot-writer-{coin}-{time.time_ns()}",
            )
        except Exception as exc:
            logger.warning(
                "Snapshot write failed for %s: pipeline error: %s", coin, exc,
            )
            results[coin] = f"failed:{exc}"
            continue

        try:
            snap = _snapshot_dict(coin, report, evidence)
        except Exception as exc:
            logger.warning(
                "Snapshot write failed for %s: dict error: %s", coin, exc,
            )
            results[coin] = f"failed:{exc}"
            continue

        # 先寫 history（不可復原的 PIT 資料），再寫 latest（可自癒）
        history_result = cache_set_if_newer(
            backend,
            trust_snapshot_history_key(coin, snapshot_history_date(run_now)),
            [snap],
            fetched_at=run_now,
            ttl_seconds=TRUST_SNAPSHOT_HISTORY_TTL_SECONDS,
            allow_json_fallback=False,
        )
        if not history_result.ok:
            logger.warning(
                "Snapshot history write failed for %s: %s", coin, history_result.error,
            )
            results[coin] = f"failed:history_write:{history_result.error}"
            continue
        if history_result.skipped:
            results[coin] = "skipped"
            continue

        # Latest（全域 key，可自癒）
        latest_result = cache_set_if_newer(
            backend,
            cache_key(TRUST_SNAPSHOT_SOURCE, coin),
            [snap],
            fetched_at=run_now,
            ttl_seconds=TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS,
            allow_json_fallback=False,
        )
        if not latest_result.ok:
            logger.warning(
                "Snapshot latest write failed for %s: %s", coin, latest_result.error,
            )
            results[coin] = f"failed:latest_write:{latest_result.error}"
            continue

        results[coin] = "ok" if not latest_result.skipped else "ok:latest_skipped"

    return results
