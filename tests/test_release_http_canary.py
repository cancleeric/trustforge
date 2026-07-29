from __future__ import annotations

import hashlib
import json
import runpy
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from trustforge import release_http_canary, web
from trustforge.agent.shadow_contracts import canonical_json
from trustforge.release_http_canary import (
    ACTIVATION_CONTRACT,
    CanaryAllowlistEntry,
    CanaryRequest,
    ReleaseHTTPCanaryPolicy,
    parse_canary_request,
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
        ledger_head="sha256:" + "c" * 64,
    )


def _entry(endpoint: str, assets: tuple[str, ...]) -> CanaryAllowlistEntry:
    snapshot = _snapshot()
    return CanaryAllowlistEntry(
        trusted_identity="operator@example.test",
        endpoint=endpoint,
        assets=assets,
        active_release_digest=snapshot.active.release_digest,
        candidate_release_digest=snapshot.candidate.release_digest,
        ramp_id=snapshot.policy.ramp_id,
        control_ledger_id=snapshot.ledger_id,
        policy_digest=snapshot.policy.policy_digest,
    )


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
    assert parse_canary_request(path) == CanaryRequest(endpoint, assets)


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


def test_allowlist_is_bound_to_identity_assets_releases_ramp_and_control_state():
    snapshot = _snapshot()
    policy = ReleaseHTTPCanaryPolicy(
        (_entry("analyze", ("BTC",)),), trusted_proxy_uid=1234
    )

    subject, expected_head = policy.routing_subject(
        trusted_identity="operator@example.test",
        path="/api/analyze?coin=BTC",
        snapshot=snapshot,
    )
    assert subject is not None and subject.startswith("sha256:")
    assert expected_head == snapshot.ledger_head

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
        (_entry("analyze", ("BTC",)),), trusted_proxy_uid=1234
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

    def absent(_cls):
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
        "schema": "trustforge.release-http-canary-allowlist/v1",
        "activation_contract": ACTIVATION_CONTRACT,
        "trusted_proxy_uid": 1234,
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
    )[0] is not None


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
