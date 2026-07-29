from __future__ import annotations

import hashlib
import json
import runpy
import threading
import urllib.request
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from trustforge import release_http_canary, web
from trustforge.agent.shadow_contracts import canonical_json
from trustforge.canary_cost_budget import (
    BUDGET_DOMAIN,
    BUDGET_VERSION,
    CanaryCostBudget,
)
from trustforge.release_http_canary import (
    ACTIVATION_CONTRACT,
    ALLOWLIST_SCHEMA,
    CanaryAllowlistEntry,
    ReleaseHTTPCanaryPolicy,
    live_token_binding_digest,
    parse_canary_request,
    request_binding_digest,
    validate_analyze_compare_response,
)
from trustforge.release_router import (
    ReleaseEndpoint,
    ReleaseRoutingError,
    RoutedResponse,
    RoutingPolicy,
    RoutingSnapshot,
)


def _policy() -> RoutingPolicy:
    raw = {
        "ratio_basis_points": 100,
        "request_cap": 20,
        "timeout_ms": 500,
        "routing_key_id": "route-1",
        "ramp_id": "ramp-1",
    }
    digest = "sha256:" + hashlib.sha256(
        b"trustforge.routing-policy.v1\x00" + canonical_json(raw)
    ).hexdigest()
    return RoutingPolicy(**raw, policy_digest=digest)


def _snapshot() -> RoutingSnapshot:
    return RoutingSnapshot(
        ledger_id="ledger-1",
        phase="canary",
        desired_phase="canary",
        activation_status="completed",
        active=ReleaseEndpoint("sha256:" + "a" * 64, "http://127.0.0.1:8001", "m1"),
        candidate=ReleaseEndpoint(
            "sha256:" + "b" * 64, "http://127.0.0.1:8002", "m1"
        ),
        policy=_policy(),
        candidate_requests=0,
        consecutive_errors=0,
        stop_after_errors=2,
        control_event_head="sha256:" + "c" * 64,
        outcome_head="sha256:" + "e" * 64,
        canary_epoch="sha256:" + "d" * 64,
    )


def _entry(endpoint: str, assets: tuple[str, ...]) -> CanaryAllowlistEntry:
    kind = "comparison" if endpoint == "compare" else "multi_source"
    return _entry_for_path(
        f"/api/analyze?type={kind}&coin={','.join(assets)}"
    )


def _entry_for_path(
    path: str, *, online_stance_mode: bool = True
) -> CanaryAllowlistEntry:
    request = parse_canary_request(path)
    assert request is not None
    snapshot = _snapshot()
    return CanaryAllowlistEntry(
        trusted_identity="operator@example.test",
        endpoint=request.endpoint,
        assets=request.assets,
        query_digest=request.query_digest,
        question_type=request.question_type,
        live_mode=request.live_mode,
        sample_mode=request.sample_mode,
        data_mode=request.data_mode,
        llm_mode=request.llm_mode,
        online_stance_mode=online_stance_mode,
        active_release_digest=snapshot.active.release_digest,
        candidate_release_digest=snapshot.candidate.release_digest,
        ramp_id=snapshot.policy.ramp_id,
        control_ledger_id=snapshot.ledger_id,
        policy_digest=snapshot.policy.policy_digest,
    )


def _structural_budget() -> dict:
    return {
        "deployment_ledger_id": "ledger-1",
        "canary_epoch": "sha256:" + "d" * 64,
        "active_artifact_digest": "sha256:" + "a" * 64,
        "candidate_artifact_digest": "sha256:" + "b" * 64,
        "ramp_id": "ramp-1",
        "routing_policy_digest": _policy().policy_digest,
        "ramp_budget_id": "sha256:" + "f" * 64,
        "request_binding_digest": "sha256:" + "1" * 64,
        "model_call_cap": 2,
        "monetary_cap_microusd": 100,
        "per_request_model_calls": 1,
        "per_request_cost_microusd": 50,
        "issued_at": "2026-07-30T00:00:00+00:00",
        "expires_at": "2026-07-30T01:00:00+00:00",
        "nonce": "structural-budget",
        "key_id": "budget-1",
        "signature": "00" * 64,
        "receipt_version": BUDGET_VERSION,
    }
@pytest.mark.parametrize(
    ("path", "endpoint", "assets"),
    [
        ("/api/analyze?coin=btc", "analyze", ("BTC",)),
        (
            "/api/analyze?type=comparison&coin=BTC&coin2=ETH",
            "compare",
            ("BTC", "ETH"),
        ),
        (
            "/api/analyze?type=comparison&coin=BTC%2CETH",
            "compare",
            ("BTC", "ETH"),
        ),
    ],
)
def test_parse_real_analyze_compare_entrypoint(path, endpoint, assets):
    request = parse_canary_request(path)
    assert request is not None
    assert request.endpoint == endpoint
    assert request.assets == assets
    assert request.query_digest.startswith("sha256:")
    assert request.data_mode == "live"
    assert request.llm_mode == "off"


def test_explicit_real_one_is_equivalent_to_omitted_real_mode():
    omitted = parse_canary_request("/api/analyze?coin=BTC")
    explicit = parse_canary_request("/api/analyze?coin=BTC&real=1")
    assert omitted is not None
    assert explicit == omitted
    assert explicit.data_mode == "live"
    assert explicit.llm_mode == "off"
    assert explicit.cost_bearing is True
    assert explicit.model_calls_possible(online_stance_mode=False) is True


@pytest.mark.parametrize(
    "path",
    [
        "/api/compare?coin=BTC,ETH",
        "/api/analyze?type=unknown&coin=BTC",
        "/api/analyze?type=multi_source&coin=BTC&coin=ETH",
        "/api/analyze?type=comparison&coin=BTC&coin2=BTC",
        "/api/analyze?type=multi_source&coin=BTC,ETH",
    ],
)
def test_invalid_or_noncanonical_request_is_never_canary_eligible(path):
    assert parse_canary_request(path) is None


@pytest.mark.parametrize(
    "path",
    [
        "/api/analyze?coin=BTC&q=a&q=b",
        "/api/analyze?coin=BTC&live=0&live=1",
        "/api/analyze?coin=BTC&sample=0&sample=1",
        "/api/analyze?coin=BTC&unknown=1",
        "/api/analyze?coin=BTC&data_mode=live",
        "/api/analyze?coin=BTC&llm_mode=off",
    ],
)
def test_duplicate_or_unknown_cost_affecting_fields_are_a_only(path):
    assert parse_canary_request(path) is None


def test_query_change_changes_opaque_subject_without_exposing_raw_query():
    first_path = "/api/analyze?coin=BTC&q=private-alpha"
    second_path = "/api/analyze?coin=BTC&q=private-beta"
    first_request = parse_canary_request(first_path)
    second_request = parse_canary_request(second_path)
    assert first_request is not None and second_request is not None
    first = request_binding_digest(
        "operator@example.test",
        first_request,
        _snapshot(),
        online_stance_mode=True,
    )
    second = request_binding_digest(
        "operator@example.test",
        second_request,
        _snapshot(),
        online_stance_mode=True,
    )
    assert first != second
    assert first is not None
    assert "private-alpha" not in first
    assert "operator@example.test" not in first


def test_live_requires_exact_signed_budget_and_sample_remains_a_only():
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    live_path = "/api/analyze?coin=BTC&q=paid&live=1"
    sample_path = "/api/analyze?coin=BTC&q=demo&sample=1"
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    snapshot = _snapshot()
    live_request = parse_canary_request(live_path)
    assert live_request is not None
    assert live_request.model_calls_possible(online_stance_mode=False)
    unsigned = {
        "deployment_ledger_id": snapshot.ledger_id,
        "canary_epoch": snapshot.canary_epoch,
        "active_artifact_digest": snapshot.active.release_digest,
        "candidate_artifact_digest": snapshot.candidate.release_digest,
        "ramp_id": snapshot.policy.ramp_id,
        "routing_policy_digest": snapshot.policy.policy_digest,
        "ramp_budget_id": "sha256:" + "f" * 64,
        "request_binding_digest": request_binding_digest(
            "operator@example.test",
            live_request,
            snapshot,
            online_stance_mode=True,
            live_token_digest=live_token_binding_digest("valid-live-token"),
        ),
        "model_call_cap": 10,
        "monetary_cap_microusd": 1000,
        "per_request_model_calls": 2,
        "per_request_cost_microusd": 100,
        "issued_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "nonce": "live-budget",
        "key_id": "budget-1",
        "receipt_version": BUDGET_VERSION,
    }
    budget = CanaryCostBudget(
        **unsigned,
        signature=private.sign(BUDGET_DOMAIN + canonical_json(unsigned)).hex(),
    )
    policy = ReleaseHTTPCanaryPolicy(
        (_entry_for_path(live_path), _entry_for_path(sample_path)),
        trusted_proxy_uid=1234,
        control_ledger_head=_snapshot().control_event_head,
        budget_keyring={"budget-1": public},
        clock=lambda: now,
        live_token_validator=lambda token: token == "valid-live-token",
    )
    assert policy.routing_subject(
        trusted_identity="operator@example.test",
        path=live_path,
        snapshot=snapshot,
        live_token="valid-live-token",
    ) == (None, None)
    assert policy.routing_subject(
        trusted_identity="operator@example.test",
        path=live_path,
        snapshot=snapshot,
        cost_budget=budget,
        live_token="valid-live-token",
    )[0] is not None
    assert policy.routing_subject(
        trusted_identity="operator@example.test",
        path=sample_path,
        snapshot=snapshot,
    ) == (None, None)
    paid_disabled = policy.without_cost_bearing()
    assert paid_disabled.routing_subject(
        trusted_identity="operator@example.test",
        path=live_path,
        snapshot=snapshot,
        cost_budget=budget,
        live_token="valid-live-token",
    ) == (None, None)


def test_online_stance_requires_budget_and_binds_execution_mode():
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    path = "/api/analyze?coin=BTC"
    request = parse_canary_request(path)
    assert request is not None
    snapshot = _snapshot()
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    request_digest = request_binding_digest(
        "operator@example.test",
        request,
        snapshot,
        online_stance_mode=True,
    )
    unsigned = {
        "deployment_ledger_id": snapshot.ledger_id,
        "canary_epoch": snapshot.canary_epoch,
        "active_artifact_digest": snapshot.active.release_digest,
        "candidate_artifact_digest": snapshot.candidate.release_digest,
        "ramp_id": snapshot.policy.ramp_id,
        "routing_policy_digest": snapshot.policy.policy_digest,
        "ramp_budget_id": "sha256:" + "f" * 64,
        "request_binding_digest": request_digest,
        "model_call_cap": 2,
        "monetary_cap_microusd": 100,
        "per_request_model_calls": 1,
        "per_request_cost_microusd": 50,
        "issued_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "nonce": "online-stance-budget",
        "key_id": "budget-1",
        "receipt_version": BUDGET_VERSION,
    }
    budget = CanaryCostBudget(
        **unsigned,
        signature=private.sign(BUDGET_DOMAIN + canonical_json(unsigned)).hex(),
    )
    entry = replace(
        _entry_for_path(path, online_stance_mode=True),
        cost_budget=budget,
    )
    policy = ReleaseHTTPCanaryPolicy(
        (entry,),
        trusted_proxy_uid=1234,
        control_ledger_head=snapshot.control_event_head,
        budget_keyring={"budget-1": public},
        clock=lambda: now,
        online_stance_requested_fn=lambda: True,
    )
    assert policy.routing_subject(
        trusted_identity="operator@example.test",
        path=path,
        snapshot=snapshot,
        cost_budget=None,
    )[0] is not None
    decision = policy.routing_decision(
        trusted_identity="operator@example.test",
        path=path,
        snapshot=snapshot,
    )
    assert decision.subject is not None
    assert decision.control_head == snapshot.control_event_head
    assert decision.cost_budget == budget
    assert decision.request_binding_digest == request_digest
    assert policy.routing_admission(
        trusted_identity="operator@example.test",
        path=path,
        snapshot=snapshot,
    ) == (decision.subject, decision.control_head, budget)

    no_budget_policy = ReleaseHTTPCanaryPolicy(
        (replace(entry, cost_budget=None),),
        trusted_proxy_uid=1234,
        control_ledger_head=snapshot.control_event_head,
        budget_keyring={"budget-1": public},
        clock=lambda: now,
        online_stance_requested_fn=lambda: True,
    )
    assert no_budget_policy.routing_subject(
        trusted_identity="operator@example.test",
        path=path,
        snapshot=snapshot,
    ) == (None, None)


def test_live_token_is_validated_forwardable_and_only_digest_is_bound():
    path = "/api/analyze?coin=BTC&live=1"
    token = "valid-live-token-super-secret"
    entry = _entry_for_path(path)
    policy = ReleaseHTTPCanaryPolicy(
        (entry,),
        trusted_proxy_uid=1234,
        control_ledger_head=_snapshot().control_event_head,
        live_token_validator=lambda value: value == token,
    )
    for supplied in (None, "invalid-token"):
        assert policy.routing_subject(
            trusted_identity="operator@example.test",
            path=path,
            snapshot=_snapshot(),
            live_token=supplied,
        ) == (None, None)
    digest = live_token_binding_digest(token)
    assert token not in digest
    assert token.encode() not in canonical_json({"live_token_digest": digest})


def test_mutable_online_stance_off_cannot_create_free_admission():
    path = "/api/analyze?coin=BTC"
    mutable_process_state = [False]
    policy = ReleaseHTTPCanaryPolicy(
        (_entry_for_path(path, online_stance_mode=True),),
        trusted_proxy_uid=1234,
        control_ledger_head=_snapshot().control_event_head,
        online_stance_requested_fn=lambda: mutable_process_state[0],
    )
    for candidate_policy in (policy, policy.without_cost_bearing()):
        for state in (False, True):
            mutable_process_state[0] = state
            subject, head, budget = candidate_policy.routing_admission(
                trusted_identity="operator@example.test",
                path=path,
                snapshot=_snapshot(),
            )
            assert subject is None
            assert head is None
            assert budget is None


def test_allowlist_is_bound_to_identity_assets_releases_ramp_and_control_state():
    snapshot = _snapshot()
    policy = ReleaseHTTPCanaryPolicy(
        (_entry("analyze", ("BTC",)),),
        trusted_proxy_uid=1234,
        control_ledger_head=_snapshot().control_event_head,
    )

    subject, expected_head = policy.routing_subject(
        trusted_identity="operator@example.test",
        path="/api/analyze?coin=BTC",
        snapshot=snapshot,
    )
    assert subject is None
    assert expected_head is None

    assert policy.routing_subject(
        trusted_identity="other@example.test",
        path="/api/analyze?coin=BTC",
        snapshot=snapshot,
    ) == (None, None)
    changed = RoutingSnapshot(
        **{
            field: getattr(snapshot, field)
            for field in snapshot.__dataclass_fields__
            if field != "candidate"
        },
        candidate=ReleaseEndpoint(
            "sha256:" + "d" * 64, "http://127.0.0.1:8003", "m1"
        ),
    )
    assert policy.routing_subject(
        trusted_identity="operator@example.test",
        path="/api/analyze?coin=BTC",
        snapshot=changed,
    ) == (None, None)


def test_identity_header_is_ignored_for_non_proxy_unix_peer():
    class FakeConnection:
        def __init__(self, uid):
            self.uid = uid

        def getpeereid(self):
            return self.uid, 99

    policy = ReleaseHTTPCanaryPolicy(
        (_entry("analyze", ("BTC",)),),
        trusted_proxy_uid=1234,
        control_ledger_head=_snapshot().control_event_head,
    )
    assert (
        policy.authenticated_identity(
            FakeConnection(1234),  # type: ignore[arg-type]
            "operator@example.test",
        )
        == "operator@example.test"
    )
    assert (
        policy.authenticated_identity(
            FakeConnection(4321),  # type: ignore[arg-type]
            "spoofed@example.test",
        )
        is None
    )


def test_absent_allowlist_builds_service_with_permanently_disabled_policy(monkeypatch):
    service = runpy.run_path("scripts/release_router_service.py")
    build_router = service["build_router_with_canary_policy"]
    sentinel_router = object()
    monkeypatch.setitem(
        build_router.__globals__,
        "build_runtime_router",
        lambda **_kwargs: sentinel_router,
    )

    def absent(_cls, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(ReleaseHTTPCanaryPolicy, "load", classmethod(absent))
    router, policy = build_router()
    assert router is sentinel_router
    assert policy.trusted_proxy_uid is None
    assert policy.routing_subject(
        trusted_identity="spoofed@example.test",
        path="/api/analyze?coin=BTC",
        snapshot=_snapshot(),
    ) == (None, None)


def test_legacy_build_router_api_still_returns_only_router(monkeypatch):
    service = runpy.run_path("scripts/release_router_service.py")
    build_router = service["build_router"]
    sentinel_router = object()
    calls = []

    def runtime_router(**kwargs):
        calls.append(kwargs)
        return sentinel_router

    monkeypatch.setitem(
        build_router.__globals__,
        "build_runtime_router",
        runtime_router,
    )
    assert build_router() is sentinel_router
    assert calls == [{}]


def _allowlist_payload(entries):
    return {
        "schema": ALLOWLIST_SCHEMA,
        "activation_contract": ACTIVATION_CONTRACT,
        "trusted_proxy_uid": 1234,
        "control_ledger_head": _snapshot().control_event_head,
        "entries": entries,
    }


def test_versioned_activation_contract_allows_exact_nonempty_policy(monkeypatch):
    entry = _entry("analyze", ("BTC",))
    raw_entry = {
        field: list(value) if field == "assets" else value
        for field, value in (
            (name, getattr(entry, name)) for name in entry.__dataclass_fields__
        )
    }
    raw_entry["cost_budget"] = _structural_budget()
    monkeypatch.setattr(
        release_http_canary,
        "read_regular_file",
        lambda *_args, **_kwargs: (
            json.dumps(_allowlist_payload([raw_entry])).encode(),
            SimpleNamespace(st_uid=0, st_mode=0o100600),
        ),
    )
    policy = ReleaseHTTPCanaryPolicy.load()
    assert policy.trusted_proxy_uid == 1234
    assert policy.routing_subject(
        trusted_identity="operator@example.test",
        path="/api/analyze?coin=BTC",
        snapshot=_snapshot(),
    ) == (None, None)


def test_provisioner_payload_round_trips_shared_runtime_contract(monkeypatch):
    provisioner = runpy.run_path(
        "scripts/provision_release_http_canary_allowlist.py"
    )
    build_payload = provisioner["_payload"]
    now = datetime.now(timezone.utc)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    snapshot = replace(
        _snapshot(),
        ledger_id="ledger-provision-1",
        canary_epoch="sha256:" + "c" * 64,
    )
    path = "/api/analyze?coin=BTC"
    request = parse_canary_request(path)
    assert request is not None
    digest = request_binding_digest(
        "operator@example.test",
        request,
        snapshot,
        online_stance_mode=True,
    )
    unsigned = {
        "deployment_ledger_id": snapshot.ledger_id,
        "canary_epoch": snapshot.canary_epoch,
        "active_artifact_digest": snapshot.active.release_digest,
        "candidate_artifact_digest": snapshot.candidate.release_digest,
        "ramp_id": snapshot.policy.ramp_id,
        "routing_policy_digest": snapshot.policy.policy_digest,
        "ramp_budget_id": "sha256:" + "f" * 64,
        "request_binding_digest": digest,
        "model_call_cap": 2,
        "monetary_cap_microusd": 100,
        "per_request_model_calls": 1,
        "per_request_cost_microusd": 50,
        "issued_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "nonce": "provision-budget",
        "key_id": "cost-budget-1",
        "receipt_version": BUDGET_VERSION,
    }
    budget = CanaryCostBudget(
        **unsigned,
        signature=private.sign(BUDGET_DOMAIN + canonical_json(unsigned)).hex(),
    )
    initialized = {
        "active": {
            "release_digest": snapshot.active.release_digest,
        },
        "candidate": {
            "release_digest": snapshot.candidate.release_digest,
        },
        "policy": {
            "ramp_id": snapshot.policy.ramp_id,
            "policy_digest": snapshot.policy.policy_digest,
        },
    }
    records = [
        {
            "ledger_id": snapshot.ledger_id,
            "event_hash": "sha256:" + "1" * 64,
            "event": initialized,
        },
        {
            "ledger_id": snapshot.ledger_id,
            "event_hash": snapshot.control_event_head,
            "event": {"kind": "activation_completed"},
        },
    ]
    runtime = {
        "control_ledger_id": snapshot.ledger_id,
        "deployment_initialized_event_hash": records[0]["event_hash"],
        "a_artifact_digest": snapshot.active.release_digest,
        "b_artifact_digest": snapshot.candidate.release_digest,
        "routing_policy": initialized["policy"],
    }
    keys = {
        "canary_cost_budget_public": {
            "cost-budget-1": public.hex(),
        }
    }
    monkeypatch.setitem(
        build_payload.__globals__,
        "_nginx_worker",
        lambda *_args, **_kwargs: ("www-data", 1234),
    )
    monkeypatch.setitem(
        build_payload.__globals__,
        "_json_file",
        lambda path: runtime if path == "runtime" else keys,
    )
    monkeypatch.setitem(
        build_payload.__globals__,
        "_request",
        lambda _path: [
            {
                "trusted_identity": "operator@example.test",
                "path": path,
                "live_token_digest": "",
                "cost_budget": asdict(budget),
            }
        ],
    )
    payload = build_payload(
        SimpleNamespace(
            nginx_snippet="snippet",
            runtime="runtime",
            keys="keys",
            request="request",
        ),
        "nginx",
        records,
        b"snippet",
    )
    assert payload["schema"] == ALLOWLIST_SCHEMA
    assert payload["activation_contract"] == ACTIVATION_CONTRACT
    policy = ReleaseHTTPCanaryPolicy.from_payload(
        payload,
        budget_keyring={"cost-budget-1": public},
        clock=lambda: now,
    )
    decision = policy.routing_decision(
        trusted_identity="operator@example.test",
        path=path,
        snapshot=snapshot,
    )
    assert decision.subject is not None
    assert decision.cost_budget == budget
    assert decision.request_binding_digest == digest


@pytest.mark.parametrize(
    "raw",
    [
        b"{not-json",
        json.dumps(_allowlist_payload([])).encode(),
        json.dumps(
            {
                **_allowlist_payload([{"not": "an entry"}]),
                "activation_contract": "unknown",
            }
        ).encode(),
    ],
)
def test_present_malformed_or_empty_allowlist_never_becomes_disabled(monkeypatch, raw):
    monkeypatch.setattr(
        release_http_canary,
        "read_regular_file",
        lambda *_args, **_kwargs: (
            raw,
            SimpleNamespace(st_uid=0, st_mode=0o100600),
        ),
    )
    with pytest.raises(ReleaseRoutingError):
        ReleaseHTTPCanaryPolicy.load()


def test_present_unsafe_allowlist_fails_instead_of_becoming_disabled(monkeypatch):
    monkeypatch.setattr(
        release_http_canary,
        "read_regular_file",
        lambda *_args, **_kwargs: (
            json.dumps(_allowlist_payload([{}])).encode(),
            SimpleNamespace(st_uid=501, st_mode=0o100644),
        ),
    )
    with pytest.raises(ReleaseRoutingError, match="ownership"):
        ReleaseHTTPCanaryPolicy.load()


def _response(data: dict) -> RoutedResponse:
    return RoutedResponse(
        body=json.dumps({"ok": True, "data": data}).encode(),
        status_code=200,
        release="B",
        failed_over=False,
        headers=(("Content-Type", "application/json; charset=utf-8"),),
    )


def test_candidate_response_contract_accepts_real_analyze_and_compare_shapes():
    validate_analyze_compare_response(
        "/api/analyze?coin=BTC",
        _response({"version": "test", "report": {}, "evidence": [], "execution": {}}),
    )
    validate_analyze_compare_response(
        "/api/analyze?type=comparison&coin=BTC,ETH",
        _response(
            {
                "version": "test",
                "report_a": {},
                "evidence_a": [],
                "report_b": {},
                "evidence_b": [],
                "comparison_report": {},
                "execution": {},
            }
        ),
    )


def test_malformed_candidate_success_is_rejected_before_egress():
    with pytest.raises(ReleaseRoutingError, match="schema"):
        validate_analyze_compare_response(
            "/api/analyze?coin=BTC",
            _response({"version": "test", "report": {}, "evidence": []}),
        )


@pytest.mark.parametrize(
    "path",
    [
        "/api/analyze?coin=BTC&type=multi_source&q=k2-real-http-analyze",
        "/api/analyze?coin=BTC%2CETH&type=comparison&q=k2-real-http-compare",
    ],
)
def test_development_only_http_actual_handler_matches_candidate_contract(path):
    """Development evidence only; not nginx/AF_UNIX or production/release evidence."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}{path}", timeout=10
        ) as result:
            response = RoutedResponse(
                body=result.read(),
                status_code=result.status,
                release="B",
                failed_over=False,
                headers=tuple(result.headers.items()),
            )
        validate_analyze_compare_response(path, response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
