"""Production-flow parity checks with Agent OS disabled and enabled."""
from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from trustforge.agent.orchestrator import build_report
from trustforge.analysis_flow import AnalysisFlow
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import OHLCV_DIR, collect
from trustforge.schema import QuestionType
from trustforge.trust.scoring import aggregate, extract_claims, score


def _report_pipeline(enabled: bool) -> tuple[dict, list[dict]]:
    """Run the real deterministic report/evidence pipeline under one flag value."""
    question = "分析 BTC 過去兩週市場狀況"
    docs = collect(question, coin="BTC", offline=True, data_dir=OHLCV_DIR)
    now = max(doc.ts for doc in docs)
    scored = score(extract_claims(docs), now=now)
    brief = aggregate(scored, question)
    with patch.dict(
        os.environ, {"TRUSTFORGE_AGOS_ENABLED": "1" if enabled else "0"}
    ):
        report, evidence = build_report(
            question,
            "BTC",
            QuestionType.MULTI_SOURCE,
            brief,
            client=BedrockClient(offline=True),
            log=ExecutionLog(now_fn=lambda: 1000.0),
            now_fn=lambda: 1000.0,
            scored=scored,
        )
    return asdict(report), [item.to_dict() for item in evidence]


def test_agos_flag_does_not_change_trust_report_or_evidence_output() -> None:
    disabled_report, disabled_evidence = _report_pipeline(False)
    enabled_report, enabled_evidence = _report_pipeline(True)

    assert enabled_report == disabled_report
    assert enabled_evidence == disabled_evidence
    assert [item["trust"] for item in enabled_evidence] == [
        item["trust"] for item in disabled_evidence
    ]
    assert enabled_report["confidence"] == disabled_report["confidence"]


def test_agos_flag_does_not_change_question_rag_or_dialogue(tmp_path: Path) -> None:
    flow = AnalysisFlow(tmp_path / "question-rag.sqlite3")
    flow.register_question(
        "BTC", "risk", "BTC 市場風險與來源分歧", enqueue=False
    )

    with patch.dict(os.environ, {"TRUSTFORGE_AGOS_ENABLED": "0"}):
        disabled = flow.question_context(
            "BTC", "risk", "BTC 來源分歧是否代表市場風險"
        )
    with patch.dict(os.environ, {"TRUSTFORGE_AGOS_ENABLED": "1"}):
        enabled = flow.question_context(
            "BTC", "risk", "BTC 來源分歧是否代表市場風險"
        )

    assert enabled == disabled
    assert enabled["matches"][0]["question"] == "BTC 市場風險與來源分歧"
    assert enabled["conversation"] == disabled["conversation"]
