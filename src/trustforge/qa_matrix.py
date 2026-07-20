"""QA mini matrix：5 幣 × 3 題型退化檢查（issue #203）。

在線上模式（無 --offline）下跑 15 組分析，檢查是否退化成 offline placeholder；
--offline 模式下以離線樣本資料測試整條管線可完整跑完。

產出 out/qa-matrix-latest.json，含每組成功/失敗/退化狀態與摘要統計。
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

from .pipeline import run, run_comparison
from .schema import COIN_POOL, QuestionType

# 固定 comparison pair：每幣配一個對照幣（避免 O(n²)）
_COMPARISON_PAIRS: dict[str, str] = {
    "BTC": "ETH",
    "ETH": "BTC",
    "SOL": "BTC",
    "BNB": "ETH",
    "XRP": "SOL",
}

_OFFLINE_MARKERS = ["[OFFLINE]", "[離線模式]", "[SAMPLE]"]

_DEFAULT_QUERIES: dict[str, str] = {
    "multi_source": "分析該幣種近兩週市場狀況，整合多源資料",
    "hypothesis": "假設該幣種將在下月上漲 20%，驗證此假設的多源證據",
    "comparison": "比較兩幣種的市場表現與信任度差異",
}


def _has_offline_marker(text: str) -> bool:
    """Check if the report text contains offline degradation markers."""
    upper = text.upper()
    for marker in _OFFLINE_MARKERS:
        if marker.upper() in upper:
            return True
    return False


def run_matrix(offline: bool = False, data_dir: str | None = None) -> dict:
    """Run the full 5×3 QA matrix and return results dict."""
    results: list[dict] = []
    coins = list(COIN_POOL)
    qtypes = [QuestionType.MULTI_SOURCE, QuestionType.HYPOTHESIS, QuestionType.COMPARISON]

    for coin in coins:
        for qtype in qtypes:
            entry: dict = {
                "coin": coin,
                "qtype": qtype.value,
                "status": "unknown",
                "degraded": False,
                "elapsed_sec": 0.0,
                "evidence_count": 0,
                "failed_sources": [],
                "error": None,
            }
            query = _DEFAULT_QUERIES[qtype.value]
            t0 = time.time()

            try:
                if qtype == QuestionType.COMPARISON:
                    coin_b = _COMPARISON_PAIRS[coin]
                    report_a, evidence_a, report_b, evidence_b, log = run_comparison(
                        coin, coin_b, query,
                        offline=offline, data_dir=data_dir,
                    )
                    # Check both reports for degradation
                    report_text = report_a.to_markdown(evidence_a) + report_b.to_markdown(evidence_b)
                    evidence_count = len(evidence_a) + len(evidence_b)
                    entry["coin_b"] = coin_b
                    # Collect limits from both reports
                    entry["limits"] = report_a.limits + report_b.limits
                else:
                    report, evidence, log = run(
                        coin, query, qtype,
                        offline=offline, data_dir=data_dir,
                    )
                    report_text = report.to_markdown(evidence)
                    evidence_count = len(evidence)
                    entry["limits"] = report.limits

                entry["elapsed_sec"] = round(time.time() - t0, 2)
                entry["evidence_count"] = evidence_count
                entry["degraded"] = _has_offline_marker(report_text)
                entry["status"] = "degraded" if entry["degraded"] else "pass"

            except Exception as exc:
                entry["elapsed_sec"] = round(time.time() - t0, 2)
                entry["status"] = "fail"
                entry["error"] = f"{type(exc).__name__}: {exc}"
                entry["traceback"] = traceback.format_exc()

            results.append(entry)
            # Progress output
            status_icon = {"pass": "✓", "degraded": "⚠", "fail": "✗"}.get(entry["status"], "?")
            pair_label = f" vs {entry.get('coin_b', '')}" if qtype == QuestionType.COMPARISON else ""
            print(f"  [{status_icon}] {coin}{pair_label} / {qtype.value}"
                  f"  ({entry['elapsed_sec']:.1f}s, {entry['evidence_count']} ev)")

    # Summary statistics
    passed = sum(1 for r in results if r["status"] == "pass")
    degraded = sum(1 for r in results if r["status"] == "degraded")
    failed = sum(1 for r in results if r["status"] == "fail")
    total_elapsed = sum(r["elapsed_sec"] for r in results)
    elapsed_list = sorted(r["elapsed_sec"] for r in results)
    p95_idx = min(len(elapsed_list) - 1, int(len(elapsed_list) * 0.95))
    p95_elapsed = elapsed_list[p95_idx] if elapsed_list else 0.0

    summary = {
        "total": len(results),
        "passed": passed,
        "degraded": degraded,
        "failed": failed,
        "total_elapsed_sec": round(total_elapsed, 2),
        "p95_elapsed_sec": round(p95_elapsed, 2),
        "offline_mode": offline,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    return {"summary": summary, "results": results}


def main(offline: bool = False, data_dir: str | None = None, out_dir: str = "out") -> int:
    """Entry point for the QA matrix."""
    print(f"TrustForge QA Matrix — {'OFFLINE' if offline else 'ONLINE'} mode")
    print(f"  5 coins × 3 types = 15 combinations\n")

    output = run_matrix(offline=offline, data_dir=data_dir)
    summary = output["summary"]

    # Write output
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    artifact = out_path / "qa-matrix-latest.json"
    artifact.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    # Print summary
    print(f"\n{'=' * 50}")
    print(f"  結果：{summary['passed']} 通過 / {summary['degraded']} 退化 / {summary['failed']} 失敗")
    print(f"  P95 耗時：{summary['p95_elapsed_sec']:.1f}s")
    print(f"  總耗時：{summary['total_elapsed_sec']:.1f}s")
    print(f"  產出：{artifact}")
    print(f"{'=' * 50}")

    # Return non-zero if any failures
    if summary["failed"] > 0:
        return 1
    if not offline and summary["degraded"] > 0:
        # In online mode, degradation is a warning but not a hard failure
        return 0
    return 0
