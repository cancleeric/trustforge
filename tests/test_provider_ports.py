"""Tests for provider ports — Protocol 驗證、fake 實作、resolver、切換測試。

Ref: Issue #386, Spec .kiro/specs/provider-ports-386.md
"""
from __future__ import annotations

import pytest

from trustforge.ports import (
    BudgetProvider,
    CacheProvider,
    FakeBudgetProvider,
    FakeCacheProvider,
    FakeLLMProvider,
    FakeObservabilityProvider,
    FakeSourceProvider,
    LLMProvider,
    ObservabilityProvider,
    ProviderResolution,
    ProviderSet,
    SourceProvider,
    resolve_providers,
)


# ═══════════════════════════════════════════════════════════════════════════════
# R5-1: runtime_checkable isinstance 驗證
# ═══════════════════════════════════════════════════════════════════════════════


class TestProtocolRuntimeCheckable:
    """每個 Protocol 的 fake 實作通過 isinstance 檢查。"""

    def test_llm_provider_isinstance(self):
        fake = FakeLLMProvider()
        assert isinstance(fake, LLMProvider)

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

    def test_defaults_to_fakes(self):
        """無 explicit provider 時 fallback 到 Fake。"""
        ps = resolve_providers()
        assert isinstance(ps.llm, FakeLLMProvider)
        assert isinstance(ps.cache, FakeCacheProvider)
        assert isinstance(ps.source, FakeSourceProvider)
        assert isinstance(ps.observability, FakeObservabilityProvider)
        assert isinstance(ps.budget, FakeBudgetProvider)

    def test_explicit_provider_used(self):
        """explicit 傳入時使用指定 provider。"""
        my_llm = FakeLLMProvider(default_response="custom")
        ps = resolve_providers(llm=my_llm)
        assert ps.llm is my_llm

    def test_resolution_metadata(self):
        """ProviderSet.resolutions 記錄每個 key 的解析結果。"""
        ps = resolve_providers()
        assert len(ps.resolutions) == 5
        keys = [r.key for r in ps.resolutions]
        assert "llm" in keys
        assert "cache" in keys
        assert "source" in keys
        assert "observability" in keys
        assert "budget" in keys

    def test_explicit_resolution_metadata(self):
        """explicit provider 的 resolution 記錄 configured=explicit。"""
        ps = resolve_providers(llm=FakeLLMProvider())
        llm_res = next(r for r in ps.resolutions if r.key == "llm")
        assert llm_res.configured == "explicit"
        assert llm_res.resolved == "explicit"
        assert llm_res.fallback_reason == ""

    def test_fallback_resolution_metadata(self):
        """fallback 時 resolution 記錄 fallback_reason。"""
        ps = resolve_providers()
        llm_res = next(r for r in ps.resolutions if r.key == "llm")
        assert llm_res.configured == "builtin"
        assert llm_res.resolved == "builtin"
        assert "no explicit" in llm_res.fallback_reason


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
