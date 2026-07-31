from __future__ import annotations

import json
from dataclasses import dataclass
from unittest import mock

import pytest

from trustforge.agent.agentcore_memory import build_memory_session_manager
from trustforge.agent.agentcore_runtime import (
    analyze_market,
    invoke_payload,
    list_supported_coins,
)
from trustforge.execlog import ExecutionLog


@dataclass
class _Value:
    value: str


def test_supported_coins_uses_canonical_pool():
    assert list_supported_coins() == ["ARB", "BNB", "BTC", "ETH", "SOL", "XRP"]


def test_invoke_payload_validates_required_fields():
    with pytest.raises(ValueError, match="coin must be a string"):
        invoke_payload({"query": "市場如何"})
    with pytest.raises(ValueError, match="query must be a string"):
        invoke_payload({"coin": "BTC"})


def test_invoke_payload_rejects_non_string_and_oversized_values():
    with pytest.raises(ValueError, match="coin must be a string"):
        invoke_payload({"coin": ["BTC"], "query": "市場如何"})
    with pytest.raises(ValueError, match="query must be a string"):
        invoke_payload({"coin": "BTC", "query": {"text": "市場如何"}})
    with pytest.raises(ValueError, match="query is too long"):
        invoke_payload({"coin": "BTC", "query": "x" * 4001})


def test_analyze_market_delegates_to_governed_pipeline():
    # #943: ``run()`` returns a real ``ExecutionLog`` (a plain class, NOT a
    # dataclass). The old stub returned a dataclass mock, which masked the fact
    # that ``asdict(execution_log)`` would TypeError at runtime. Use a real
    # ExecutionLog so the public allowlist path is genuinely exercised.
    log = ExecutionLog(now_fn=lambda: 1000.0, run_id="hermes-test")
    log.record("provider.invoke", params={"provider": "fake"}, summary="ran provider")

    with mock.patch(
        "trustforge.agent.agentcore_runtime.run",
        return_value=(_Value("report"), [_Value("evidence")], log),
    ) as pipeline:
        result = analyze_market(
            "btc",
            "市場如何",
            data_mode="sample",
            llm_mode="off",
        )

    assert result["report"] == {"value": "report"}
    assert result["evidence"] == [{"value": "evidence"}]
    # execution_log now flows through the public allowlist (list of projected
    # dicts), never a raw ``asdict`` of the non-dataclass log.
    assert isinstance(result["execution_log"], list)
    assert all(isinstance(ev, dict) for ev in result["execution_log"])
    assert pipeline.call_args.args[:3][0:2] == ("BTC", "市場如何")
    assert pipeline.call_args.kwargs == {"data_mode": "sample", "llm_mode": "off"}


def test_analyze_market_execution_log_excludes_raw_sensitive_params():
    """#943 contract: the agentcore result's execution_log must carry NO raw
    params secrets — only allowlisted public fields (hermes context +
    ingestion.source meta). Seeded api_key/url/wallet + unlisted keys must not
    leak into the public payload."""
    log = ExecutionLog(now_fn=lambda: 1000.0, run_id="hermes-sec")
    log.record(
        "ingestion.source",
        params={
            "api_key": "sk_live_SECRET",
            "url": "https://x/?token=LEAK",
            "wallet": "0xABC",
            "source": "coinapi",
            "kind": "ohlcv",
            "duration_ms": 42,
            "document_count": 1,
            "outcome": "ok",
            "internal_extra": "must-not-leak",
        },
        summary="fetched source data",
    )
    with mock.patch(
        "trustforge.agent.agentcore_runtime.run",
        return_value=(_Value("report"), [_Value("evidence")], log),
    ):
        result = analyze_market("btc", "市場如何", data_mode="sample", llm_mode="off")

    blob = json.dumps(result["execution_log"], ensure_ascii=False)
    # raw params secrets + unlisted keys never reach the public payload
    for needle in ("sk_live_SECRET", "LEAK", "0xABC", "internal_extra", "must-not-leak"):
        assert needle not in blob, f"sensitive value leaked into execution_log: {needle!r}"
    # public ingestion keys + hermes context survive (frontend reads them)
    ingestion = [ev for ev in result["execution_log"] if ev.get("tool") == "ingestion.source"]
    assert ingestion
    params = ingestion[0]["params"]
    assert params["source"] == "coinapi"
    assert params["duration_ms"] == 42
    assert params["hermes"]["node_id"]


def test_memory_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_AGENTCORE_MEMORY_ENABLED", raising=False)
    assert (
        build_memory_session_manager(actor_id="actor", session_id="session") is None
    )


def test_memory_requires_id_when_enabled(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_AGENTCORE_MEMORY_ENABLED", "true")
    monkeypatch.delenv("TRUSTFORGE_AGENTCORE_MEMORY_ID", raising=False)
    with pytest.raises(RuntimeError, match="MEMORY_ID"):
        build_memory_session_manager(actor_id="actor", session_id="session")


def test_memory_factory_receives_scoped_identity(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_AGENTCORE_MEMORY_ENABLED", "true")
    monkeypatch.setenv("TRUSTFORGE_AGENTCORE_MEMORY_ID", "memory-1")
    factory = mock.Mock(return_value=object())

    result = build_memory_session_manager(
        actor_id="actor-1",
        session_id="session-1",
        factory=factory,
    )

    assert result is factory.return_value
    factory.assert_called_once_with(
        agentcore_memory_id="memory-1",
        actor_id="actor-1",
        session_id="session-1",
    )
