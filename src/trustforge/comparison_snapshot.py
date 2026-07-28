"""DB-free comparison snapshot synthesis — CA-09.

從既有的 `out/snapshots/{coin}.json` 讀取 A/B snapshot，當兩者皆存在時，
用 deterministic fallback（不呼叫 Bedrock）產出 ComparisonReport。

不修改 DB schema、不呼叫 Bedrock、向後相容。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .comparison_contract import (
    ComparisonReport,
    _report_from_dict,
    build_comparison_report,
)
from .schema import Evidence

logger = logging.getLogger(__name__)

DEFAULT_SNAPSHOT_DIR = Path("out/snapshots")


def synthesize_comparison_from_snapshots(
    coin_a: str,
    coin_b: str,
    query: str,
    *,
    snapshot_dir: Path | None = None,
) -> ComparisonReport | None:
    """從 A/B snapshot 合成 ComparisonReport（純規則層，無 LLM/Bedrock）。

    讀取 `{snapshot_dir}/{coin_a}.json` 和 `{snapshot_dir}/{coin_b}.json`，
    任一缺失即回傳 None。兩者皆存在時，用 `build_comparison_report()`
    產出結構化比較報告。

    Args:
        coin_a: A 幣（如 "BTC"）
        coin_b: B 幣（如 "ETH"）
        query: 比較問題
        snapshot_dir: snapshot 目錄，預設 `out/snapshots/`

    Returns:
        ComparisonReport | None: 比較報告；任一 snapshot 缺失時回傳 None
    """
    snap_dir = snapshot_dir or DEFAULT_SNAPSHOT_DIR

    path_a = snap_dir / f"{coin_a}.json"
    path_b = snap_dir / f"{coin_b}.json"

    missing: list[str] = []
    if not path_a.exists():
        missing.append(str(path_a))
    if not path_b.exists():
        missing.append(str(path_b))
    if missing:
        logger.info(
            "Snapshot synthesis skipped: missing %s/%s snapshots — %s",
            coin_a, coin_b, ", ".join(missing),
        )
        return None

    try:
        snap_a = json.loads(path_a.read_text())
        snap_b = json.loads(path_b.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "Snapshot synthesis failed for %s vs %s: %s", coin_a, coin_b, exc,
        )
        return None

    try:
        report_a = _report_from_dict(snap_a["report"])
        report_b = _report_from_dict(snap_b["report"])
        evidence_a = [Evidence(**e) for e in snap_a["evidence"]]
        evidence_b = [Evidence(**e) for e in snap_b["evidence"]]
    except (KeyError, TypeError) as exc:
        logger.warning(
            "Snapshot synthesis failed for %s vs %s: malformed snapshot — %s",
            coin_a, coin_b, exc,
        )
        return None

    return build_comparison_report(
        coin_a=coin_a,
        coin_b=coin_b,
        query=query,
        report_a=report_a,
        report_b=report_b,
        evidence_a=evidence_a,
        evidence_b=evidence_b,
    )
