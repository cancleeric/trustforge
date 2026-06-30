#!/usr/bin/env python3
"""P0-5 E2E 壓測 harness — 5 幣 × 3 題型 + 失敗降級驗證。

用法:
    python3 scripts/stress_test.py                 # offline 模式（預設）
    python3 scripts/stress_test.py --offline       # 同上，驗正確性/降級，instant
    python3 scripts/stress_test.py --online        # 走真實 Bedrock+連接器，量真時間
                                                   # 需 env: BEDROCK_MODEL_ID, AWS_REGION
                                                   # 沒有就跳過 online 並註明

目標：
  - 5 幣(BTC/ETH/SOL/BNB/XRP) × 3 題型矩陣
  - offline 模式：立即跑，驗正確性與降級行為（elapsed 為 pipeline 內部計時，非真實 Bedrock）
  - online 模式：走真實 Bedrock，量真時間；無 env 時自動跳過並在報告中註明
  - 輸出 docs/STRESS-TEST.md

純 stdlib + trustforge 本身（無額外依賴）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# ── 路徑設定 ─────────────────────────────────────────────────────────────────
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from trustforge.ingestion.base import (
    Document,
    OfflineSampleSource,
    Source,
    SOURCE_KINDS,
    collect,
)
from trustforge.pipeline import run, run_comparison
from trustforge.schema import QuestionType, comparison_to_markdown

# ── 常數 ─────────────────────────────────────────────────────────────────────
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP"]

# comparison 用固定配對，讓 5 幣都出現至少一次
COMPARISON_PAIRS: list[tuple[str, str]] = [
    ("BTC", "ETH"),
    ("ETH", "SOL"),
    ("SOL", "BNB"),
    ("BNB", "XRP"),
    ("XRP", "BTC"),
]

BUDGET_SEC = 900          # 15 分鐘官方上限
SINGLE_EV_THRESHOLD = 8   # DEV-PLAN P0-5：multi_source/hypothesis evidence ≥ 8
COMP_EV_THRESHOLD = 12    # DEV-PLAN P0-5：comparison evidence ≥ 12


# ── 工具函式 ──────────────────────────────────────────────────────────────────

def _write_deliverables(out_dir: Path, result_data: tuple) -> None:
    """將 run()/run_comparison() 結果寫入 3 個交付件文件。"""
    if len(result_data) == 3:
        report, evidence, log = result_data
        (out_dir / "report.md").write_text(
            report.to_markdown(evidence), encoding="utf-8"
        )
        (out_dir / "evidence.json").write_text(
            json.dumps([e.to_dict() for e in evidence], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out_dir / "execution_log.jsonl").write_text(
            log.to_jsonl(), encoding="utf-8"
        )
    else:
        report_a, evidence_a, report_b, evidence_b, log = result_data
        md = comparison_to_markdown(report_a, evidence_a, report_b, evidence_b, "壓測")
        (out_dir / "report.md").write_text(md, encoding="utf-8")
        all_ev = (
            [{**e.to_dict(), "coin": report_a.coin} for e in evidence_a]
            + [{**e.to_dict(), "coin": report_b.coin} for e in evidence_b]
        )
        (out_dir / "evidence.json").write_text(
            json.dumps(all_ev, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "execution_log.jsonl").write_text(
            log.to_jsonl(), encoding="utf-8"
        )


def _check_deliverables(out_dir: Path) -> tuple[bool, list[str]]:
    """檢查 3 個文件交付件（第 4 件為 code repo，永遠存在）。"""
    required = ["report.md", "evidence.json", "execution_log.jsonl"]
    missing = [f for f in required if not (out_dir / f).exists()]
    return len(missing) == 0, missing


def run_single_case(
    label: str, coin: str, qtype: str, query: str, offline: bool
) -> dict:
    """跑單幣 multi_source / hypothesis，回傳結果 dict。"""
    out_dir = Path(tempfile.mkdtemp(prefix="tf_stress_"))
    result: dict = {
        "label": label,
        "coin": coin,
        "qtype": qtype,
        "elapsed": None,
        "evidence_count": 0,
        "deliverables_ok": False,
        "missing": [],
        "exception": None,
        "pass": False,
    }
    try:
        data = run(coin, query, QuestionType(qtype), offline=offline)
        report, evidence, log = data
        _write_deliverables(out_dir, data)

        ok, missing = _check_deliverables(out_dir)
        elapsed = log.elapsed()

        result["elapsed"] = round(elapsed, 3)
        result["evidence_count"] = len(evidence)
        result["deliverables_ok"] = ok
        result["missing"] = missing
        result["pass"] = ok and len(evidence) >= SINGLE_EV_THRESHOLD and elapsed < BUDGET_SEC

    except Exception as exc:
        result["elapsed"] = 0.0
        result["exception"] = repr(exc)
        result["pass"] = False
    finally:
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)
    return result


def run_comparison_case(
    label: str, coin_a: str, coin_b: str, query: str, offline: bool
) -> dict:
    """跑比較分析案例，回傳結果 dict。"""
    out_dir = Path(tempfile.mkdtemp(prefix="tf_stress_"))
    result: dict = {
        "label": label,
        "coin": f"{coin_a}/{coin_b}",
        "qtype": "comparison",
        "elapsed": None,
        "evidence_count": 0,
        "deliverables_ok": False,
        "missing": [],
        "exception": None,
        "pass": False,
    }
    try:
        data = run_comparison(coin_a, coin_b, query, offline=offline)
        report_a, evidence_a, report_b, evidence_b, log = data
        _write_deliverables(out_dir, data)

        ok, missing = _check_deliverables(out_dir)
        total_ev = len(evidence_a) + len(evidence_b)
        elapsed = log.elapsed()

        result["elapsed"] = round(elapsed, 3)
        result["evidence_count"] = total_ev
        result["deliverables_ok"] = ok
        result["missing"] = missing
        result["pass"] = ok and total_ev >= COMP_EV_THRESHOLD and elapsed < BUDGET_SEC

    except Exception as exc:
        result["elapsed"] = 0.0
        result["exception"] = repr(exc)
        result["pass"] = False
    finally:
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)
    return result


# ── 失敗降級驗證 ─────────────────────────────────────────────────────────────

class _FailingSource(Source):
    """模擬逾時/失敗的來源，用於降級驗證。"""
    kind = "news"
    name = "failing-news-src"

    def fetch(self, query: str, coin: str = "") -> list[Document]:  # noqa: ARG002
        raise TimeoutError("模擬來源逾時（壓測降級驗證）")


def run_degradation_tests(offline: bool) -> list[dict]:
    """失敗降級驗證：來源逾時 + Bedrock 離線。"""
    results = []

    # ── 驗證 1：來源逾時 → collect 跳過 → pipeline 不崩 → limits 有記錄 ────────
    degrade1: dict = {
        "case": "source_timeout_degradation",
        "pass": False,
        "note": "",
        "docs_from_working": 0,
        "failed_in_limits": False,
    }
    try:
        working_sources = [OfflineSampleSource(k, k) for k in SOURCE_KINDS]
        sources_with_fail = [_FailingSource()] + working_sources

        # 直接呼叫 collect，確認 FailingSource 被跳過
        failed_names: list[str] = []
        docs = collect(
            "分析 BTC 壓測降級",
            coin="BTC",
            sources=sources_with_fail,
            offline=True,
            _failed=failed_names,
        )

        assert "failing-news-src" in failed_names, "collect 應記錄失敗來源"
        assert docs, "其餘 working sources 仍應有文件"
        degrade1["docs_from_working"] = len(docs)

        # 透過 pipeline.run（monkeypatch collect 注入含 failing source 的列表）
        import trustforge.pipeline as _pipeline_mod
        _orig_collect = _pipeline_mod.collect

        def _patched_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
            return collect(
                query,
                coin=coin,
                sources=sources_with_fail,
                offline=True,
                _failed=_failed,
            )

        _pipeline_mod.collect = _patched_collect
        try:
            report, evidence, log = run(
                "BTC", "分析 BTC 壓測降級", QuestionType.MULTI_SOURCE, offline=True
            )
        finally:
            _pipeline_mod.collect = _orig_collect

        # pipeline 不崩，report.limits 有記錄
        limits_text = " ".join(report.limits)
        degrade1["failed_in_limits"] = "failing-news-src" in limits_text
        degrade1["pass"] = bool(evidence) and degrade1["failed_in_limits"]
        degrade1["note"] = (
            f"pipeline 正常完成；working docs={len(docs)}；"
            f"limits 含失敗來源={degrade1['failed_in_limits']}"
        )
    except Exception as exc:
        degrade1["note"] = f"不應拋未捕獲例外，但收到：{exc!r}"
        degrade1["pass"] = False
    results.append(degrade1)

    # ── 驗證 2：Bedrock 離線 → 降級行文 → 不崩 ────────────────────────────────
    degrade2: dict = {
        "case": "bedrock_offline_degradation",
        "pass": False,
        "note": "",
        "narrative_nonempty": False,
    }
    try:
        # offline=True 即模擬 Bedrock 不可用（regex fallback）
        report, evidence, log = run(
            "ETH", "假設 ETH 將突破歷史高點", QuestionType.HYPOTHESIS, offline=True
        )
        has_narrative = bool(report.market_judgment)
        bedrock_events = [e for e in log.events if e["tool"] == "bedrock.complete"]

        degrade2["narrative_nonempty"] = has_narrative
        degrade2["pass"] = has_narrative and bool(evidence)
        degrade2["note"] = (
            f"降級模式成功；market_judgment 非空={has_narrative}；"
            f"bedrock 事件數={len(bedrock_events)}（offline=regex fallback）"
        )
    except Exception as exc:
        degrade2["note"] = f"不應拋未捕獲例外，但收到：{exc!r}"
        degrade2["pass"] = False
    results.append(degrade2)

    return results


# ── Markdown 輸出 ─────────────────────────────────────────────────────────────

def generate_stress_test_md(
    matrix_results: list[dict],
    degradation_results: list[dict],
    offline: bool,
    total_elapsed: float,
) -> str:
    lines: list[str] = []

    lines.append("# TrustForge P0-5 壓測結果")
    lines.append("")
    mode = "offline（樣本資料）" if offline else "online（真實 Bedrock）"
    lines.append(f"> 模式：**{mode}** | 測試時間：{_now_iso()}")
    lines.append(f"> 總執行時間：{total_elapsed:.2f}s")
    lines.append("")

    if offline:
        lines.append(
            "> **offline 模式注意**：elapsed 為 pipeline 內部計時（含 regex fallback），"
            "**不含真實 Bedrock 網路延遲**。真實時間（含 LLM）請用 `--online` 旗標量測。"
        )
        lines.append("")

    # ── 矩陣結果表 ────────────────────────────────────────────────────────────
    lines.append("## 矩陣結果（5 幣 × 3 題型）")
    lines.append("")
    lines.append("| 幣種/配對 | 題型 | elapsed(s) | evidence | 交付件 OK | PASS |")
    lines.append("|-----------|------|-----------|---------|----------|------|")
    for r in matrix_results:
        ev = r.get("evidence_count", "-")
        el = f"{r['elapsed']:.3f}" if r.get("elapsed") is not None else "-"
        dlv = "OK" if r.get("deliverables_ok") else f"MISSING:{r.get('missing')}"
        p = "PASS" if r.get("pass") else f"FAIL({r.get('exception', 'criteria')})"
        lines.append(
            f"| {r['coin']:12s} | {r['qtype']:12s} | {el:>10} | {ev:>8} | {dlv} | {p} |"
        )
    lines.append("")

    # 彙總
    total_cases = len(matrix_results)
    pass_cases = sum(1 for r in matrix_results if r.get("pass"))
    lines.append(f"**通過：{pass_cases}/{total_cases}**")
    lines.append("")

    all_under_budget = all(
        r.get("elapsed", BUDGET_SEC) < BUDGET_SEC for r in matrix_results
    )
    budget_note = "全部 < 15 分鐘" if all_under_budget else "部分超出 15 分鐘"
    lines.append(f"### 結論：{budget_note}")
    if offline:
        lines.append(
            "（offline 模式：時間僅供正確性驗證，真實 Bedrock 時間需 `--online` 確認）"
        )
    lines.append("")

    # ── 失敗降級結果 ──────────────────────────────────────────────────────────
    lines.append("## 失敗降級驗證")
    lines.append("")
    lines.append("| 案例 | PASS | 說明 |")
    lines.append("|------|------|------|")
    for d in degradation_results:
        p = "PASS" if d.get("pass") else "FAIL"
        note = d.get("note", "")
        lines.append(f"| {d['case']} | {p} | {note} |")
    lines.append("")

    degrade_pass = sum(1 for d in degradation_results if d.get("pass"))
    lines.append(f"**降級通過：{degrade_pass}/{len(degradation_results)}**")
    lines.append("")

    # ── 整體結論 ──────────────────────────────────────────────────────────────
    lines.append("## 整體結論")
    all_pass = pass_cases == total_cases and degrade_pass == len(degradation_results)
    lines.append("- 所有矩陣案例通過：" + ("YES" if pass_cases == total_cases else "NO"))
    lines.append("- 所有降級驗證通過：" + ("YES" if degrade_pass == len(degradation_results) else "NO"))
    lines.append("- " + budget_note)
    if offline:
        lines.append("- 真實 Bedrock 時間：**需 --online 旗標 + BEDROCK_MODEL_ID/AWS_REGION env 量測**")
    lines.append("")
    lines.append("---")
    lines.append("*由 `scripts/stress_test.py` 自動產生。*")

    return "\n".join(lines)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="TrustForge P0-5 E2E 壓測 harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument("--offline", action="store_true", default=False,
                          help="offline 模式（預設）")
    mode_grp.add_argument("--online", action="store_true", default=False,
                          help="online 模式（真實 Bedrock；需 env）")
    parser.add_argument("--no-output", action="store_true",
                        help="不寫入 docs/STRESS-TEST.md（僅印出）")
    args = parser.parse_args()

    # online 模式：需 TRUSTFORGE_ONLINE=1 或 BEDROCK_MODEL_ID 作為顯式線上授權
    # 缺授權時明確標 SKIPPED，不以 offline 結果假 pass
    online = args.online
    if online:
        has_online_auth = bool(os.getenv("TRUSTFORGE_ONLINE") or os.getenv("BEDROCK_MODEL_ID"))
        if not has_online_auth:
            print(
                "[SKIPPED(未設線上環境)] --online 需 TRUSTFORGE_ONLINE=1 或 BEDROCK_MODEL_ID；"
                "未設線上環境，略過 online 測試（不以 offline 結果假 pass）。"
            )
            sys.exit(0)
        if not os.getenv("AWS_REGION"):
            print(
                "[SKIPPED(未設線上環境)] --online 需 AWS_REGION env；"
                "未設，略過 online 測試（不以 offline 結果假 pass）。"
            )
            sys.exit(0)

    offline = not online
    print(f"TrustForge P0-5 壓測 harness — 模式：{'offline' if offline else 'online'}")
    print("=" * 60)

    # ── 矩陣 ─────────────────────────────────────────────────────────────────
    QUERY_MS = "分析幣種近兩週市場狀況，整合多源資料"
    QUERY_HY = "假設該幣種短期將突破歷史高點，驗證此假設"
    QUERY_CMP = "比較兩幣種當前市場位置與相對強弱"

    matrix_results: list[dict] = []
    t_total = time.monotonic()

    print("\n[矩陣] single-coin cases (multi_source / hypothesis)：")
    for coin in COINS:
        for qtype, query in [("multi_source", QUERY_MS), ("hypothesis", QUERY_HY)]:
            label = f"{coin}/{qtype}"
            r = run_single_case(label, coin, qtype, query, offline)
            matrix_results.append(r)
            status = "PASS" if r["pass"] else "FAIL"
            exc_note = f" exception={r['exception']!r}" if r["exception"] else ""
            print(
                f"  [{status}] {label:25s}"
                f" elapsed={r['elapsed']:.3f}s"
                f" evidence={r['evidence_count']}"
                f"{exc_note}"
            )

    print("\n[矩陣] comparison cases：")
    for coin_a, coin_b in COMPARISON_PAIRS:
        label = f"{coin_a}/{coin_b}"
        r = run_comparison_case(label, coin_a, coin_b, QUERY_CMP, offline)
        matrix_results.append(r)
        status = "PASS" if r["pass"] else "FAIL"
        exc_note = f" exception={r['exception']!r}" if r["exception"] else ""
        print(
            f"  [{status}] {label:25s}"
            f" elapsed={r['elapsed']:.3f}s"
            f" evidence={r['evidence_count']}"
            f"{exc_note}"
        )

    # ── 失敗降級驗證 ─────────────────────────────────────────────────────────
    print("\n[降級] 失敗降級驗證：")
    degradation_results = run_degradation_tests(offline=True)  # 降級驗證永遠用 offline
    for d in degradation_results:
        status = "PASS" if d["pass"] else "FAIL"
        print(f"  [{status}] {d['case']}: {d['note']}")

    total_elapsed = time.monotonic() - t_total

    # ── 彙總 ─────────────────────────────────────────────────────────────────
    matrix_pass = sum(1 for r in matrix_results if r["pass"])
    degrade_pass = sum(1 for d in degradation_results if d["pass"])
    print(f"\n矩陣：{matrix_pass}/{len(matrix_results)} 通過")
    print(f"降級：{degrade_pass}/{len(degradation_results)} 通過")
    print(f"總計耗時：{total_elapsed:.2f}s")

    # ── 輸出 STRESS-TEST.md ───────────────────────────────────────────────────
    md_content = generate_stress_test_md(
        matrix_results, degradation_results, offline, total_elapsed
    )
    if not args.no_output:
        out_path = _REPO / "docs" / "STRESS-TEST.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md_content, encoding="utf-8")
        print(f"\n[OK] STRESS-TEST.md 寫入：{out_path}")
    else:
        print("\n" + md_content)

    return 0 if (matrix_pass == len(matrix_results) and degrade_pass == len(degradation_results)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
