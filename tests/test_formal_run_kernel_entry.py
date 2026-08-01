"""Formal pipeline authoritative-kernel regression (#734)."""

from __future__ import annotations

from trustforge.ingestion.base import Document
from trustforge.pipeline import run
from trustforge.schema import QuestionType


def test_formal_pipeline_pr1_never_enters_candidate_runtime(monkeypatch):
    monkeypatch.setenv("KERNEL_CANARY_RATIO", "1.0")
    monkeypatch.setenv("KERNEL_SHADOW_OBSERVE", "1")

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return [
            Document(
                id="formal-price",
                kind="price",
                source="hoya-ohlcv",
                text=f"{coin} Daily OHLCV close rose from 45000 to 46600.",
                ts=1_700_000_000.0,
                meta={"coin": coin, "close": 46600.0},
            ),
            Document(
                id="formal-news",
                kind="news",
                source="coindesk",
                text=f"Analysts say {coin} spot demand improved after ETF inflows expanded.",
                ts=1_700_000_100.0,
                meta={"coin": coin},
            ),
        ]

    def bomb(*args, **kwargs):
        raise AssertionError("PR1 formal pipeline must not call candidate runtime")

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)
    import trustforge.agent.orchestrator as orchestrator

    monkeypatch.setattr(orchestrator, "to_kernel_input", bomb, raising=False)
    monkeypatch.setattr(orchestrator, "run_kernel", bomb, raising=False)
    monkeypatch.setattr(orchestrator, "record_shadow_run", bomb, raising=False)

    report, evidence, log = run(
        "BTC",
        "formal BTC multi-source analysis",
        QuestionType.MULTI_SOURCE,
        offline=True,
        run_scope_id="test-formal-run-kernel-entry",
    )

    assert report.coin == "BTC"
    assert evidence
    event = next(event for event in log.events if event.get("tool") == "judgment.derive")
    assert event["params"]["judgment_source"] == "trustforge_core.run_kernel"
    assert event["params"]["provider_calls"] == 0
    assert event["params"]["cost_usd"] == 0.0
    assert "shadow_observation_status" not in event["params"]
