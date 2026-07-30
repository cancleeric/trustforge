from __future__ import annotations

from email.message import Message
from io import BytesIO
import json
from types import MethodType

import pytest

from trustforge import web
from trustforge.analysis_plan import (
    AnalysisPlanRuntime,
    BedrockConverseTokenizer,
    HermesBedrockPlanner,
    TOKENIZER_PACKAGE,
    TOKENIZER_VERSION,
    TOKENIZER_VOCAB_HASH,
    initialize_analysis_plan_runtime_from_env,
    project_analysis_plan,
)
from trustforge.hermes_preview_control_plane import (
    MAX_OUTPUT_TOKENS,
    PreviewControlStatus,
    PreviewExecution,
    PreviewTerminalClass,
    PlannerPortFailure,
    canonical_preview_policy_digest,
)
from trustforge.preview_admission_executor import AdmissionOutcome
from tests.test_hermes_preview_control_plane import _runtime


def _plan(**changes):
    value = {
        "outcome": "ready",
        "detected_assets": ["BTC"],
        "intent_shape": "single",
        "intents": [{"label": "market context", "rationale": "Assess evidence."}],
        "source_classes": ["market_price", "news"],
        "strategy_summary": "Compare independent evidence before any conclusion.",
        "clarifications": [],
        "warnings": [],
        "confidence": {"level": "medium", "rationale": "The request is clear."},
    }
    value.update(changes)
    return value


def test_projector_accepts_open_intent_and_adds_public_provenance():
    result = project_analysis_plan(_plan())
    assert result["intents"][0]["label"] == "market context"
    assert result["provenance"] == {
        "planner": "hermes",
        "provider": "aws-bedrock",
        "policy_version": "v1",
    }


@pytest.mark.parametrize(
    "value",
    [
        _plan(secret="smuggled"),
        _plan(source_classes=["connector_name"]),
        _plan(confidence={"level": "high", "rationale": "ok", "score": 1}),
        _plan(strategy_summary="\u202eevil"),
        _plan(outcome="needs_clarification", clarifications=[]),
    ],
)
def test_projector_rejects_smuggling_unknown_sources_and_active_text(value):
    with pytest.raises(ValueError):
        project_analysis_plan(value)


def test_projector_rejects_lone_surrogate_before_http_encoding():
    with pytest.raises(ValueError):
        project_analysis_plan(_plan(strategy_summary="\ud800"))


def test_escaped_lone_surrogate_provider_output_is_safe_schema_failure():
    client = Converse(
        json.dumps(_plan(strategy_summary="\ud800"), ensure_ascii=True)
    )
    planner = HermesBedrockPlanner(client, "internal-model")
    with pytest.raises(ValueError, match="invalid planner response"):
        planner.plan(
            b'{"fixed":"payload"}',
            max_output_tokens=MAX_OUTPUT_TOKENS,
            provider_deadline=5.0,
            total_deadline=6.0,
            attempts=1,
        )


class Converse:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": self.text}],
                }
            },
            "usage": {"inputTokens": 20, "outputTokens": 30, "totalTokens": 50},
            "stopReason": "end_turn",
            "metrics": {},
            "ResponseMetadata": {},
        }

    def count_tokens(self, **kwargs):
        self.calls.append(kwargs)
        return {"inputTokens": 20}


def test_bedrock_planner_is_single_attempt_no_tools_and_strictly_projects():
    client = Converse(json.dumps(_plan()))
    planner = HermesBedrockPlanner(client, "internal-model")
    result = planner.plan(
        b'{"fixed":"payload"}',
        max_output_tokens=MAX_OUTPUT_TOKENS,
        provider_deadline=5.0,
        total_deadline=6.0,
        attempts=1,
    )
    assert result.value["outcome"] == "ready"
    assert result.input_tokens == 20
    assert len(client.calls) == 1
    assert "toolConfig" not in client.calls[0]
    assert client.calls[0]["inferenceConfig"]["maxTokens"] == 512


@pytest.mark.parametrize(
    "error,status,terminal",
    [
        ("ThrottlingException", 429, PreviewTerminalClass.PROVIDER_THROTTLE),
        ("InternalServerException", 500, PreviewTerminalClass.PROVIDER_5XX),
        ("ValidationException", 400, PreviewTerminalClass.KNOWN_PROVIDER_FAILURE),
    ],
)
def test_botocore_client_error_mapping(error, status, terminal):
    from botocore.exceptions import ClientError

    class Failed:
        def converse(self, **kwargs):
            raise ClientError(
                {
                    "Error": {"Code": error, "Message": "sensitive"},
                    "ResponseMetadata": {"HTTPStatusCode": status},
                },
                "Converse",
            )

    planner = HermesBedrockPlanner(Failed(), "internal-model")
    with pytest.raises(PlannerPortFailure) as caught:
        planner.plan(
            b'{"fixed":"payload"}',
            max_output_tokens=MAX_OUTPUT_TOKENS,
            provider_deadline=5.0,
            total_deadline=6.0,
            attempts=1,
        )
    assert caught.value.terminal_class is terminal


def test_botocore_read_timeout_maps_to_provider_timeout():
    from botocore.exceptions import ReadTimeoutError

    class Failed:
        def converse(self, **kwargs):
            raise ReadTimeoutError(endpoint_url="https://bedrock.invalid")

    planner = HermesBedrockPlanner(Failed(), "internal-model")
    with pytest.raises(PlannerPortFailure) as caught:
        planner.plan(
            b'{"fixed":"payload"}',
            max_output_tokens=MAX_OUTPUT_TOKENS,
            provider_deadline=5.0,
            total_deadline=6.0,
            attempts=1,
        )
    assert (
        caught.value.terminal_class
        is PreviewTerminalClass.PROVIDER_TIMEOUT
    )


@pytest.mark.parametrize(
    "failure,expected_status,expected_code",
    [
        ("timeout", 504, "plan_timeout"),
        ("throttle", 503, "plan_temporarily_unavailable"),
    ],
)
def test_production_adapter_control_and_http_safe_mapping(
    monkeypatch, failure, expected_status, expected_code
):
    from botocore.exceptions import ClientError, ReadTimeoutError

    class FailedConverse(Converse):
        def converse(self, **kwargs):
            if failure == "timeout":
                raise ReadTimeoutError(endpoint_url="https://bedrock.invalid")
            raise ClientError(
                {
                    "Error": {
                        "Code": "ThrottlingException",
                        "Message": "sensitive",
                    },
                    "ResponseMetadata": {"HTTPStatusCode": 429},
                },
                "Converse",
            )

    runtime = initialize_analysis_plan_runtime_from_env(
        _runtime(AdmissionOutcome.ADMITTED),
        environ=_production_env(),
        bedrock_runtime=FailedConverse(json.dumps(_plan())),
    )
    runtime.control_plane.reconcile = MethodType(
        lambda self, admission, **terminal: True,
        runtime.control_plane,
    )
    monkeypatch.setattr(web, "_ANALYSIS_PLAN_RUNTIME", runtime)
    body = b'{"question":"x","locale":"en"}'
    status, raw = web._handle_api_analysis_plan(
        _headers(body, X_Real_IP="203.0.113.9"),
        BytesIO(body),
        "127.0.0.1",
    )
    assert status == expected_status
    assert json.loads(raw)["error"]["code"] == expected_code
    assert "sensitive" not in raw


def test_count_tokens_and_converse_receive_identical_input():
    client = Converse(json.dumps(_plan()))
    tokenizer = BedrockConverseTokenizer(
        client, "internal-model", "bedrock-count-tokens", "v1", "a" * 64
    )
    planner = HermesBedrockPlanner(client, "internal-model")
    payload = b'{"fixed":"payload"}'
    assert tokenizer.count(payload) == 20
    planner.plan(
        payload,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        provider_deadline=5.0,
        total_deadline=6.0,
        attempts=1,
    )
    count_call, converse_call = client.calls
    assert count_call["input"]["converse"] == {
        "messages": converse_call["messages"]
    }


def _production_env():
    metadata = {
        "source_policy_version": "analysis-plan-source-classes-v1",
        "model_price_policy_version": "price-v1",
        "tokenizer_package": TOKENIZER_PACKAGE,
        "tokenizer_version": TOKENIZER_VERSION,
        "tokenizer_vocab_hash": TOKENIZER_VOCAB_HASH,
        "allowed_model_id": "internal-model",
        "planner_identity": "hermes-planner-port-v1",
        "input_micro_usd_per_million_tokens": 1_000,
        "output_micro_usd_per_million_tokens": 2_000,
        "valid_from_epoch": 1,
        "valid_until_epoch": 4_000_000_000,
        "policy_version": 1,
        "identity_requests_minute": 3,
        "identity_requests_day": 20,
        "identity_concurrency": 1,
        "global_concurrency": 4,
        "minute_tokens": 8_000,
        "day_tokens": 51_200,
        "minute_micro_usd": 50_000,
        "day_micro_usd": 500_000,
    }

    class Values:
        pass

    policy = Values()
    for key, value in metadata.items():
        setattr(policy, key, value)
    return {
        "TRUSTFORGE_ANALYSIS_PLAN_ORIGIN": "https://app.example",
        "TRUSTFORGE_ANALYSIS_PLAN_TRUSTED_PROXY_IPS": "127.0.0.1",
        "TRUSTFORGE_ANALYSIS_PLAN_MODEL_ID": "internal-model",
        "TRUSTFORGE_ANALYSIS_PLAN_TOKENIZER_PACKAGE": TOKENIZER_PACKAGE,
        "TRUSTFORGE_ANALYSIS_PLAN_TOKENIZER_VERSION": TOKENIZER_VERSION,
        "TRUSTFORGE_ANALYSIS_PLAN_TOKENIZER_VOCAB_HASH": TOKENIZER_VOCAB_HASH,
        "TRUSTFORGE_ANALYSIS_PLAN_PRICE_POLICY_VERSION": "price-v1",
        "TRUSTFORGE_ANALYSIS_PLAN_INPUT_MICRO_USD_PER_MTOK": "1000",
        "TRUSTFORGE_ANALYSIS_PLAN_OUTPUT_MICRO_USD_PER_MTOK": "2000",
        "TRUSTFORGE_ANALYSIS_PLAN_POLICY_VALID_FROM": "1",
        "TRUSTFORGE_ANALYSIS_PLAN_POLICY_VALID_UNTIL": "4000000000",
        "TRUSTFORGE_ANALYSIS_PLAN_POLICY_DIGEST": canonical_preview_policy_digest(
            policy
        ),
        "TRUSTFORGE_BIND_HOST": "127.0.0.1",
        "TRUSTFORGE_INGRESS_OVERWRITES_FORWARDED_HEADERS": "1",
    }


def test_production_composition_reaches_real_control_plane_without_formal_authority():
    bedrock = Converse(json.dumps(_plan()))
    runtime = initialize_analysis_plan_runtime_from_env(
        _runtime(AdmissionOutcome.ADMITTED),
        environ=_production_env(),
        bedrock_runtime=bedrock,
    )
    assert runtime.control_plane._planner.capabilities == ("plan",)
    assert not hasattr(runtime, "register_question")
    assert not hasattr(runtime, "create_job")
    # The deployment foundation test double does not mint a production
    # TerminalIntent handle; isolate that already-covered durable authority so
    # this test can prove startup composition reaches CountTokens + Converse.
    runtime.control_plane.reconcile = MethodType(
        lambda self, admission, **terminal: True,
        runtime.control_plane,
    )
    result = runtime.execute(
        web._read_analysis_plan_body(
            _headers(b'{"question":"x","locale":"en"}'),
            BytesIO(b'{"question":"x","locale":"en"}'),
        ),
        peer_ip="127.0.0.1",
        canonical_client_ip="203.0.113.9",
    )
    assert result.status is PreviewControlStatus.ADMITTED
    assert len(bedrock.calls) == 2  # one CountTokens, one Converse inference


def test_production_composition_fails_closed_without_exact_policy():
    values = _production_env()
    del values["TRUSTFORGE_ANALYSIS_PLAN_TOKENIZER_VOCAB_HASH"]
    with pytest.raises(ValueError):
        initialize_analysis_plan_runtime_from_env(
            _runtime(),
            environ=values,
            bedrock_runtime=Converse(json.dumps(_plan())),
        )


def test_development_composition_allows_4175_without_trusting_forwarded_header():
    values = _production_env()
    values.update(
        {
            "TRUSTFORGE_ANALYSIS_PLAN_DEVELOPMENT_LOOPBACK": "1",
            "TRUSTFORGE_ANALYSIS_PLAN_ORIGIN": "http://127.0.0.1:4175",
            "TRUSTFORGE_INGRESS_OVERWRITES_FORWARDED_HEADERS": "0",
        }
    )
    values.pop("TRUSTFORGE_ANALYSIS_PLAN_TRUSTED_PROXY_IPS")
    runtime = initialize_analysis_plan_runtime_from_env(
        _runtime(),
        environ=values,
        bedrock_runtime=Converse(json.dumps(_plan())),
    )
    topology = runtime.control_plane._topology
    assert topology.development_loopback is True
    assert topology.canonical_identity(
        peer_ip="127.0.0.1",
        canonical_client_ip="198.51.100.200",
    ) == b"pap1-client-ip:127.0.0.1"


def test_web_startup_default_off_does_not_compose_plan(monkeypatch):
    class Readiness:
        enabled = True

        def runtime(self):
            return _runtime()

    import trustforge.analysis_plan as analysis_plan
    import trustforge.preview_admission_deployment as deployment

    called = []
    monkeypatch.setenv("TRUSTFORGE_PREVIEW_ADMISSION_ENABLED", "1")
    monkeypatch.delenv("TRUSTFORGE_ANALYSIS_PLAN_ENABLED", raising=False)
    monkeypatch.setattr(
        deployment, "initialize_preview_runtime_from_env", lambda: Readiness()
    )
    monkeypatch.setattr(
        analysis_plan,
        "initialize_analysis_plan_runtime_from_env",
        lambda runtime: called.append(runtime),
    )
    web._initialize_preview_admission()
    assert web._ANALYSIS_PLAN_RUNTIME is None
    assert called == []


def test_web_startup_ready_installs_composed_runtime(monkeypatch):
    admission = _runtime()
    composed = object.__new__(AnalysisPlanRuntime)

    class Readiness:
        enabled = True

        def runtime(self):
            return admission

    import trustforge.analysis_plan as analysis_plan
    import trustforge.preview_admission_deployment as deployment

    monkeypatch.setenv("TRUSTFORGE_PREVIEW_ADMISSION_ENABLED", "1")
    monkeypatch.setenv("TRUSTFORGE_ANALYSIS_PLAN_ENABLED", "1")
    monkeypatch.setattr(
        deployment, "initialize_preview_runtime_from_env", lambda: Readiness()
    )
    monkeypatch.setattr(
        analysis_plan,
        "initialize_analysis_plan_runtime_from_env",
        lambda runtime: composed if runtime is admission else None,
    )
    web._initialize_preview_admission()
    assert web._ANALYSIS_PLAN_RUNTIME is composed


class _Topology:
    allowed_origin = "https://app.example"


class _Plane:
    _topology = _Topology()


class RouteRuntime:
    control_plane = _Plane()

    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, request, **identity):
        self.calls.append((request, identity))
        return self.result


def _headers(body: bytes, **values):
    headers = Message()
    headers["Content-Type"] = "application/json"
    headers["Content-Length"] = str(len(body))
    headers["Origin"] = "https://app.example"
    for key, value in values.items():
        headers[key.replace("_", "-")] = value
    return headers


def test_http_handler_strict_success_and_memory_only_request_id(monkeypatch):
    body = json.dumps(
        {
            "question": "Compare BTC evidence",
            "locale": "en",
            "asset_hints": ["BTC"],
            "client_request_id": "b2754c6e-77ef-4db3-a483-bb8769e4d001",
        }
    ).encode()
    runtime = RouteRuntime(
        PreviewExecution(PreviewControlStatus.ADMITTED, "completed", _plan())
    )
    monkeypatch.setattr(web, "_ANALYSIS_PLAN_RUNTIME", runtime)
    status, raw = web._handle_api_analysis_plan(
        _headers(body, X_Real_IP="203.0.113.9"),
        BytesIO(body),
        "127.0.0.1",
    )
    assert status == 200
    assert json.loads(raw)["data"]["outcome"] == "ready"
    request, identity = runtime.calls[0]
    assert request.client_request_id.endswith("d001")
    assert identity == {
        "peer_ip": "127.0.0.1",
        "canonical_client_ip": "203.0.113.9",
    }
    assert "client_request_id" not in raw


@pytest.mark.parametrize(
    "body",
    [
        b'{"question":"x","question":"y","locale":"en"}',
        b'{"question":"x","locale":"en","extra":true}',
        b'{"question":"x","locale":"en","asset_hints":["btc"]}',
        b'{"question":"   ","locale":"en"}',
        b'{"question":"x","locale":"zh-Hant"}',
    ],
)
def test_http_handler_rejects_strict_body_without_runtime_call(monkeypatch, body):
    runtime = RouteRuntime(
        PreviewExecution(PreviewControlStatus.ADMITTED, "completed", _plan())
    )
    monkeypatch.setattr(web, "_ANALYSIS_PLAN_RUNTIME", runtime)
    status, raw = web._handle_api_analysis_plan(
        _headers(body), BytesIO(body), "127.0.0.1"
    )
    assert status == 400
    assert json.loads(raw)["error"]["code"] == "invalid_plan_request"
    assert runtime.calls == []


def test_http_handler_maps_timeout_to_safe_504(monkeypatch):
    body = b'{"question":"x","locale":"en"}'
    runtime = RouteRuntime(
        PreviewExecution(PreviewControlStatus.UNAVAILABLE, "planner_timeout")
    )
    monkeypatch.setattr(web, "_ANALYSIS_PLAN_RUNTIME", runtime)
    status, raw = web._handle_api_analysis_plan(
        _headers(body), BytesIO(body), "127.0.0.1"
    )
    assert status == 504
    assert json.loads(raw) == {
        "ok": False,
        "error": {
            "code": "plan_timeout",
            "message": "Hermes 規劃逾時。你可以返回編輯。",
            "retryable": True,
        },
    }
