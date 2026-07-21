"""Provider ports — Trust Kernel 的外層介面定義。

本模組定義 TrustForge 各子系統的 Protocol（ports），讓 Trust Kernel
與外層透過抽象介面互動，而非直接依賴具體實作。這是 Hexagonal Architecture
（ports-and-adapters）模式的「port」層。

設計原則：
  - 所有 Protocol 使用 @runtime_checkable 以支持 isinstance 驗證
  - 每個 Protocol 對應一個關注面（LLM / Cache / Source / Observability / Budget）
  - Adapter 實作放在 adapters.py（builtin）或外部模組（agentcore）
  - Trust Kernel 不直接 import 本模組——由外層（agent/pipeline）負責注入

Ref: Issue #386, Spec .kiro/specs/provider-ports-386.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# ═══════════════════════════════════════════════════════════════════════════════
# Port: LLM Provider
# ═══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class LLMProvider(Protocol):
    """語言模型抽象。

    - complete()：通用文字生成（system prompt + user prompt）
    - classify_stance()：兩條主張的語意關係分類
      回傳值限定：'entailment' | 'contradiction' | 'neutral'
    """

    def complete(self, system: str, prompt: str) -> str:
        """Generate text completion given system and user prompt."""
        ...

    def classify_stance(self, claim_a: str, claim_b: str) -> str:
        """Classify semantic relationship between two claims.

        Returns one of: 'entailment', 'contradiction', 'neutral'.
        """
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# Port: Cache Provider
# ═══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class CacheProvider(Protocol):
    """快取存取抽象。

    - get()：讀取快取，miss 回傳 None
    - set()：寫入快取，帶 TTL（秒）
    """

    def get(self, key: str) -> dict | None:
        """Retrieve cached value by key. Returns None on miss."""
        ...

    def set(self, key: str, value: dict, ttl: int) -> None:
        """Store value with TTL in seconds."""
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# Port: Source Provider
# ═══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class SourceProvider(Protocol):
    """多源資料連接器抽象。

    fetch() 依查詢與幣種抓取文件，回傳 list[dict]。
    每個 dict 應符合 Document schema（含 id/kind/source/text/ts/meta）。
    """

    def fetch(self, query: str, coin: str) -> list[dict]:
        """Fetch documents matching query and coin."""
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# Port: Observability Provider
# ═══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class ObservabilityProvider(Protocol):
    """可觀測性抽象——發送遙測事件。"""

    def emit(self, event: str, payload: dict) -> None:
        """Emit a telemetry event with associated payload."""
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# Port: Budget Provider
# ═══════════════════════════════════════════════════════════════════════════════

@runtime_checkable
class BudgetProvider(Protocol):
    """預算控管抽象。

    - check()：判斷此次呼叫是否在預算內（True = 允許）
    - record()：記錄實際消耗
    """

    def check(self, model_id: str, input_tokens: int, output_tokens: int) -> bool:
        """Check if the call is within budget. Returns True if allowed."""
        ...

    def record(self, model_id: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        """Record actual token consumption and cost."""
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# Provider Resolution（Runtime resolver 的回傳型別）
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ProviderResolution:
    """記錄一次 provider resolve 的結果，供 execution log 追蹤。"""

    key: str                        # provider key (e.g., "llm", "cache")
    configured: str = "builtin"     # 設定檔中指定的 provider
    resolved: str = "builtin"       # 實際 resolve 結果
    invoked: bool = False           # 是否已被呼叫
    revision: str = ""              # adapter 版本/commit
    fallback_reason: str = ""       # 若 fallback 到 builtin，記錄原因


@dataclass
class ProviderSet:
    """一次執行所使用的全部 providers，由 resolve_providers() 產出。"""

    llm: LLMProvider | None = None
    cache: CacheProvider | None = None
    source: SourceProvider | None = None
    observability: ObservabilityProvider | None = None
    budget: BudgetProvider | None = None
    resolutions: list[ProviderResolution] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# Fake implementations（測試用）
# ═══════════════════════════════════════════════════════════════════════════════

class FakeLLMProvider:
    """測試用 LLM fake——回傳固定文字，記錄呼叫歷史。"""

    def __init__(self, default_response: str = "fake response", default_stance: str = "neutral"):
        self.default_response = default_response
        self.default_stance = default_stance
        self.calls: list[dict] = []

    def complete(self, system: str, prompt: str) -> str:
        self.calls.append({"method": "complete", "system": system, "prompt": prompt})
        return self.default_response

    def classify_stance(self, claim_a: str, claim_b: str) -> str:
        self.calls.append({"method": "classify_stance", "claim_a": claim_a, "claim_b": claim_b})
        return self.default_stance


class FakeCacheProvider:
    """測試用記憶體快取——dict-backed，記錄操作歷史。"""

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}
        self.calls: list[dict] = []

    def get(self, key: str) -> dict | None:
        self.calls.append({"method": "get", "key": key})
        return self._store.get(key)

    def set(self, key: str, value: dict, ttl: int) -> None:
        self.calls.append({"method": "set", "key": key, "ttl": ttl})
        self._store[key] = value


class FakeSourceProvider:
    """測試用資料源——回傳預設文件 list。"""

    def __init__(self, documents: list[dict] | None = None):
        self._documents = documents or []
        self.calls: list[dict] = []

    def fetch(self, query: str, coin: str) -> list[dict]:
        self.calls.append({"method": "fetch", "query": query, "coin": coin})
        return self._documents


class FakeObservabilityProvider:
    """測試用遙測——記錄所有 emit 事件。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: str, payload: dict) -> None:
        self.events.append({"event": event, "payload": payload})


class FakeBudgetProvider:
    """測試用預算——預設允許，記錄所有呼叫。"""

    def __init__(self, allow: bool = True):
        self._allow = allow
        self.checks: list[dict] = []
        self.records: list[dict] = []

    def check(self, model_id: str, input_tokens: int, output_tokens: int) -> bool:
        self.checks.append({"model_id": model_id, "input_tokens": input_tokens, "output_tokens": output_tokens})
        return self._allow

    def record(self, model_id: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        self.records.append({
            "model_id": model_id, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "cost_usd": cost_usd,
        })


# ═══════════════════════════════════════════════════════════════════════════════
# Runtime Resolver
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_providers(
    *,
    llm: LLMProvider | None = None,
    cache: CacheProvider | None = None,
    source: SourceProvider | None = None,
    observability: ObservabilityProvider | None = None,
    budget: BudgetProvider | None = None,
) -> ProviderSet:
    """Resolve providers for a pipeline run.

    Accepts optional explicit provider instances. For any not provided,
    falls back to builtin defaults (Fake providers for safety — real
    adapters should be explicitly constructed by the caller).

    Returns a ProviderSet with resolution metadata for execution logging.
    """
    resolutions: list[ProviderResolution] = []

    def _resolve(key: str, explicit: object | None, fallback_factory: type) -> tuple[object, ProviderResolution]:
        if explicit is not None:
            res = ProviderResolution(
                key=key, configured="explicit", resolved="explicit", invoked=False,
            )
            return explicit, res
        # Fallback to safe default
        instance = fallback_factory()
        res = ProviderResolution(
            key=key, configured="builtin", resolved="builtin",
            invoked=False, fallback_reason="no explicit provider given",
        )
        return instance, res

    resolved_llm, r = _resolve("llm", llm, FakeLLMProvider)
    resolutions.append(r)
    resolved_cache, r = _resolve("cache", cache, FakeCacheProvider)
    resolutions.append(r)
    resolved_source, r = _resolve("source", source, FakeSourceProvider)
    resolutions.append(r)
    resolved_obs, r = _resolve("observability", observability, FakeObservabilityProvider)
    resolutions.append(r)
    resolved_budget, r = _resolve("budget", budget, FakeBudgetProvider)
    resolutions.append(r)

    return ProviderSet(
        llm=resolved_llm,  # type: ignore[arg-type]
        cache=resolved_cache,  # type: ignore[arg-type]
        source=resolved_source,  # type: ignore[arg-type]
        observability=resolved_obs,  # type: ignore[arg-type]
        budget=resolved_budget,  # type: ignore[arg-type]
        resolutions=resolutions,
    )
