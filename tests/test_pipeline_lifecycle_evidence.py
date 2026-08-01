"""Lifecycle evidence regressions for issue #412."""
from __future__ import annotations

from trustforge import pipeline
from trustforge.ingestion.base import Document
from trustforge.schema import QuestionType


def test_formal_run_logs_policy_provider_and_kernel_evidence(monkeypatch):
    """A formal run should expose policy, provider, and kernel lifecycle evidence."""

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return [
            Document(
                id="p0",
                kind="price",
                source="fixture-price",
                text="BTC Daily OHLCV 2024-01-01: O=45000 H=45500 L=44500 C=45000 V=10000",
                ts=1_000.0,
                meta={"coin": "BTC", "date": "2024-01-01", "close": 45_000.0},
            ),
            Document(
                id="p1",
                kind="price",
                source="fixture-price",
                text="BTC Daily OHLCV 2024-01-15: O=46500 H=47000 L=46000 C=46637.08 V=10000",
                ts=2_000.0,
                meta={"coin": "BTC", "date": "2024-01-15", "close": 46_637.08},
            ),
            Document(
                id="n0",
                kind="news",
                source="fixture-news",
                text="Analysts cite improving liquidity and market breadth for BTC.",
                ts=2_100.0,
                meta={"coin": "BTC"},
            ),
        ]

    monkeypatch.setattr(pipeline, "collect", fake_collect)

    _report, _evidence, log = pipeline.run(
        "BTC",
        "分析 BTC",
        QuestionType.MULTI_SOURCE,
        offline=True,
        run_scope_id="test-pipeline-lifecycle",
    )

    events = log.events
    policy_events = [event for event in events if event.get("tool") == "policy.snapshot"]
    provider_events = [event for event in events if event.get("tool") == "provider.resolve"]
    kernel_events = [event for event in events if event.get("tool") == "judgment.derive"]

    assert policy_events
    assert provider_events
    assert kernel_events

    llm_resolution = [
        event for event in provider_events
        if event["params"].get("key") == "llm"
    ]
    assert llm_resolution
    assert llm_resolution[0]["params"]["configured"]
    assert llm_resolution[0]["params"]["resolved"]
    assert llm_resolution[0]["params"]["invoked"] is True

    assert policy_events[0]["params"].get("event") == "policy_snapshot"
    assert any(
        event["params"].get("judgment_source") == "trustforge_core.run_kernel"
        and "confidence" in event["params"]
        and "contract_version" in event["params"]
        for event in kernel_events
    )
    assert any("abstain" in event["params"] for event in kernel_events)

    policy_idx = events.index(policy_events[0])
    provider_idx = events.index(provider_events[0])
    kernel_idx = events.index(kernel_events[0])
    assert policy_idx < provider_idx < kernel_idx
