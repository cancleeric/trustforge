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
from typing import Any, Literal, Protocol, runtime_checkable


# ═══════════════════════════════════════════════════════════════════════════════
# Port: generic Model Provider
# ═══════════════════════════════════════════════════════════════════════════════

ModelErrorCategory = Literal[
    "provider_unavailable",
    "rate_limited",
    "timeout",
    "bad_request",
    "auth_error",
    "safety_blocked",
    "unknown",
]


@dataclass(frozen=True)
class ModelRequest:
    """Provider-neutral model request.

    The contract deliberately has no TrustForge domain vocabulary such as
    coin, Evidence, stance, or Hermes. Domain-specific adapters may translate
    into this shape at the application boundary.
    """

    system: str
    prompt: str
    response_format: Literal["text", "json"] = "text"
    model: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class ModelUsage:
    """Token/cost usage sufficient for budget ledger accounting."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class ModelResponse:
    """Provider-neutral completion response."""

    text: str
    model: str
    provider: str
    usage: ModelUsage = field(default_factory=ModelUsage)
    structured: dict[str, Any] | list[Any] | None = None


class ModelProviderError(RuntimeError):
    """Classified model-provider failure."""

    def __init__(self, message: str, *, category: ModelErrorCategory = "unknown"):
        super().__init__(message)
        self.category = category


@runtime_checkable
class ModelProvider(Protocol):
    """Minimal provider-neutral model completion contract."""

    provider_id: str

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Run one text or structured-output completion."""
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# Port: legacy LLM Provider
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
# Port: Security Decision Provider
# ═══════════════════════════════════════════════════════════════════════════════

DecisionAction = Literal["allow", "deny"]


@dataclass(frozen=True)
class PolicyRequest:
    """Provider-neutral authorization request.

    This port carries only generic subject/action/resource/context facts. Web,
    API, role, route, and header rules belong in application adapters.
    """

    subject: str
    action: str
    resource: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyDecision:
    """Provider-neutral authorization decision."""

    action: DecisionAction
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        """True when the decision explicitly allows the request."""
        return self.action == "allow"


@runtime_checkable
class SecurityDecisionProvider(Protocol):
    """Minimal provider-neutral authorization/security decision contract."""

    provider_id: str

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        """Evaluate one authorization request."""
        ...


_SENSITIVE_POLICY_KEYS = frozenset({
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "password",
    "secret",
    "session",
    "token",
})


def _redact_policy_value(value: Any) -> Any:
    if isinstance(value, dict):
        return redact_policy_context(value)
    if isinstance(value, list):
        return [_redact_policy_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_policy_value(item) for item in value)
    return value


def redact_policy_context(context: dict[str, Any]) -> dict[str, Any]:
    """Return a copy safe for security-decision logs."""
    redacted: dict[str, Any] = {}
    for key, value in context.items():
        if key.lower() in _SENSITIVE_POLICY_KEYS:
            redacted[key] = "<redacted>"
        else:
            redacted[key] = _redact_policy_value(value)
    return redacted


def evaluate_security_decision(
    provider: SecurityDecisionProvider,
    request: PolicyRequest,
) -> PolicyDecision:
    """Evaluate a policy adapter with fail-closed error handling."""
    try:
        decision = provider.evaluate(request)
    except Exception as exc:
        return PolicyDecision(
            action="deny",
            reason=f"policy evaluation failed: {type(exc).__name__}",
            evidence={
                "provider": provider.provider_id,
                "context": redact_policy_context(request.context),
            },
        )

    return PolicyDecision(
        action=decision.action,
        reason=decision.reason,
        evidence=redact_policy_context(decision.evidence),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Port: Agent Runtime
# ═══════════════════════════════════════════════════════════════════════════════

RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
TraceLevel = Literal["debug", "info", "warning", "error"]


@dataclass(frozen=True)
class RuntimeCapability:
    """Provider-neutral runtime capability declaration."""

    name: str
    version: str = ""
    limits: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeSession:
    """Opaque agent runtime session handle."""

    session_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeToolCall:
    """Tool invocation request for an agent runtime."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    timeout_sec: float | None = None


@dataclass(frozen=True)
class RuntimeTraceEvent:
    """Structured runtime trace event."""

    event: str
    level: TraceLevel = "info"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeRun:
    """Opaque run handle and status."""

    run_id: str
    status: RunStatus
    output: dict[str, Any] | None = None


@runtime_checkable
class AgentRuntimeProvider(Protocol):
    """Minimal provider-neutral agent runtime contract."""

    runtime_id: str

    def capabilities(self) -> list[RuntimeCapability]:
        """Return runtime capabilities without starting a run."""
        ...

    def start_session(self, metadata: dict[str, Any] | None = None) -> RuntimeSession:
        """Create or attach to an agent session."""
        ...

    def start_run(
        self,
        session: RuntimeSession,
        *,
        input: dict[str, Any],
        tools: list[RuntimeToolCall] | None = None,
    ) -> RuntimeRun:
        """Start an agent run."""
        ...

    def cancel_run(self, run_id: str) -> bool:
        """Cancel a running agent run."""
        ...

    def trace(self, run_id: str, event: RuntimeTraceEvent) -> None:
        """Record a runtime trace event."""
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


class FakeModelProvider:
    """Test model provider implementing the generic ModelProvider contract."""

    provider_id = "fake"

    def __init__(
        self,
        default_text: str = "fake model response",
        *,
        default_model: str = "fake-model",
        default_structured: dict[str, Any] | list[Any] | None = None,
        usage: ModelUsage | None = None,
    ) -> None:
        self.default_text = default_text
        self.default_model = default_model
        self.default_structured = default_structured
        self.usage = usage or ModelUsage()
        self.calls: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return ModelResponse(
            text=self.default_text,
            model=request.model or self.default_model,
            provider=self.provider_id,
            usage=self.usage,
            structured=self.default_structured if request.response_format == "json" else None,
        )


class NullModelProvider:
    """Offline model provider that never calls an external service."""

    provider_id = "null"

    def complete(self, request: ModelRequest) -> ModelResponse:
        structured: dict[str, Any] | None = {} if request.response_format == "json" else None
        return ModelResponse(
            text="",
            model=request.model or "offline/null",
            provider=self.provider_id,
            usage=ModelUsage(),
            structured=structured,
        )


class FakeAgentRuntimeProvider:
    """Test fake for the generic AgentRuntimeProvider contract."""

    runtime_id = "fake-agent-runtime"

    def __init__(self, capabilities: list[RuntimeCapability] | None = None) -> None:
        self._capabilities = capabilities or [RuntimeCapability(name="session")]
        self.sessions: list[RuntimeSession] = []
        self.runs: dict[str, RuntimeRun] = {}
        self.traces: list[tuple[str, RuntimeTraceEvent]] = []
        self.cancelled: list[str] = []

    def capabilities(self) -> list[RuntimeCapability]:
        return list(self._capabilities)

    def start_session(self, metadata: dict[str, Any] | None = None) -> RuntimeSession:
        session = RuntimeSession(
            session_id=f"session-{len(self.sessions) + 1}",
            metadata=dict(metadata or {}),
        )
        self.sessions.append(session)
        return session

    def start_run(
        self,
        session: RuntimeSession,
        *,
        input: dict[str, Any],
        tools: list[RuntimeToolCall] | None = None,
    ) -> RuntimeRun:
        run = RuntimeRun(
            run_id=f"run-{len(self.runs) + 1}",
            status="running",
            output={
                "session_id": session.session_id,
                "input": dict(input),
                "tools": [tool.name for tool in tools or []],
            },
        )
        self.runs[run.run_id] = run
        return run

    def cancel_run(self, run_id: str) -> bool:
        if run_id not in self.runs:
            return False
        self.runs[run_id] = RuntimeRun(run_id=run_id, status="cancelled")
        self.cancelled.append(run_id)
        return True

    def trace(self, run_id: str, event: RuntimeTraceEvent) -> None:
        self.traces.append((run_id, event))


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


class FakeSecurityDecisionProvider:
    """Test fake for the generic SecurityDecisionProvider contract."""

    provider_id = "fake-security"

    def __init__(self, decision: PolicyDecision | None = None, *, failure: Exception | None = None) -> None:
        self.decision = decision or PolicyDecision(action="allow", reason="matched")
        self.failure = failure
        self.requests: list[PolicyRequest] = []

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return self.decision


# ═══════════════════════════════════════════════════════════════════════════════
# Builtin Adapters（production 用）
# ═══════════════════════════════════════════════════════════════════════════════


class BedrockLLMAdapter:
    """Builtin LLM adapter via bedrock.BedrockClient."""

    def __init__(self, client=None):
        if client is None:
            from .bedrock import BedrockClient
            self._client = BedrockClient()
        else:
            self._client = client

    @property
    def client(self):
        """Return the underlying Bedrock client for legacy orchestration bridges."""
        return self._client

    def complete(self, system: str, prompt: str) -> str:
        return self._client.complete(system=system, prompt=prompt).text

    def classify_stance(self, claim_a: str, claim_b: str) -> str:
        return self._client.classify_stance(claim_a, claim_b)


class SQLiteCacheAdapter:
    """Builtin Cache adapter backed by SQLite."""

    def get(self, key: str) -> dict | None:
        from .ingestion.cache import get_cache_backend, cache_get
        result = cache_get(get_cache_backend(), key)
        if result is None:
            return None
        if hasattr(result, '__iter__'):
            return {"documents": [getattr(d, '__dict__', d) for d in result]}
        return None

    def set(self, key: str, value: dict, ttl: int = 3600) -> None:
        import time as _time
        from .ingestion.cache import get_cache_backend, cache_set
        cache_set(get_cache_backend(), key, value, fetched_at=_time.time(), ttl_seconds=ttl)


class AgentCoreLLMAdapter:
    """AgentCore LLM adapter（TRUSTFORGE_AGENTCORE=1 時啟用）。"""

    def __init__(self):
        import os
        if not os.environ.get("TRUSTFORGE_AGENTCORE"):
            raise RuntimeError(
                "AgentCoreLLMAdapter requires TRUSTFORGE_AGENTCORE=1 in environment"
            )

    def complete(self, system: str, prompt: str) -> str:
        raise NotImplementedError(
            "AgentCore bridge not yet implemented — set BEDROCK_MODEL_ID to use Bedrock adapter"
        )

    def classify_stance(self, claim_a: str, claim_b: str) -> str:
        raise NotImplementedError(
            "AgentCore bridge not yet implemented — set BEDROCK_MODEL_ID to use Bedrock adapter"
        )


class NullLLMAdapter:
    """Offline LLM adapter — 回傳固定佔位字串。"""

    def complete(self, system: str, prompt: str) -> str:
        return "[offline]"

    def classify_stance(self, claim_a: str, claim_b: str) -> str:
        return "neutral"


class NullCacheAdapter:
    """Offline Cache adapter — 永遠 miss。"""

    def get(self, key: str) -> dict | None:
        return None

    def set(self, key: str, value: dict, ttl: int = 3600) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# Runtime Resolver
# ═══════════════════════════════════════════════════════════════════════════════


def _resolve_llm_from_env(*, bedrock_client_factory=None) -> tuple[LLMProvider, str]:
    """根據環境變數選擇 LLM adapter。"""
    import os

    if os.environ.get("TRUSTFORGE_AGENTCORE") == "1":
        try:
            adapter = AgentCoreLLMAdapter()
            return adapter, "agentcore"
        except Exception as exc:
            raise RuntimeError(
                f"TRUSTFORGE_AGENTCORE=1 but AgentCore init failed: {exc}"
            ) from exc

    if os.environ.get("BEDROCK_MODEL_ID"):
        if bedrock_client_factory is not None:
            return BedrockLLMAdapter(client=bedrock_client_factory()), "bedrock"
        return BedrockLLMAdapter(), "bedrock"

    return NullLLMAdapter(), "null"


def _resolve_cache_from_env() -> tuple[CacheProvider, str]:
    """根據環境變數選擇 Cache adapter。"""
    import os

    if os.environ.get("CACHE_BACKEND", "").lower() == "sqlite":
        return SQLiteCacheAdapter(), "sqlite"

    return NullCacheAdapter(), "null"


def resolve_providers(
    *,
    llm: LLMProvider | None = None,
    cache: CacheProvider | None = None,
    source: SourceProvider | None = None,
    observability: ObservabilityProvider | None = None,
    budget: BudgetProvider | None = None,
    offline: bool = False,
    bedrock_client_factory=None,
) -> ProviderSet:
    """Resolve providers for a pipeline run.

    Accepts optional explicit provider instances. For any not provided,
    resolves from environment or falls back to Null/Fake adapters.
    """
    resolutions: list[ProviderResolution] = []

    # --- LLM ---
    if llm is not None:
        resolved_llm = llm
        resolutions.append(ProviderResolution(
            key="llm", configured="explicit", resolved="explicit", invoked=False,
        ))
    elif offline:
        resolved_llm = NullLLMAdapter()
        resolutions.append(ProviderResolution(
            key="llm", configured="offline", resolved="null",
            invoked=False, fallback_reason="offline=True",
        ))
    else:
        resolved_llm, adapter_name = _resolve_llm_from_env(
            bedrock_client_factory=bedrock_client_factory
        )
        resolutions.append(ProviderResolution(
            key="llm", configured=adapter_name, resolved=adapter_name, invoked=False,
        ))

    # --- Cache ---
    if cache is not None:
        resolved_cache = cache
        resolutions.append(ProviderResolution(
            key="cache", configured="explicit", resolved="explicit", invoked=False,
        ))
    elif offline:
        resolved_cache = NullCacheAdapter()
        resolutions.append(ProviderResolution(
            key="cache", configured="offline", resolved="null",
            invoked=False, fallback_reason="offline=True",
        ))
    else:
        resolved_cache, cache_name = _resolve_cache_from_env()
        resolutions.append(ProviderResolution(
            key="cache", configured=cache_name, resolved=cache_name, invoked=False,
        ))

    # --- Source / Observability / Budget ---
    def _resolve_generic(key: str, explicit: object | None, fallback_factory: type) -> tuple[object, ProviderResolution]:
        if explicit is not None:
            return explicit, ProviderResolution(key=key, configured="explicit", resolved="explicit", invoked=False)
        instance = fallback_factory()
        return instance, ProviderResolution(key=key, configured="builtin", resolved="builtin", invoked=False, fallback_reason="no explicit provider given")

    resolved_source, r = _resolve_generic("source", source, FakeSourceProvider)
    resolutions.append(r)
    resolved_obs, r = _resolve_generic("observability", observability, FakeObservabilityProvider)
    resolutions.append(r)
    resolved_budget, r = _resolve_generic("budget", budget, FakeBudgetProvider)
    resolutions.append(r)

    return ProviderSet(
        llm=resolved_llm,  # type: ignore[arg-type]
        cache=resolved_cache,  # type: ignore[arg-type]
        source=resolved_source,  # type: ignore[arg-type]
        observability=resolved_obs,  # type: ignore[arg-type]
        budget=resolved_budget,  # type: ignore[arg-type]
        resolutions=resolutions,
    )
