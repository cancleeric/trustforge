"""Runtime policy consumers in pipeline.run."""
from __future__ import annotations

from trustforge import pipeline
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.policy.schema import (
    AnalysisPolicy,
    EvaluationPolicy,
    ImprovementPolicy,
    ReportPolicy,
    SourcePolicy,
)
from trustforge.schema import BasisItem, QuestionType, Report


class FakePolicyExecutor:
    calls: list[str] = []

    def __init__(self):
        self._policies = {
            "source": SourcePolicy(timeout_sec=12),
            "analysis": AnalysisPolicy(max_llm_calls=2),
            "report": ReportPolicy(max_sections=1, include_contrarian=False),
            "evaluation": EvaluationPolicy(min_pass_score=0.7),
            "improvement": ImprovementPolicy(proposal_limit=1),
        }

    def resolve_effective(self):
        return self._policies

    def snapshot_for_log(self):
        return {
            "event": "policy_snapshot",
            "requires_human_approval": True,
            "policies": {},
        }

    def get_policy(self, family: str):
        self.calls.append(family)
        return self._policies[family]


def test_pipeline_consumes_runtime_policies(monkeypatch):
    FakePolicyExecutor.calls = []
    monkeypatch.setattr(pipeline, "PolicyExecutor", FakePolicyExecutor)
    monkeypatch.setattr(pipeline, "daily_cap_exceeded", lambda: False)
    monkeypatch.setattr(pipeline, "online_stance_requested", lambda: False)

    def fake_collect(query, coin, offline, data_dir=None, _failed=None):
        return [
            Document(
                id="d1",
                kind="news",
                source="unit-test",
                text="BTC unit test",
                ts=1,
                meta={"coin": coin},
            )
        ]

    def fake_run_agent_pipeline(query, coin, qtype, docs, client=None, log=None):
        assert log is not None
        report = Report(
            coin=coin,
            question_type=qtype.value,
            question=query,
            market_judgment="judgment",
            facts=["fact 1", "fact 2"],
            inferences=["inference 1", "inference 2"],
            key_basis=[
                BasisItem("basis 1", "explanation 1"),
                BasisItem("basis 2", "explanation 2"),
            ],
            confidence=0.8,
            limits=["limit 1", "limit 2"],
            could_flip=["flip 1", "flip 2"],
            contrarian=["contra 1", "contra 2"],
            generated_at="2026-07-21T00:00:00Z",
            calibrated_confidence=0.8,
            decision_state="normal",
        )
        return report, []

    monkeypatch.setattr(pipeline, "collect", fake_collect)
    monkeypatch.setattr(pipeline, "run_agent_pipeline", fake_run_agent_pipeline)

    report, _evidence, log = pipeline.run(
        "BTC",
        "policy runtime test",
        QuestionType.MULTI_SOURCE,
        offline=True,
        _log=ExecutionLog(),
    )

    assert FakePolicyExecutor.calls == [
        "source",
        "analysis",
        "report",
        "evaluation",
        "improvement",
    ]
    assert report.facts == ["fact 1"]
    assert report.inferences == ["inference 1"]
    assert [b.claim for b in report.key_basis] == ["basis 1"]
    assert report.contrarian == []
    consumer_events = [e for e in log.events if e["tool"] == "policy.consumer"]
    assert {e["params"]["family"] for e in consumer_events} == set(FakePolicyExecutor.calls)
    assert any(e["tool"] == "policy.consumer.report.applied" for e in log.events)
