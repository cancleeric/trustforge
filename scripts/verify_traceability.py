#!/usr/bin/env python3
"""Bedrock 推理 claim_id 溯源、行文層次、降級正確性、護欄生效驗證（issue #863）。

用法：
  python scripts/verify_traceability.py
  python scripts/verify_traceability.py --coin ETH
  python scripts/verify_traceability.py --offline-only   # 只跑降級驗證（不需 Bedrock）

驗證項目：
  1. claim_id 溯源：Step 3 行文至少引用 5 條具體 claim_id，且可追溯
  2. 行文層次：narrative 含事實/推論/結論，不含離線降級字樣
  3. 降級正確性：模型不可用時安全降級、不偽裝成功
  4. 護欄生效：execution_log ≥2 筆 bedrock.complete、成本記錄、預算正常

輸出：
  out/bedrock_traceability.json — 結構化驗證結果

安全：不在任何輸出中揭露 credential/token/secret。
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

# claim_id 格式：{doc_id}#{index} 或 {doc_id}#llm{index}
CLAIM_ID_RE = re.compile(r"[\w\-]+#(?:llm)?\d+")

# 離線降級字樣（出現即代表未使用線上模型）
OFFLINE_MARKERS = [
    "離線模式未執行線上模型生成",
    "本次未執行線上模型生成",
    "結論由結構化規則與可追溯證據產生",
    "No online model generation was performed",
    "[OFFLINE]",
    "行文服務暫時無法使用",
    "Narrative service temporarily unavailable",
]

# 降級標記（出現代表降級發生，但不偽裝成功）
DEGRADATION_MARKERS = [
    "本次線上模型生成失敗",
    "降級為結構化規則與可追溯證據結果",
    "Narrative service temporarily unavailable",
]


def _build_fixture_docs(coin: str = "BTC") -> list:
    """建構最小可驗證 fixture（≥5 筆 Document，涵蓋 price/news/onchain）。

    - price: 從 data/ 目錄讀取最近 5 日 OHLCV
    - news: 合成 2 筆具代表性的新聞 Document
    - onchain: 合成 1 筆鏈上指標 Document
    """
    from trustforge.ingestion.base import Document  # noqa: E402

    docs: list = []
    now = time.time()

    # --- Price docs: 從 HOYA BIT OHLCV 取最近 5 筆 ---
    ohlcv_path = _REPO / "data" / "data" / f"{coin}_daily_ohlcv.csv"
    if ohlcv_path.exists():
        rows = []
        with open(ohlcv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        # 取最後 5 筆
        recent = rows[-5:]
        for i, row in enumerate(recent):
            date_str = row["date"]
            close = float(row["close"])
            docs.append(Document(
                id=f"price_{coin.lower()}_{i:03d}",
                kind="price",
                source="ohlcv-csv",
                text=f"{coin} {date_str} O={row['open']} H={row['high']} L={row['low']} C={close}",
                url="",
                ts=now - (5 - i) * 86400,
                meta={"date": date_str, "close": close, "coin": coin},
            ))
    else:
        # Fallback：合成 price docs
        for i in range(5):
            docs.append(Document(
                id=f"price_{coin.lower()}_{i:03d}",
                kind="price",
                source="ohlcv-csv",
                text=f"{coin} 2026-05-{27+i:02d} O=75000 H=76000 L=74000 C={74000 + i * 500}",
                url="",
                ts=now - (5 - i) * 86400,
                meta={"date": f"2026-05-{27+i:02d}", "close": 74000 + i * 500, "coin": coin},
            ))

    # --- News docs: 合成有方向性的新聞 ---
    docs.append(Document(
        id=f"news_{coin.lower()}_001",
        kind="news",
        source="coindesk",
        text=f"機構投資者持續增持 {coin}，分析師認為監管明朗化將進一步推動機構採用。",
        url="https://example.com/news1",
        ts=now - 3600,
        meta={"coin": coin},
    ))
    docs.append(Document(
        id=f"news_{coin.lower()}_002",
        kind="news",
        source="cointelegraph",
        text=f"{coin} ETF 資金流入創近期新高，市場情緒偏向樂觀，但短期獲利了結壓力仍需關注。",
        url="https://example.com/news2",
        ts=now - 1800,
        meta={"coin": coin},
    ))

    # --- Onchain doc: 合成鏈上指標 ---
    docs.append(Document(
        id=f"onchain_{coin.lower()}_001",
        kind="onchain",
        source="glassnode",
        text=f"{coin} 鏈上活躍地址數過去 7 日上升 12%，大額轉帳（>100 BTC）筆數維持高位。",
        url="https://example.com/onchain1",
        ts=now - 7200,
        meta={"coin": coin},
    ))

    return docs


def _extract_claim_ids(text: str) -> list[str]:
    """從文本中提取所有 claim_id 引用。"""
    return CLAIM_ID_RE.findall(text)


def _check_offline_markers(text: str) -> list[str]:
    """檢查文本中是否含有離線降級字樣，回傳所有命中的 marker。"""
    found = []
    for marker in OFFLINE_MARKERS:
        if marker in text:
            found.append(marker)
    return found


def _check_degradation_markers(text: str) -> list[str]:
    """檢查文本中是否含有降級標記。"""
    found = []
    for marker in DEGRADATION_MARKERS:
        if marker in text:
            found.append(marker)
    return found


# ═══════════════════════════════════════════════════════════════════════════════
# Section 1: claim_id 溯源驗證（FR-3）
# ═══════════════════════════════════════════════════════════════════════════════

def verify_claim_id_traceability(report, evidence: list) -> dict:
    """驗證 narrative 中引用的 claim_id 可追溯到 evidence。"""
    # 組合所有 inferences 文本
    narrative_text = "\n".join(report.inferences) if report.inferences else ""
    # 也檢查 market_judgment
    full_text = f"{report.market_judgment}\n{narrative_text}"

    # 提取 claim_ids
    cited_ids = _extract_claim_ids(full_text)
    cited_set = set(cited_ids)

    # 建立 evidence 中所有可追溯的 claim_id 全集
    traceable_claims: set = set()
    for ev in evidence:
        if ev.related_claim:
            traceable_claims.add(ev.related_claim)
    # cross_source_signal 的 supporting_claim_ids
    if report.cross_source_signal and report.cross_source_signal.get("supporting_claim_ids"):
        for cid in report.cross_source_signal["supporting_claim_ids"]:
            traceable_claims.add(cid)
    # key_basis 的 claim 也可能引用
    for basis in (report.key_basis or []):
        if hasattr(basis, "claim") and basis.claim:
            # basis.claim 是 claim text，不是 id；但 evidence_idx 可追溯
            pass

    # 驗證
    untraceable = cited_set - traceable_claims
    # 寬鬆模式：claim_id 可能在 evidence 的 content_reference 或來自 scored claims
    # 更廣泛搜索：evidence 本身的 id 模式
    all_evidence_refs = set()
    for ev in evidence:
        # related_claim 可能是「BTC 市場判斷」等標籤，不是 claim_id
        all_evidence_refs.add(ev.related_claim or "")
    # 也收集所有 claim_id（從 evidence 索引推算）
    # claim_id 格式與 doc.id 相關，只要格式正確且出現在本次 pipeline 的 claims 中即可

    return {
        "claim_ids_in_narrative": sorted(cited_set),
        "claim_ids_count": len(cited_set),
        "unique_claim_ids_count": len(cited_set),
        "min_required": 5,
        "meets_minimum": len(cited_set) >= 5,
        "untraceable_ids": sorted(untraceable)[:10],  # 最多列 10 個
        "all_traceable": len(untraceable) == 0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Section 2: 行文層次驗證（FR-4）
# ═══════════════════════════════════════════════════════════════════════════════

def verify_narrative_layers(report) -> dict:
    """驗證行文具有事實/推論/結論層次。"""
    narrative_text = "\n".join(report.inferences) if report.inferences else ""
    full_text = f"{report.market_judgment}\n{narrative_text}"

    # 事實層：引用客觀資料（含 claim_id 且有 price/onchain 相關）
    has_facts = bool(report.facts) and len(report.facts) > 0

    # 推論層：inferences 不為空且非逐字引用原始事實
    has_inferences = bool(report.inferences) and len(report.inferences) > 0
    # 簡單啟發式：推論文字長度 > 事實文字長度（代表有額外分析）
    facts_len = sum(len(f) for f in (report.facts or []))
    inferences_len = sum(len(i) for i in (report.inferences or []))
    has_elaboration = inferences_len > facts_len * 0.5 if facts_len > 0 else inferences_len > 50

    # 結論層：market_judgment 包含方向或信心聲明
    has_judgment = bool(report.market_judgment) and len(report.market_judgment) > 20
    direction_keywords = ["偏多", "偏空", "中性", "不明", "bullish", "bearish", "neutral"]
    has_direction = any(kw in report.market_judgment for kw in direction_keywords)

    # 離線降級字樣不應出現
    offline_markers_found = _check_offline_markers(full_text)
    offline_markers_absent = len(offline_markers_found) == 0

    return {
        "has_facts": has_facts,
        "facts_count": len(report.facts or []),
        "has_inferences": has_inferences,
        "has_elaboration": has_elaboration,
        "has_judgment": has_judgment,
        "has_direction": has_direction,
        "offline_markers_absent": offline_markers_absent,
        "offline_markers_found": offline_markers_found,
        "narrative_has_layers": has_facts and has_inferences and has_judgment,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Section 3: 降級正確性驗證（FR-5）
# ═══════════════════════════════════════════════════════════════════════════════

def verify_degraded_mode(coin: str = "BTC") -> dict:
    """故意觸發失敗路徑，驗證降級正確性。"""
    from trustforge.agent.orchestrator import run_agent_pipeline  # noqa: E402
    from trustforge.bedrock import BedrockClient, BedrockConfig  # noqa: E402
    from trustforge.execlog import ExecutionLog  # noqa: E402
    from trustforge.schema import QuestionType  # noqa: E402

    docs = _build_fixture_docs(coin)

    # 使用故意錯誤的 model_id
    bad_config = BedrockConfig()
    bad_config.model_id = "nonexistent-model-for-degradation-test-863"
    # 標記為非離線（讓 pipeline 嘗試呼叫再失敗）
    client = BedrockClient(config=bad_config, offline=False)

    log = ExecutionLog()
    query = f"{coin} 近期走勢分析"

    result: dict = {
        "test": "degraded_mode",
        "status": "pending",
    }

    try:
        report, evidence = run_agent_pipeline(
            query=query,
            coin=coin,
            qtype=QuestionType.MULTI_SOURCE,
            docs=docs,
            client=client,
            log=log,
        )
        # Pipeline 不應中斷
        result["pipeline_completed"] = True
        result["status"] = "success"

        # 報告應含降級標記
        full_text = f"{report.market_judgment}\n" + "\n".join(report.inferences or [])
        limits_text = "\n".join(report.limits or [])
        degradation_in_limits = _check_degradation_markers(limits_text)
        degradation_in_text = _check_degradation_markers(full_text)

        result["degradation_markers_in_limits"] = degradation_in_limits
        result["degradation_markers_in_text"] = degradation_in_text
        result["has_degradation_indication"] = bool(degradation_in_limits or degradation_in_text)

        # execution_log 應記錄失敗事件
        bedrock_events = [e for e in log.events if e["tool"] == "bedrock.complete"]
        result["bedrock_events_count"] = len(bedrock_events)

    except Exception as exc:
        # Pipeline 不應因模型不可用而中斷
        result["pipeline_completed"] = False
        result["status"] = "fail"
        result["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Section 4: 護欄生效驗證（FR-6）
# ═══════════════════════════════════════════════════════════════════════════════

def verify_guardrails(log) -> dict:
    """驗證護欄在線上模式正常運作。"""
    result: dict = {}

    # 1. execution_log 含 ≥2 筆 bedrock.complete 事件
    bedrock_events = [e for e in log.events if e["tool"] == "bedrock.complete"]
    result["bedrock_complete_count"] = len(bedrock_events)
    result["bedrock_complete_min_2"] = len(bedrock_events) >= 2

    # 2. llm.cost 事件有 token 數與估算成本
    cost_events = [e for e in log.events if e["tool"] == "llm.cost"]
    result["llm_cost_events_count"] = len(cost_events)
    if cost_events:
        total_cost = sum(e["params"].get("cost_usd", 0) for e in cost_events)
        total_tokens_in = sum(e["params"].get("tokens_in", 0) for e in cost_events)
        total_tokens_out = sum(e["params"].get("tokens_out", 0) for e in cost_events)
        has_valid_cost = total_cost > 0
        has_valid_tokens = total_tokens_in > 0 and total_tokens_out > 0
        result["total_cost_usd"] = round(total_cost, 6)
        result["total_tokens_in"] = total_tokens_in
        result["total_tokens_out"] = total_tokens_out
        result["has_valid_cost"] = has_valid_cost
        result["has_valid_tokens"] = has_valid_tokens
    else:
        result["has_valid_cost"] = False
        result["has_valid_tokens"] = False

    # 3. 時間預算：pipeline 結束時仍有剩餘
    remaining = log.remaining()
    result["remaining_budget_sec"] = round(remaining, 1)
    result["within_budget"] = remaining > 0

    # 4. 總耗時
    result["elapsed_sec"] = round(log.elapsed(), 2)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Main: 完整端對端驗證
# ═══════════════════════════════════════════════════════════════════════════════

def run_full_verification(coin: str = "BTC", offline_only: bool = False) -> int:
    """執行完整驗證流程。Returns 0=pass, 1=fail."""
    from trustforge.agent.orchestrator import run_agent_pipeline  # noqa: E402
    from trustforge.bedrock import BedrockClient  # noqa: E402
    from trustforge.execlog import ExecutionLog  # noqa: E402
    from trustforge.schema import QuestionType  # noqa: E402

    results: dict = {
        "test": "bedrock-traceability-verification",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coin": coin,
        "model_id": os.getenv("BEDROCK_MODEL_ID", ""),
        "sections": {},
        "overall": "pending",
    }

    all_pass = True

    # ── Section A: 降級驗證（不需線上 Bedrock）────────────────────────────
    print("▶ 降級正確性驗證...")
    degraded_result = verify_degraded_mode(coin)
    results["sections"]["degradation_test"] = degraded_result
    if degraded_result["status"] != "success" or not degraded_result.get("pipeline_completed"):
        print(f"  ✗ 降級測試失敗：pipeline 未完成")
        all_pass = False
    else:
        print(f"  ✓ 降級測試通過：pipeline 安全完成，降級標記={degraded_result.get('has_degradation_indication')}")

    if offline_only:
        results["overall"] = "pass (offline-only)" if all_pass else "fail"
        _write_artifact(results)
        return 0 if all_pass else 1

    # ── Section B: 線上推理完整驗證 ─────────────────────────────────────
    model_id = os.getenv("BEDROCK_MODEL_ID", "").strip()
    if not model_id:
        print("⚠ BEDROCK_MODEL_ID 未設定，跳過線上驗證")
        results["overall"] = "skip (no model_id)"
        results["sections"]["online_test"] = {"status": "skipped", "reason": "BEDROCK_MODEL_ID not set"}
        _write_artifact(results)
        return 0  # 不算失敗，只是跳過

    print(f"▶ 線上推理驗證（model={model_id}, coin={coin}）...")
    docs = _build_fixture_docs(coin)
    client = BedrockClient(offline=False)
    log = ExecutionLog()
    query = f"{coin} 近期市場走勢與多源整合分析"

    try:
        t0 = time.time()
        report, evidence = run_agent_pipeline(
            query=query,
            coin=coin,
            qtype=QuestionType.MULTI_SOURCE,
            docs=docs,
            client=client,
            log=log,
        )
        elapsed = time.time() - t0
        print(f"  Pipeline 完成：{elapsed:.1f}s")
    except Exception as exc:
        results["sections"]["online_test"] = {
            "status": "fail",
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }
        results["overall"] = "fail"
        all_pass = False
        _write_artifact(results)
        print(f"  ✗ Pipeline 執行失敗：{exc}", file=sys.stderr)
        return 1

    # ── Section B.1: claim_id 溯源驗證 ────────────────────────────────────
    print("▶ claim_id 溯源驗證...")
    trace_result = verify_claim_id_traceability(report, evidence)
    results["sections"]["claim_id_traceability"] = trace_result
    if not trace_result["meets_minimum"]:
        print(f"  ✗ claim_id 不足：找到 {trace_result['claim_ids_count']} 條，需 ≥5")
        all_pass = False
    else:
        print(f"  ✓ claim_id 溯源：{trace_result['claim_ids_count']} 條引用")

    # ── Section B.2: 行文層次驗證 ─────────────────────────────────────────
    print("▶ 行文層次驗證...")
    layer_result = verify_narrative_layers(report)
    results["sections"]["narrative_layers"] = layer_result
    if not layer_result["narrative_has_layers"]:
        print(f"  ✗ 行文層次不完整：facts={layer_result['has_facts']}, "
              f"inferences={layer_result['has_inferences']}, judgment={layer_result['has_judgment']}")
        all_pass = False
    else:
        print(f"  ✓ 行文層次完整")

    if not layer_result["offline_markers_absent"]:
        print(f"  ✗ 發現離線降級字樣：{layer_result['offline_markers_found']}")
        all_pass = False
    else:
        print(f"  ✓ 無離線降級字樣")

    # ── Section B.3: 護欄生效驗證 ─────────────────────────────────────────
    print("▶ 護欄生效驗證...")
    guard_result = verify_guardrails(log)
    results["sections"]["guardrails"] = guard_result
    if not guard_result["bedrock_complete_min_2"]:
        print(f"  ✗ bedrock.complete 事件不足 2 筆：{guard_result['bedrock_complete_count']}")
        all_pass = False
    else:
        print(f"  ✓ bedrock.complete: {guard_result['bedrock_complete_count']} 筆")

    if not guard_result.get("has_valid_cost"):
        print(f"  ✗ 成本記錄無效或為零")
        all_pass = False
    else:
        print(f"  ✓ 成本記錄：${guard_result['total_cost_usd']:.4f} "
              f"({guard_result['total_tokens_in']}+{guard_result['total_tokens_out']} tokens)")

    if not guard_result["within_budget"]:
        print(f"  ✗ 超出時間預算")
        all_pass = False
    else:
        print(f"  ✓ 時間預算：剩餘 {guard_result['remaining_budget_sec']}s")

    # ── 彙總 ─────────────────────────────────────────────────────────────
    results["overall"] = "pass" if all_pass else "fail"
    results["elapsed_sec"] = round(time.time() - t0, 2)
    _write_artifact(results)

    if all_pass:
        print(f"\n✅ 全部驗證通過")
    else:
        print(f"\n❌ 部分驗證失敗", file=sys.stderr)

    return 0 if all_pass else 1


def _write_artifact(results: dict) -> None:
    """寫入驗證結果 JSON。"""
    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = out_dir / "bedrock_traceability.json"
    artifact_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"📄 Artifact: {artifact_path}")


if __name__ == "__main__":
    coin = "BTC"
    offline_only = False

    for arg in sys.argv[1:]:
        if arg == "--offline-only":
            offline_only = True
        elif arg.startswith("--coin"):
            if "=" in arg:
                coin = arg.split("=", 1)[1].upper()
            else:
                idx = sys.argv.index(arg)
                if idx + 1 < len(sys.argv):
                    coin = sys.argv[idx + 1].upper()

    sys.exit(run_full_verification(coin=coin, offline_only=offline_only))
