"""Strict public projection and provider adapter for Hermes plan previews.

This module has no formal analysis authority.  It cannot create jobs, read
history, call connectors, or interpret model strings as executable content.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
import time
from types import SimpleNamespace
from typing import Protocol

from .hermes_preview_control_plane import (
    ATTEMPTS,
    MAX_OUTPUT_TOKENS,
    BoundedPlannerExecutionAuthority,
    HermesPreviewControlPlane,
    PlannerPortFailure,
    PlannerResult,
    PreviewPolicy,
    PreviewRequest,
    PreviewTerminalClass,
    PreviewTopology,
    SOURCE_POLICY_VERSION,
    TrustedProxyPolicy,
    ZeroSensitiveObserver,
    canonical_preview_policy_digest,
)
from .preview_admission_deployment import PreviewAdmissionProductionRuntime


SOURCE_CLASSES = frozenset(
    {
        "market_price",
        "derivatives",
        "on_chain",
        "news",
        "social",
        "regulatory",
        "macroeconomic",
        "project_primary",
        "exchange",
        "security_incident",
        "governance",
        "research",
    }
)
TOKENIZER_PACKAGE = "aws-bedrock-count-tokens"
TOKENIZER_VERSION = "converse-messages-v1"
TOKENIZER_VOCAB_HASH = hashlib.sha256(
    b"TrustForge/BedrockCountTokens/ConverseMessages/v1"
).hexdigest()
_ASSET_RE = re.compile(r"^[A-Z0-9][A-Z0-9._:-]{0,15}$")
_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_BIDI_CONTROLS = frozenset(
    chr(value)
    for value in (
        0x061C,
        0x200E,
        0x200F,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
    )
)


class BedrockConversePort(Protocol):
    def converse(self, **kwargs: object) -> object: ...

    def count_tokens(self, **kwargs: object) -> object: ...


def _converse_messages(payload: bytes) -> list[dict[str, object]]:
    """Build the canonical token-bearing messages shared by both operations."""

    if type(payload) is not bytes:
        raise ValueError("invalid planner payload")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("invalid planner payload") from None
    return [{"role": "user", "content": [{"text": text}]}]


@dataclass(slots=True)
class BedrockConverseTokenizer:
    """Exact Bedrock CountTokens adapter over the eventual Converse input.

    CountTokens is a non-inference Bedrock API operation: it performs no model
    completion and returns no generated content.  Admission still fails closed
    if this exact count cannot be obtained.
    """

    runtime: BedrockConversePort
    model_id: str
    package: str
    version: str
    vocab_hash: str

    def __post_init__(self) -> None:
        if (
            not callable(getattr(self.runtime, "count_tokens", None))
            or any(
                type(value) is not str or not value or value != value.strip()
                for value in (
                    self.model_id,
                    self.package,
                    self.version,
                    self.vocab_hash,
                )
            )
        ):
            raise ValueError("invalid exact tokenizer")

    def count(self, payload: bytes) -> int:
        messages = _converse_messages(payload)
        response = self.runtime.count_tokens(
            modelId=self.model_id,
            input={"converse": {"messages": messages}},
        )
        if type(response) is not dict:
            raise ValueError("invalid CountTokens response")
        count = response.get("inputTokens")
        if type(count) is not int or count < 1:
            raise ValueError("invalid CountTokens response")
        return count

def _strict_json(raw: str) -> object:
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError("non-finite JSON number")
            ),
        )
    except (TypeError, json.JSONDecodeError):
        raise ValueError("invalid JSON") from None


def _plain(value: object, *, maximum: int) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(character in _BIDI_CONTROLS for character in value)
    ):
        raise ValueError("invalid model string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("invalid model string") from None
    return value


def _strict_keys(value: object, required: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != required:
        raise ValueError("invalid object fields")
    return value


def _array(value: object, maximum: int) -> list[object]:
    if type(value) is not list or len(value) > maximum:
        raise ValueError("invalid array")
    return value


def project_analysis_plan(value: object) -> dict[str, object]:
    """Validate every provider field and return only the public contract."""

    root_keys = {
        "outcome",
        "detected_assets",
        "intent_shape",
        "intents",
        "source_classes",
        "strategy_summary",
        "clarifications",
        "warnings",
        "confidence",
    }
    root = _strict_keys(value, root_keys)
    outcome = root["outcome"]
    if outcome not in {"ready", "needs_clarification"}:
        raise ValueError("invalid outcome")
    if root["intent_shape"] not in {"single", "multiple", "unknown"}:
        raise ValueError("invalid intent shape")

    assets = _array(root["detected_assets"], 8)
    if any(type(item) is not str or not _ASSET_RE.fullmatch(item) for item in assets):
        raise ValueError("invalid detected asset")
    if len(set(assets)) != len(assets):
        raise ValueError("duplicate detected asset")

    intents = _array(root["intents"], 8)
    for item in intents:
        intent = _strict_keys(item, {"label", "rationale"})
        _plain(intent["label"], maximum=64)
        _plain(intent["rationale"], maximum=240)

    classes = _array(root["source_classes"], 12)
    if any(type(item) is not str or item not in SOURCE_CLASSES for item in classes):
        raise ValueError("invalid source class")
    if len(set(classes)) != len(classes):
        raise ValueError("duplicate source class")

    _plain(root["strategy_summary"], maximum=600)
    clarifications = _array(root["clarifications"], 3)
    if outcome == "needs_clarification" and not clarifications:
        raise ValueError("missing clarification")
    seen_ids: set[str] = set()
    for item in clarifications:
        clarification = _strict_keys(item, {"id", "question", "options"})
        identifier = clarification["id"]
        if type(identifier) is not str or not _PUBLIC_ID_RE.fullmatch(identifier):
            raise ValueError("invalid clarification id")
        if identifier in seen_ids:
            raise ValueError("duplicate clarification id")
        seen_ids.add(identifier)
        _plain(clarification["question"], maximum=240)
        options = _array(clarification["options"], 6)
        for option in options:
            _plain(option, maximum=80)

    warnings = _array(root["warnings"], 8)
    for warning in warnings:
        _plain(warning, maximum=160)

    confidence = _strict_keys(root["confidence"], {"level", "rationale"})
    if confidence["level"] not in {"low", "medium", "high"}:
        raise ValueError("invalid confidence")
    _plain(confidence["rationale"], maximum=160)

    # A JSON round trip breaks aliases to provider-owned mutable structures.
    projected = json.loads(json.dumps(root, ensure_ascii=False))
    projected["provenance"] = {
        "planner": "hermes",
        "provider": "aws-bedrock",
        "policy_version": "v1",
    }
    return projected


@dataclass(slots=True)
class HermesBedrockPlanner:
    """Single-attempt, no-tool Bedrock adapter with strict output projection."""

    runtime: BedrockConversePort
    model_id: str
    identity: str = "hermes-planner-port-v1"
    capabilities: tuple[str, ...] = ("plan",)

    def __post_init__(self) -> None:
        if (
            not callable(getattr(self.runtime, "converse", None))
            or type(self.model_id) is not str
            or not self.model_id
            or self.model_id != self.model_id.strip()
        ):
            raise ValueError("invalid Hermes planner")

    def plan(
        self,
        payload: bytes,
        *,
        max_output_tokens: int,
        provider_deadline: float,
        total_deadline: float,
        attempts: int,
    ) -> PlannerResult:
        if (
            type(payload) is not bytes
            or max_output_tokens != MAX_OUTPUT_TOKENS
            or attempts != ATTEMPTS
            or type(provider_deadline) not in (int, float)
            or type(total_deadline) not in (int, float)
            or not math.isfinite(provider_deadline)
            or not math.isfinite(total_deadline)
            or provider_deadline > total_deadline
        ):
            raise ValueError("invalid planner invocation")
        try:
            response = self.runtime.converse(
                modelId=self.model_id,
                messages=_converse_messages(payload),
                inferenceConfig={
                    "maxTokens": MAX_OUTPUT_TOKENS,
                    "temperature": 0,
                },
            )
        except TimeoutError:
            raise PlannerPortFailure.provider(
                PreviewTerminalClass.PROVIDER_TIMEOUT
            ) from None
        except Exception as exc:
            raise _map_provider_failure(exc) from None
        try:
            if type(response) is not dict or set(response) - {
                "output",
                "usage",
                "stopReason",
                "metrics",
                "ResponseMetadata",
            }:
                raise ValueError
            usage = _strict_keys(
                response["usage"], {"inputTokens", "outputTokens", "totalTokens"}
            )
            input_tokens = usage["inputTokens"]
            output_tokens = usage["outputTokens"]
            total_tokens = usage["totalTokens"]
            if (
                type(input_tokens) is not int
                or type(output_tokens) is not int
                or type(total_tokens) is not int
                or input_tokens < 1
                or output_tokens < 0
                or total_tokens != input_tokens + output_tokens
            ):
                raise ValueError
            output = _strict_keys(response["output"], {"message"})
            message = _strict_keys(output["message"], {"role", "content"})
            if message["role"] != "assistant":
                raise ValueError
            content = message["content"]
            if (
                type(content) is not list
                or len(content) != 1
                or type(content[0]) is not dict
                or set(content[0]) != {"text"}
                or type(content[0]["text"]) is not str
            ):
                raise ValueError
            projected = project_analysis_plan(_strict_json(content[0]["text"]))
            return PlannerResult(projected, input_tokens, output_tokens)
        except (KeyError, ValueError):
            # Output/schema failures consume the admitted reservation but never
            # count as a provider circuit failure.
            raise ValueError("invalid planner response") from None


@dataclass(frozen=True, slots=True)
class AnalysisPlanRuntime:
    """Exact route composition; no duck-typed formal runtime may be substituted."""

    control_plane: HermesPreviewControlPlane

    def __post_init__(self) -> None:
        if type(self.control_plane) is not HermesPreviewControlPlane:
            raise ValueError("invalid analysis plan runtime")

    def execute(
        self,
        request: PreviewRequest,
        *,
        peer_ip: str,
        canonical_client_ip: str | None,
    ):
        return self.control_plane.execute(
            request,
            peer_ip=peer_ip,
            canonical_client_ip=canonical_client_ip,
        )


class _Monotonic:
    def now(self) -> float:
        return time.monotonic()


_REQUIRED_PLAN_ENV = (
    "TRUSTFORGE_ANALYSIS_PLAN_ORIGIN",
    "TRUSTFORGE_ANALYSIS_PLAN_MODEL_ID",
    "TRUSTFORGE_ANALYSIS_PLAN_TOKENIZER_PACKAGE",
    "TRUSTFORGE_ANALYSIS_PLAN_TOKENIZER_VERSION",
    "TRUSTFORGE_ANALYSIS_PLAN_TOKENIZER_VOCAB_HASH",
    "TRUSTFORGE_ANALYSIS_PLAN_PRICE_POLICY_VERSION",
    "TRUSTFORGE_ANALYSIS_PLAN_INPUT_MICRO_USD_PER_MTOK",
    "TRUSTFORGE_ANALYSIS_PLAN_OUTPUT_MICRO_USD_PER_MTOK",
    "TRUSTFORGE_ANALYSIS_PLAN_POLICY_VALID_FROM",
    "TRUSTFORGE_ANALYSIS_PLAN_POLICY_VALID_UNTIL",
    "TRUSTFORGE_ANALYSIS_PLAN_POLICY_DIGEST",
)


def _map_provider_failure(exc: Exception) -> PlannerPortFailure:
    """Map botocore failures without exposing or logging exception content."""

    try:
        from botocore.exceptions import (
            ClientError,
            ConnectTimeoutError,
            ConnectionClosedError,
            EndpointConnectionError,
            ReadTimeoutError,
        )
    except ImportError:
        ClientError = ()  # type: ignore[assignment,misc]
        ConnectTimeoutError = ConnectionClosedError = EndpointConnectionError = ()  # type: ignore[assignment,misc]
        ReadTimeoutError = ()  # type: ignore[assignment,misc]
    if isinstance(exc, (TimeoutError, ConnectTimeoutError, ReadTimeoutError)):
        terminal = PreviewTerminalClass.PROVIDER_TIMEOUT
    elif isinstance(exc, (ConnectionClosedError, EndpointConnectionError)):
        terminal = PreviewTerminalClass.PROVIDER_TRANSPORT
    elif isinstance(exc, ClientError):
        response = exc.response if type(exc.response) is dict else {}
        error = response.get("Error", {})
        metadata = response.get("ResponseMetadata", {})
        code = error.get("Code", "") if type(error) is dict else ""
        status = (
            metadata.get("HTTPStatusCode")
            if type(metadata) is dict
            else None
        )
        if code in {
            "ThrottlingException",
            "TooManyRequestsException",
            "ServiceQuotaExceededException",
        } or status == 429:
            terminal = PreviewTerminalClass.PROVIDER_THROTTLE
        elif type(status) is int and status >= 500:
            terminal = PreviewTerminalClass.PROVIDER_5XX
        elif type(status) is int and 400 <= status < 500:
            terminal = PreviewTerminalClass.KNOWN_PROVIDER_FAILURE
        else:
            terminal = PreviewTerminalClass.PROVIDER_TRANSPORT
    else:
        terminal = PreviewTerminalClass.PROVIDER_TRANSPORT
    return PlannerPortFailure.provider(terminal)


def initialize_analysis_plan_runtime_from_env(
    admission_runtime: PreviewAdmissionProductionRuntime,
    *,
    environ: dict[str, str] | None = None,
    bedrock_runtime: BedrockConversePort | None = None,
) -> AnalysisPlanRuntime:
    """Strict production composition; missing/stale metadata fails closed."""

    values = os.environ if environ is None else environ
    if type(admission_runtime) is not PreviewAdmissionProductionRuntime:
        raise ValueError("invalid preview admission runtime")
    if any(not values.get(name) for name in _REQUIRED_PLAN_ENV):
        raise ValueError("analysis plan configuration incomplete")
    if (
        values["TRUSTFORGE_ANALYSIS_PLAN_TOKENIZER_PACKAGE"]
        != TOKENIZER_PACKAGE
        or values["TRUSTFORGE_ANALYSIS_PLAN_TOKENIZER_VERSION"]
        != TOKENIZER_VERSION
        or values["TRUSTFORGE_ANALYSIS_PLAN_TOKENIZER_VOCAB_HASH"]
        != TOKENIZER_VOCAB_HASH
    ):
        raise ValueError("exact tokenizer contract mismatch")
    model_id = values["TRUSTFORGE_ANALYSIS_PLAN_MODEL_ID"]
    metadata = {
        "source_policy_version": SOURCE_POLICY_VERSION,
        "model_price_policy_version": values[
            "TRUSTFORGE_ANALYSIS_PLAN_PRICE_POLICY_VERSION"
        ],
        "tokenizer_package": values[
            "TRUSTFORGE_ANALYSIS_PLAN_TOKENIZER_PACKAGE"
        ],
        "tokenizer_version": values[
            "TRUSTFORGE_ANALYSIS_PLAN_TOKENIZER_VERSION"
        ],
        "tokenizer_vocab_hash": values[
            "TRUSTFORGE_ANALYSIS_PLAN_TOKENIZER_VOCAB_HASH"
        ],
        "allowed_model_id": model_id,
        "planner_identity": "hermes-planner-port-v1",
        "input_micro_usd_per_million_tokens": int(
            values["TRUSTFORGE_ANALYSIS_PLAN_INPUT_MICRO_USD_PER_MTOK"]
        ),
        "output_micro_usd_per_million_tokens": int(
            values["TRUSTFORGE_ANALYSIS_PLAN_OUTPUT_MICRO_USD_PER_MTOK"]
        ),
        "valid_from_epoch": int(
            values["TRUSTFORGE_ANALYSIS_PLAN_POLICY_VALID_FROM"]
        ),
        "valid_until_epoch": int(
            values["TRUSTFORGE_ANALYSIS_PLAN_POLICY_VALID_UNTIL"]
        ),
    }
    expected_digest = canonical_preview_policy_digest(
        SimpleNamespace(
            **metadata,
            policy_version=1,
            identity_requests_minute=3,
            identity_requests_day=20,
            identity_concurrency=1,
            global_concurrency=4,
            minute_tokens=8_000,
            day_tokens=51_200,
            minute_micro_usd=50_000,
            day_micro_usd=500_000,
        )
    )
    if values["TRUSTFORGE_ANALYSIS_PLAN_POLICY_DIGEST"] != expected_digest:
        raise ValueError("analysis plan policy digest mismatch")
    policy = PreviewPolicy(
        policy_digest=expected_digest,
        **metadata,
    )
    if bedrock_runtime is None:
        import boto3
        from botocore.config import Config

        bedrock_runtime = boto3.client(
            "bedrock-runtime",
            region_name=values.get("AWS_REGION", "us-east-1"),
            config=Config(
                connect_timeout=2,
                read_timeout=5,
                retries={"total_max_attempts": 1},
            ),
        )
    tokenizer = BedrockConverseTokenizer(
        bedrock_runtime,
        model_id,
        policy.tokenizer_package,
        policy.tokenizer_version,
        policy.tokenizer_vocab_hash,
    )
    planner = HermesBedrockPlanner(bedrock_runtime, model_id)
    development_loopback = (
        values.get("TRUSTFORGE_ANALYSIS_PLAN_DEVELOPMENT_LOOPBACK", "0")
        == "1"
    )
    if values.get("TRUSTFORGE_ANALYSIS_PLAN_DEVELOPMENT_LOOPBACK", "0") not in {
        "0",
        "1",
    }:
        raise ValueError("invalid development loopback flag")
    raw_proxy_ips = values.get(
        "TRUSTFORGE_ANALYSIS_PLAN_TRUSTED_PROXY_IPS",
        "127.0.0.1" if development_loopback else "",
    )
    proxy_ips = tuple(
        item.strip() for item in raw_proxy_ips.split(",") if item.strip()
    )
    if not development_loopback and not proxy_ips:
        raise ValueError("trusted proxy policy missing")
    topology = PreviewTopology(
        app_bind_host=values.get("TRUSTFORGE_BIND_HOST", ""),
        allowed_origin=values["TRUSTFORGE_ANALYSIS_PLAN_ORIGIN"],
        proxy_policy=TrustedProxyPolicy(proxy_ips),
        ingress_overwrites_forwarded_headers=(
            False
            if development_loopback
            else values.get(
                "TRUSTFORGE_INGRESS_OVERWRITES_FORWARDED_HEADERS"
            )
            == "1"
        ),
        development_loopback=development_loopback,
    )
    monotonic = _Monotonic()
    execution = BoundedPlannerExecutionAuthority(policy.global_concurrency)
    observer = ZeroSensitiveObserver(clock=monotonic)
    return AnalysisPlanRuntime(
        HermesPreviewControlPlane(
            runtime=admission_runtime,
            policy=policy,
            tokenizer=tokenizer,
            planner=planner,
            topology=topology,
            monotonic=monotonic,
            planner_execution=execution,
            observer=observer,
        )
    )
