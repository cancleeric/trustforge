"""Tests for provider ports — Protocol 驗證、fake 實作、resolver、切換測試、spy adapter。

Ref: Issue #386, Spec .kiro/specs/provider-ports-386.md
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from trustforge import pipeline as pl
from trustforge.ingestion.base import Document
from trustforge.ports import (
    AgentCoreLLMAdapter,
    AgentRuntimeProvider,
    BedrockLLMAdapter,
    BedrockModelProvider,
    BudgetProvider,
    CacheProvider,
    FakeAgentRuntimeProvider,
    FakeBudgetProvider,
    FakeCacheProvider,
    FakeLLMProvider,
    FakeModelProvider,
    FakeObservabilityProvider,
    FakeSecurityDecisionProvider,
    FakeSourceProvider,
    LLMProvider,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    NullCacheAdapter,
    NullLLMAdapter,
    NullModelProvider,
    ObservabilityProvider,
    PolicyDecision,
    PolicyRequest,
    ProviderResolution,
    ProviderSet,
    RuntimeCapability,
    RuntimeRun,
    RuntimeSession,
    RuntimeToolCall,
    RuntimeTraceEvent,
    SecurityDecisionProvider,
    SourceProvider,
    evaluate_security_decision,
    redact_policy_context,
    resolve_providers,
)
from trustforge.schema import QuestionType


# ═══════════════════════════════════════════════════════════════════════════════
# R5-1: runtime_checkable isinstance 驗證
# ═══════════════════════════════════════════════════════════════════════════════


class TestProtocolRuntimeCheckable:
    """每個 Protocol 的 fake 實作通過 isinstance 檢查。"""

    def test_llm_provider_isinstance(self):
        fake = FakeLLMProvider()
        assert isinstance(fake, LLMProvider)

    def test_model_provider_isinstance(self):
        fake = FakeModelProvider()
        assert isinstance(fake, ModelProvider)

    def test_agent_runtime_provider_isinstance(self):
        fake = FakeAgentRuntimeProvider()
        assert isinstance(fake, AgentRuntimeProvider)

    def test_cache_provider_isinstance(self):
        fake = FakeCacheProvider()
        assert isinstance(fake, CacheProvider)

    def test_source_provider_isinstance(self):
        fake = FakeSourceProvider()
        assert isinstance(fake, SourceProvider)

    def test_observability_provider_isinstance(self):
        fake = FakeObservabilityProvider()
        assert isinstance(fake, ObservabilityProvider)

    def test_budget_provider_isinstance(self):
        fake = FakeBudgetProvider()
        assert isinstance(fake, BudgetProvider)

    def test_security_decision_provider_isinstance(self):
        fake = FakeSecurityDecisionProvider()
        assert isinstance(fake, SecurityDecisionProvider)


# ═══════════════════════════════════════════════════════════════════════════════
# R3: Fake 實作功能正確性
# ═══════════════════════════════════════════════════════════════════════════════


class TestFakeLLMProvider:
    def test_complete_returns_default(self):
        fake = FakeLLMProvider(default_response="hello")
        result = fake.complete("sys", "user prompt")
        assert result == "hello"
        assert len(fake.calls) == 1
        assert fake.calls[0]["method"] == "complete"

    def test_classify_stance_returns_default(self):
        fake = FakeLLMProvider(default_stance="entailment")
        result = fake.classify_stance("BTC 漲", "BTC 上升")
        assert result == "entailment"
        assert fake.calls[0]["method"] == "classify_stance"

    def test_records_multiple_calls(self):
        fake = FakeLLMProvider()
        fake.complete("s1", "p1")
        fake.classify_stance("a", "b")
        fake.complete("s2", "p2")
        assert len(fake.calls) == 3


class TestModelProviderContract:
    """Provider-neutral model contract (#405)."""

    def test_text_completion_returns_identity_and_usage(self):
        usage = ModelUsage(input_tokens=12, output_tokens=5, total_tokens=17, cost_usd=0.002)
        provider = FakeModelProvider(default_text="hello", usage=usage)

        response = provider.complete(ModelRequest(system="s", prompt="p", model="m1"))

        assert isinstance(response, ModelResponse)
        assert response.text == "hello"
        assert response.model == "m1"
        assert response.provider == "fake"
        assert response.usage == usage
        assert provider.calls == [ModelRequest(system="s", prompt="p", model="m1")]

    def test_structured_output_is_provider_neutral(self):
        provider = FakeModelProvider(default_structured={"answer": 42})

        response = provider.complete(
            ModelRequest(system="s", prompt="json please", response_format="json")
        )

        assert response.structured == {"answer": 42}
        assert response.text == "fake model response"

    def test_null_model_provider_is_offline_and_zero_cost(self):
        response = NullModelProvider().complete(
            ModelRequest(system="s", prompt="p", response_format="json")
        )

        assert response.provider == "null"
        assert response.model == "offline/null"
        assert response.usage == ModelUsage()
        assert response.structured == {}

    def test_error_has_classification(self):
        exc = ModelProviderError("rate limit", category="rate_limited")

        assert str(exc) == "rate limit"
        assert exc.category == "rate_limited"

    def test_bedrock_model_provider_adapts_completion_result(self):
        """Issue #407: Bedrock has a provider-neutral ModelProvider adapter."""
        from trustforge.bedrock import LLMResult

        class FakeBedrockClient:
            def __init__(self):
                self.calls = []

            def complete(self, system: str, prompt: str):
                self.calls.append({"system": system, "prompt": prompt})
                return LLMResult(
                    text="bedrock answer",
                    input_tokens=1_000,
                    output_tokens=500,
                    model_id="au.anthropic.claude-haiku-4-5-20251001-v1:0",
                )

        client = FakeBedrockClient()
        provider = BedrockModelProvider(client=client)

        response = provider.complete(ModelRequest(system="sys", prompt="user"))

        assert isinstance(provider, ModelProvider)
        assert client.calls == [{"system": "sys", "prompt": "user"}]
        assert response == ModelResponse(
            text="bedrock answer",
            model="au.anthropic.claude-haiku-4-5-20251001-v1:0",
            provider="bedrock",
            usage=ModelUsage(
                input_tokens=1_000,
                output_tokens=500,
                total_tokens=1_500,
                cost_usd=0.0035,
            ),
        )

    def test_bedrock_model_provider_classifies_client_errors(self):
        """Issue #407: provider failures should cross the port with categories."""

        class TimeoutClient:
            def complete(self, system: str, prompt: str):
                raise TimeoutError("bedrock timed out")

        provider = BedrockModelProvider(client=TimeoutClient())

        with pytest.raises(ModelProviderError) as excinfo:
            provider.complete(ModelRequest(system="sys", prompt="user"))

        assert excinfo.value.category == "timeout"
        assert str(excinfo.value) == "bedrock timed out"

    def test_contract_fields_do_not_embed_trustforge_domain_terms(self):
        forbidden = {"stance", "coin", "evidence", "hermes", "claim"}
        field_names = {
            *ModelRequest.__dataclass_fields__,
            *ModelResponse.__dataclass_fields__,
            *ModelUsage.__dataclass_fields__,
        }

        assert field_names.isdisjoint(forbidden)


class TestAgentRuntimeProviderContract:
    """Provider-neutral agent runtime contract (#406)."""

    def test_capabilities_are_generic(self):
        runtime = FakeAgentRuntimeProvider(
            [RuntimeCapability(name="tools", version="1", limits={"max": 2})]
        )

        assert runtime.capabilities() == [
            RuntimeCapability(name="tools", version="1", limits={"max": 2})
        ]

    def test_session_run_tool_trace_and_cancel_lifecycle(self):
        runtime = FakeAgentRuntimeProvider()
        session = runtime.start_session({"tenant": "unit"})
        tool = RuntimeToolCall(name="lookup", arguments={"q": "x"}, timeout_sec=3)

        run = runtime.start_run(session, input={"prompt": "hello"}, tools=[tool])
        trace = RuntimeTraceEvent(event="tool.started", payload={"tool": "lookup"})
        runtime.trace(run.run_id, trace)

        assert isinstance(session, RuntimeSession)
        assert isinstance(run, RuntimeRun)
        assert session.metadata == {"tenant": "unit"}
        assert run.status == "running"
        assert run.output == {
            "session_id": session.session_id,
            "input": {"prompt": "hello"},
            "tools": ["lookup"],
        }
        assert runtime.traces == [(run.run_id, trace)]
        assert runtime.cancel_run(run.run_id) is True
        assert runtime.runs[run.run_id].status == "cancelled"
        assert runtime.cancel_run("missing") is False

    def test_contract_fields_do_not_embed_trustforge_or_model_provider_terms(self):
        forbidden = {
            "aws",
            "bedrock",
            "trustforge",
            "coin",
            "evidence",
            "hermes",
            "model",
            "tokens",
            "cost",
        }
        field_names = {
            *RuntimeCapability.__dataclass_fields__,
            *RuntimeSession.__dataclass_fields__,
            *RuntimeToolCall.__dataclass_fields__,
            *RuntimeTraceEvent.__dataclass_fields__,
            *RuntimeRun.__dataclass_fields__,
        }

        assert field_names.isdisjoint(forbidden)


class TestSecurityDecisionProviderContract:
    """Provider-neutral security decision contract (#416)."""

    def test_allow_and_deny_decisions_are_generic(self):
        allow_provider = FakeSecurityDecisionProvider()
        deny_provider = FakeSecurityDecisionProvider(
            PolicyDecision(
                action="deny",
                reason="policy_miss",
                evidence={"rule": "default-deny"},
            )
        )
        request = PolicyRequest(
            subject="user:123",
            action="artifact.read",
            resource="artifact:abc",
            context={"tenant": "unit"},
        )

        allow = evaluate_security_decision(allow_provider, request)
        deny = evaluate_security_decision(deny_provider, request)

        assert allow.allowed is True
        assert allow.action == "allow"
        assert deny.allowed is False
        assert deny.action == "deny"
        assert deny.reason == "policy_miss"
        assert deny.evidence == {"rule": "default-deny"}
        assert allow_provider.requests == [request]

    def test_policy_failure_fails_closed_and_redacts_context(self):
        provider = FakeSecurityDecisionProvider(
            failure=RuntimeError("boom token=super-secret")
        )
        request = PolicyRequest(
            subject="user:123",
            action="artifact.read",
            resource="artifact:abc",
            context={
                "tenant": "unit",
                "token": "super-secret",
                "nested": {"Authorization": "Bearer super-secret"},
            },
        )

        decision = evaluate_security_decision(provider, request)

        assert decision.action == "deny"
        assert decision.allowed is False
        assert decision.reason == "policy evaluation failed: RuntimeError"
        assert decision.evidence == {
            "provider": "fake-security",
            "context": {
                "tenant": "unit",
                "token": "<redacted>",
                "nested": {"Authorization": "<redacted>"},
            },
        }
        assert "super-secret" not in str(decision)

    def test_policy_decision_evidence_is_redacted_before_logging(self):
        provider = FakeSecurityDecisionProvider(
            PolicyDecision(
                action="allow",
                reason="matched",
                evidence={
                    "rule": "tenant-owner",
                    "credentials": {"api_key": "secret-value"},
                },
            )
        )

        decision = evaluate_security_decision(
            provider,
            PolicyRequest(subject="u", action="a", resource="r"),
        )

        assert decision.evidence == {
            "rule": "tenant-owner",
            "credentials": "<redacted>",
        }
        assert "secret-value" not in str(decision)

    def test_contract_does_not_embed_web_or_api_rule_terms(self):
        forbidden = {"route", "header", "cookie", "http", "web", "api", "admin"}
        field_names = {
            *PolicyRequest.__dataclass_fields__,
            *PolicyDecision.__dataclass_fields__,
        }

        assert field_names.isdisjoint(forbidden)

    def test_redaction_is_recursive_without_mutating_input(self):
        original = {
            "token": "secret-token",
            "nested": [{"password": "secret-password"}, {"safe": "ok"}],
        }

        redacted = redact_policy_context(original)

        assert redacted == {
            "token": "<redacted>",
            "nested": [{"password": "<redacted>"}, {"safe": "ok"}],
        }
        assert original["token"] == "secret-token"


class TestFakeCacheProvider:
    def test_get_miss_returns_none(self):
        cache = FakeCacheProvider()
        assert cache.get("nonexist") is None

    def test_set_then_get(self):
        cache = FakeCacheProvider()
        cache.set("k1", {"data": 42}, ttl=300)
        assert cache.get("k1") == {"data": 42}

    def test_records_calls(self):
        cache = FakeCacheProvider()
        cache.set("x", {}, 60)
        cache.get("x")
        assert len(cache.calls) == 2
        assert cache.calls[0]["method"] == "set"
        assert cache.calls[1]["method"] == "get"


class TestFakeSourceProvider:
    def test_returns_configured_documents(self):
        docs = [{"id": "d1", "kind": "news", "text": "BTC rises"}]
        src = FakeSourceProvider(documents=docs)
        result = src.fetch("BTC news", "BTC")
        assert result == docs

    def test_empty_by_default(self):
        src = FakeSourceProvider()
        assert src.fetch("q", "ETH") == []

    def test_records_calls(self):
        src = FakeSourceProvider()
        src.fetch("q1", "SOL")
        src.fetch("q2", "BNB")
        assert len(src.calls) == 2
        assert src.calls[0] == {"method": "fetch", "query": "q1", "coin": "SOL"}


class TestFakeObservabilityProvider:
    def test_emit_records_events(self):
        obs = FakeObservabilityProvider()
        obs.emit("pipeline.start", {"coin": "BTC"})
        obs.emit("pipeline.end", {"duration": 3.2})
        assert len(obs.events) == 2
        assert obs.events[0]["event"] == "pipeline.start"


class TestFakeBudgetProvider:
    def test_check_allows_by_default(self):
        budget = FakeBudgetProvider()
        assert budget.check("model-1", 100, 50) is True

    def test_check_denies_when_configured(self):
        budget = FakeBudgetProvider(allow=False)
        assert budget.check("model-1", 100, 50) is False

    def test_record_stores(self):
        budget = FakeBudgetProvider()
        budget.record("model-1", 100, 50, 0.003)
        assert len(budget.records) == 1
        assert budget.records[0]["cost_usd"] == 0.003


# ═══════════════════════════════════════════════════════════════════════════════
# R5-2: 切換 provider 改變實際 invoked adapter（spy 測試）
# ═══════════════════════════════════════════════════════════════════════════════


class TestProviderSwitching:
    """驗證切換 provider 確實改變呼叫對象。"""

    def test_switch_llm_changes_invoked(self):
        """兩個不同 LLM provider，切換後呼叫的是新 provider。"""
        provider_a = FakeLLMProvider(default_response="A")
        provider_b = FakeLLMProvider(default_response="B")

        # 使用 provider A
        ps_a = resolve_providers(llm=provider_a)
        result_a = ps_a.llm.complete("s", "p")  # type: ignore[union-attr]
        assert result_a == "A"
        assert len(provider_a.calls) == 1
        assert len(provider_b.calls) == 0

        # 切換到 provider B
        ps_b = resolve_providers(llm=provider_b)
        result_b = ps_b.llm.complete("s", "p")  # type: ignore[union-attr]
        assert result_b == "B"
        assert len(provider_b.calls) == 1
        # provider A 未被再呼叫
        assert len(provider_a.calls) == 1

    def test_switch_cache_changes_invoked(self):
        """切換 cache provider 後資料隔離。"""
        cache_a = FakeCacheProvider()
        cache_b = FakeCacheProvider()

        ps_a = resolve_providers(cache=cache_a)
        ps_a.cache.set("k", {"v": 1}, 60)  # type: ignore[union-attr]

        ps_b = resolve_providers(cache=cache_b)
        # cache B 沒有 cache A 的資料
        assert ps_b.cache.get("k") is None  # type: ignore[union-attr]

    def test_switch_source_changes_invoked(self):
        """切換 source provider 回傳不同文件。"""
        src_a = FakeSourceProvider([{"id": "a"}])
        src_b = FakeSourceProvider([{"id": "b"}])

        ps_a = resolve_providers(source=src_a)
        assert ps_a.source.fetch("q", "BTC")[0]["id"] == "a"  # type: ignore[union-attr]

        ps_b = resolve_providers(source=src_b)
        assert ps_b.source.fetch("q", "BTC")[0]["id"] == "b"  # type: ignore[union-attr]


# ═══════════════════════════════════════════════════════════════════════════════
# R4: Runtime resolver
# ═══════════════════════════════════════════════════════════════════════════════


class TestResolveProviders:
    """驗證 resolve_providers() 行為。"""

    def test_defaults_to_null_when_no_env(self):
        """無 explicit provider + 無 env 設定時 fallback 到 Null/Fake。"""
        with patch.dict(os.environ, {}, clear=True):
            env = {k: v for k, v in os.environ.items()
                   if k not in ("BEDROCK_MODEL_ID", "TRUSTFORGE_AGENTCORE", "CACHE_BACKEND")}
            with patch.dict(os.environ, env, clear=True):
                ps = resolve_providers()
                assert isinstance(ps.llm, NullLLMAdapter)
                assert isinstance(ps.cache, NullCacheAdapter)
                assert isinstance(ps.source, FakeSourceProvider)
                assert isinstance(ps.observability, FakeObservabilityProvider)
                assert isinstance(ps.budget, FakeBudgetProvider)

    def test_offline_flag_forces_null(self):
        """offline=True → Null adapters regardless of env."""
        ps = resolve_providers(offline=True)
        assert isinstance(ps.llm, NullLLMAdapter)
        assert isinstance(ps.cache, NullCacheAdapter)

    def test_explicit_provider_used(self):
        """explicit 傳入時使用指定 provider。"""
        my_llm = FakeLLMProvider(default_response="custom")
        ps = resolve_providers(llm=my_llm, offline=True)
        assert ps.llm is my_llm

    def test_resolution_metadata(self):
        """ProviderSet.resolutions 記錄每個 key 的解析結果。"""
        ps = resolve_providers(offline=True)
        assert len(ps.resolutions) == 5
        keys = [r.key for r in ps.resolutions]
        assert "llm" in keys
        assert "cache" in keys

    def test_explicit_resolution_metadata(self):
        """explicit provider 的 resolution 記錄 configured=explicit。"""
        ps = resolve_providers(llm=FakeLLMProvider(), offline=True)
        llm_res = next(r for r in ps.resolutions if r.key == "llm")
        assert llm_res.configured == "explicit"
        assert llm_res.resolved == "explicit"
        assert llm_res.fallback_reason == ""

    def test_offline_resolution_metadata(self):
        """offline=True 時 resolution 記錄 fallback_reason。"""
        ps = resolve_providers(offline=True)
        llm_res = next(r for r in ps.resolutions if r.key == "llm")
        assert llm_res.configured == "offline"
        assert llm_res.resolved == "null"
        assert "offline" in llm_res.fallback_reason

    def test_bedrock_env_resolves_adapter(self):
        """BEDROCK_MODEL_ID 設定 → BedrockLLMAdapter。"""
        env = {"BEDROCK_MODEL_ID": "us.anthropic.claude-test"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("TRUSTFORGE_AGENTCORE", None)
            ps = resolve_providers()
            assert isinstance(ps.llm, BedrockLLMAdapter)
            res = next(r for r in ps.resolutions if r.key == "llm")
            assert res.resolved == "bedrock"

    def test_agentcore_env_not_implemented(self):
        """TRUSTFORGE_AGENTCORE=1 → AgentCoreLLMAdapter (complete raises NotImplementedError)。"""
        env = {"TRUSTFORGE_AGENTCORE": "1"}
        with patch.dict(os.environ, env, clear=False):
            ps = resolve_providers()
            assert isinstance(ps.llm, AgentCoreLLMAdapter)
            with pytest.raises(NotImplementedError):
                ps.llm.complete("sys", "prompt")  # type: ignore[union-attr]


class TestPipelineProviderRuntimePath:
    """Formal pipeline path should enter through provider resolver."""

    def test_pipeline_run_invokes_resolved_llm_provider(self, monkeypatch):
        fake_llm = FakeLLMProvider(default_response="provider narrative")
        seen: dict[str, object] = {}

        def fake_resolve_providers(**kwargs):
            seen["offline"] = kwargs.get("offline")
            return ProviderSet(
                llm=fake_llm,
                resolutions=[
                    ProviderResolution(key="llm", configured="test", resolved="fake"),
                    ProviderResolution(key="cache", configured="test", resolved="fake"),
                ],
            )

        def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
            return [
                Document(
                    id="btc-price",
                    kind="price",
                    source="test-price",
                    text="BTC close rose 3%",
                    ts=1_000.0,
                )
            ]

        monkeypatch.setattr(pl, "resolve_providers", fake_resolve_providers)
        monkeypatch.setattr(pl, "collect", fake_collect)
        monkeypatch.setattr(pl, "daily_cap_exceeded", lambda: False)
        monkeypatch.setattr(pl, "try_reserve_request_budget", lambda: 0.01)
        monkeypatch.setattr(pl, "release_request_budget", lambda _reservation: None)
        monkeypatch.setattr(pl, "narrative_model_priced", lambda: True)
        monkeypatch.setattr(pl, "stance_model_priced", lambda: True)

        _report, _evidence, log = pl.run(
            "BTC",
            "分析 BTC",
            QuestionType.MULTI_SOURCE,
            data_mode="sample",
            llm_mode="bedrock",
        )

        assert seen["offline"] is False
        assert any(call["method"] == "complete" for call in fake_llm.calls)
        provider_events = [event for event in log.events if event.get("tool") == "provider.resolve"]
        assert provider_events
        assert any(event["params"]["key"] == "llm" and event["params"]["invoked"] for event in provider_events)

    def test_pipeline_composition_root_passes_resolved_provider_client(self, monkeypatch):
        """Issue #408: formal pipeline should inject the resolved provider client."""
        from trustforge.schema import Report

        fake_llm = FakeLLMProvider(default_response="provider narrative")
        injected_client = object()
        seen: dict[str, object] = {}

        def fake_resolve_providers(**kwargs):
            return ProviderSet(
                llm=fake_llm,
                resolutions=[
                    ProviderResolution(key="llm", configured="test", resolved="fake"),
                ],
            )

        def fake_client_from_provider(provider, *, offline, stance_offline):
            seen["provider"] = provider
            seen["offline"] = offline
            seen["stance_offline"] = stance_offline
            return injected_client

        def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
            return [
                Document(
                    id="btc-price",
                    kind="price",
                    source="test-price",
                    text="BTC close rose 3%",
                    ts=1_000.0,
                )
            ]

        def fake_run_agent_pipeline(query, coin, qtype, docs, *, client, log):
            seen["client"] = client
            return (
                Report(
                    coin=coin,
                    question_type=str(qtype),
                    question=query,
                    market_judgment="neutral",
                    facts=[],
                    inferences=[],
                    key_basis=[],
                    confidence=0.5,
                    limits=[],
                    could_flip=[],
                    contrarian=[],
                    generated_at="2026-07-22T00:00:00Z",
                ),
                [],
            )

        monkeypatch.setattr(pl, "resolve_providers", fake_resolve_providers)
        monkeypatch.setattr(pl, "_client_from_llm_provider", fake_client_from_provider)
        monkeypatch.setattr(pl, "run_agent_pipeline", fake_run_agent_pipeline)
        monkeypatch.setattr(pl, "collect", fake_collect)
        monkeypatch.setattr(pl, "daily_cap_exceeded", lambda: False)
        monkeypatch.setattr(pl, "try_reserve_request_budget", lambda: 0.01)
        monkeypatch.setattr(pl, "release_request_budget", lambda _reservation: None)
        monkeypatch.setattr(pl, "narrative_model_priced", lambda: True)
        monkeypatch.setattr(pl, "stance_model_priced", lambda: True)

        pl.run(
            "BTC",
            "分析 BTC",
            QuestionType.MULTI_SOURCE,
            data_mode="sample",
            llm_mode="bedrock",
        )

        assert seen["provider"] is fake_llm
        assert seen["offline"] is False
        assert seen["stance_offline"] is False
        assert seen["client"] is injected_client


# ═══════════════════════════════════════════════════════════════════════════════
# R5-3: Protocol 不接受不符合介面的物件
# ═══════════════════════════════════════════════════════════════════════════════


class TestProtocolRejectsNonConforming:
    """非符合介面的物件不通過 isinstance 檢查。"""

    def test_plain_object_not_llm(self):
        class Dummy:
            pass
        assert not isinstance(Dummy(), LLMProvider)

    def test_partial_impl_not_cache(self):
        class OnlyGet:
            def get(self, key: str) -> dict | None:
                return None
        # Missing set() method
        assert not isinstance(OnlyGet(), CacheProvider)

    def test_custom_impl_passes(self):
        """自定義完整實作通過 isinstance。"""
        class MyLLM:
            def complete(self, system: str, prompt: str) -> str:
                return "ok"
            def classify_stance(self, claim_a: str, claim_b: str) -> str:
                return "neutral"
        assert isinstance(MyLLM(), LLMProvider)


# ═══════════════════════════════════════════════════════════════════════════════
# ProviderResolution dataclass
# ═══════════════════════════════════════════════════════════════════════════════


class TestProviderResolution:
    def test_defaults(self):
        r = ProviderResolution(key="llm")
        assert r.key == "llm"
        assert r.configured == "builtin"
        assert r.resolved == "builtin"
        assert r.invoked is False
        assert r.revision == ""
        assert r.fallback_reason == ""

    def test_with_fallback(self):
        r = ProviderResolution(
            key="cache", configured="agentcore", resolved="builtin",
            fallback_reason="agentcore not available",
        )
        assert r.fallback_reason == "agentcore not available"


# ═══════════════════════════════════════════════════════════════════════════════
# R2: Spy adapter — 證明切換 provider 確實改變執行路徑
# ═══════════════════════════════════════════════════════════════════════════════


class SpyLLMAdapter:
    """Spy adapter — 記錄呼叫次數，確認切換有效。"""

    def __init__(self):
        self.call_count = 0
        self.last_args: dict | None = None

    def complete(self, system: str, prompt: str) -> str:
        self.call_count += 1
        self.last_args = {"system": system, "prompt": prompt}
        return f"[spy-response-{self.call_count}]"

    def classify_stance(self, claim_a: str, claim_b: str) -> str:
        self.call_count += 1
        self.last_args = {"claim_a": claim_a, "claim_b": claim_b}
        return "entailment"


class TestSpyAdapterSwitching:
    """R2: Spy adapter 證明 resolve_providers() 切換後實際 invoked adapter 改變。"""

    def test_spy_llm_complete_increments_call_count(self):
        """切換到 SpyLLM adapter 後，complete() 呼叫次數加 1。"""
        spy = SpyLLMAdapter()
        assert spy.call_count == 0

        ps = resolve_providers(llm=spy, offline=True)
        result = ps.llm.complete("system", "hello")  # type: ignore[union-attr]

        assert spy.call_count == 1
        assert result == "[spy-response-1]"
        assert spy.last_args == {"system": "system", "prompt": "hello"}

    def test_spy_llm_multiple_calls_accumulate(self):
        """多次 complete() 呼叫正確累計。"""
        spy = SpyLLMAdapter()
        ps = resolve_providers(llm=spy, offline=True)

        ps.llm.complete("s1", "p1")  # type: ignore[union-attr]
        ps.llm.complete("s2", "p2")  # type: ignore[union-attr]
        ps.llm.classify_stance("a", "b")  # type: ignore[union-attr]

        assert spy.call_count == 3

    def test_spy_not_invoked_when_different_provider_active(self):
        """確認切換有效：spy 未被注入時，呼叫走的不是 spy。"""
        spy = SpyLLMAdapter()

        ps = resolve_providers(offline=True)
        result = ps.llm.complete("s", "p")  # type: ignore[union-attr]

        assert spy.call_count == 0
        assert result == "[offline]"

    def test_switch_from_null_to_spy_changes_behavior(self):
        """從 Null 切換到 Spy 後，行為確實改變（非靜默 fallback）。"""
        spy = SpyLLMAdapter()

        ps_null = resolve_providers(offline=True)
        r1 = ps_null.llm.complete("s", "p")  # type: ignore[union-attr]
        assert r1 == "[offline]"
        assert spy.call_count == 0

        ps_spy = resolve_providers(llm=spy, offline=True)
        r2 = ps_spy.llm.complete("s", "p")  # type: ignore[union-attr]
        assert r2 == "[spy-response-1]"
        assert spy.call_count == 1

    def test_spy_classify_stance_proves_switch(self):
        """classify_stance 也走 spy（回 entailment 非 neutral）。"""
        spy = SpyLLMAdapter()
        ps = resolve_providers(llm=spy, offline=True)

        stance = ps.llm.classify_stance("BTC 漲", "BTC 上升")  # type: ignore[union-attr]

        assert stance == "entailment"
        assert spy.call_count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Builtin Adapter Protocol 驗證
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuiltinAdaptersProtocol:
    """Builtin adapters 通過 Protocol isinstance 檢查。"""

    def test_null_llm_isinstance(self):
        assert isinstance(NullLLMAdapter(), LLMProvider)

    def test_null_cache_isinstance(self):
        assert isinstance(NullCacheAdapter(), CacheProvider)

    def test_bedrock_llm_isinstance(self):
        class FakeBedrockClient:
            def complete(self, system, prompt):
                from trustforge.bedrock import LLMResult
                return LLMResult(text="fake", input_tokens=0, output_tokens=0, model_id=None)
            def classify_stance(self, a, b):
                return "neutral"
        adapter = BedrockLLMAdapter(client=FakeBedrockClient())
        assert isinstance(adapter, LLMProvider)

    def test_spy_llm_isinstance(self):
        assert isinstance(SpyLLMAdapter(), LLMProvider)


# ═══════════════════════════════════════════════════════════════════════════════
# R4: 失敗不靜默
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailureNotSilent:
    """R4: 初始化失敗 → 明確錯誤。"""

    def test_agentcore_without_env_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            env = {k: v for k, v in os.environ.items() if k != "TRUSTFORGE_AGENTCORE"}
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(RuntimeError, match="TRUSTFORGE_AGENTCORE"):
                    AgentCoreLLMAdapter()

    def test_agentcore_complete_not_implemented(self):
        with patch.dict(os.environ, {"TRUSTFORGE_AGENTCORE": "1"}):
            adapter = AgentCoreLLMAdapter()
            with pytest.raises(NotImplementedError):
                adapter.complete("sys", "prompt")
