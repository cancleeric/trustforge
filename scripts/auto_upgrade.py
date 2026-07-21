#!/usr/bin/env python3
"""TrustForge 外框模組自動升級 — 分析完一輪後觸發。

讀取 feature_store + cost_ledger，跑：
1. connector_reliability（來源信譽更新）
2. question_bank（品質回歸）
3. diagnose_improvement（產出改善提案）
"""
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trustforge.connector_reliability import build_reliability_report
from trustforge.improvement import diagnose
from trustforge.hermes import autonomous_cycle_plan

import requests as http_requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("upgrade")

UPGRADE_LOG = Path(__file__).resolve().parent / "logs" / "upgrade_history.jsonl"
AGENTCORE_URL = "http://127.0.0.1:8080/invocations"

REVIEW_SYSTEM_PROMPT = """你是 Hermes 的自我審查模組。你的職責是審查改善提案，決定是否核准自動套用。

審查原則（bounded self-improvement）：
1. 只核准有明確證據支撐的改善
2. 不得改動信任評分的核心邏輯（那是人類決定的）
3. 只能調整：來源權重、connector 重試策略、freshness 閾值
4. severity=high 的提案需要更嚴格的證據
5. 如果證據不足或影響範圍不確定，拒絕

回覆格式（嚴格 JSON）：
{"decision": "approve" 或 "reject", "reason": "一句話說明原因"}
"""


def hermes_review_proposal(proposal: dict) -> dict:
    """用 AgentCore (Claude) 審查一項改善提案。"""
    prompt = f"""審查以下改善提案：

提案 ID: {proposal.get('id', 'unknown')}
領域: {proposal.get('area', 'unknown')}
嚴重程度: {proposal.get('severity', 'unknown')}
證據: {json.dumps(proposal.get('evidence', {}), ensure_ascii=False)[:500]}
建議實驗: {proposal.get('proposed_experiment', '')}
成功指標: {proposal.get('success_metric', '')}

請以 JSON 格式回覆你的審查決定。"""

    try:
        resp = http_requests.post(
            AGENTCORE_URL,
            json={"prompt": f"{REVIEW_SYSTEM_PROMPT}\n\n{prompt}"},
            timeout=60,
        )
        # 解析 streaming response
        text = ""
        for line in resp.text.strip().split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                chunk = line[6:]
                try:
                    text += json.loads(chunk)
                except (json.JSONDecodeError, TypeError):
                    text += chunk

        # 嘗試解析 JSON 決定
        import re
        json_match = re.search(r'\{[^}]*"decision"[^}]*\}', text)
        if json_match:
            return json.loads(json_match.group())
        # fallback: 如果回應包含 approve/reject
        if "approve" in text.lower():
            return {"decision": "approve", "reason": text[:100]}
        return {"decision": "reject", "reason": text[:100] or "無法解析 LLM 回應"}
    except Exception as e:
        return {"decision": "reject", "reason": f"LLM 審查失敗: {e}"}


def run_upgrade_cycle():
    """跑一次外框模組升級循環。"""
    log.info("=== 外框模組升級循環開始 ===")
    results = {"timestamp": datetime.now().isoformat(), "modules": {}}

    # 1. Connector Reliability
    try:
        # 讀取 scheduler 歷史（如果有的話）
        scheduler_log_path = Path(__file__).resolve().parents[1] / "out" / "scheduler_runs.jsonl"
        records = []
        if scheduler_log_path.exists():
            with open(scheduler_log_path) as f:
                records = [json.loads(line) for line in f if line.strip()]

        report = build_reliability_report(records)
        results["modules"]["connector_reliability"] = {
            "status": "ok",
            "sources_count": len(report.get("sources", [])),
            "sources_below_gate": [s["source"] for s in report.get("sources", []) if not s.get("meets_reliability_gate", True)],
        }
        log.info(f"  connector_reliability: {results['modules']['connector_reliability']}")
    except Exception as e:
        results["modules"]["connector_reliability"] = {"status": "error", "error": str(e)}
        log.warning(f"  connector_reliability failed: {e}")

    # 2. Diagnose Improvement
    try:
        diagnosis = diagnose(
            scheduler_runs=records if records else [],
            connector_reliability=report if 'report' in dir() else None,
        )
        proposals = diagnosis.get("proposals", [])
        results["modules"]["improvement"] = {
            "status": "ok",
            "proposals_count": len(proposals),
            "proposals": [{"id": p.get("id", ""), "area": p.get("area", ""), "severity": p.get("severity", "")} for p in proposals[:5]],
        }
        log.info(f"  improvement: {len(proposals)} proposals")

        # 2b. Hermes LLM 審查提案 → 自動決定是否套用
        if proposals:
            log.info(f"  🤖 Hermes LLM 審查 {len(proposals)} 項提案...")
            for p in proposals:
                verdict = hermes_review_proposal(p)
                p_result = {"id": p.get("id", ""), "verdict": verdict["decision"], "reason": verdict["reason"]}
                results["modules"].setdefault("reviews", []).append(p_result)
                if verdict["decision"] == "approve":
                    log.info(f"    ✅ 通過: {p.get('id')} — {verdict['reason'][:60]}")
                    # 記錄核准（實際套用由 review_hermes_upgrades 處理）
                else:
                    log.info(f"    ❌ 拒絕: {p.get('id')} — {verdict['reason'][:60]}")
    except Exception as e:
        results["modules"]["improvement"] = {"status": "error", "error": str(e)}
        log.warning(f"  improvement failed: {e}")

    # 3. Feature Store 統計
    try:
        import sqlite3
        db_path = Path(__file__).resolve().parents[1] / "out" / "trustforge.sqlite3"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            row = conn.execute("SELECT COUNT(*), COUNT(DISTINCT entity_key) FROM trust_feature_values").fetchone()
            results["modules"]["feature_store"] = {
                "status": "ok",
                "total_features": row[0],
                "unique_coins": row[1],
            }
            conn.close()
            log.info(f"  feature_store: {row[0]} features, {row[1]} coins")
        else:
            results["modules"]["feature_store"] = {"status": "empty", "note": "sqlite not found"}
    except Exception as e:
        results["modules"]["feature_store"] = {"status": "error", "error": str(e)}

    # 寫入升級歷史
    UPGRADE_LOG.parent.mkdir(exist_ok=True)
    with open(UPGRADE_LOG, "a") as f:
        f.write(json.dumps(results, ensure_ascii=False) + "\n")

    log.info(f"=== 升級循環完成 — 結果寫入 {UPGRADE_LOG} ===")
    return results


if __name__ == "__main__":
    run_upgrade_cycle()
