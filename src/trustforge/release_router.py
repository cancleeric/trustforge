"""Release-level A/B HTTP router; never imports or modifies the Trust Kernel."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import secrets
import urllib.error
import urllib.parse
import urllib.request
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from trustforge.agent.shadow_contracts import canonical_json
from trustforge.canary_cost_budget import CanaryCostBudget

_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_SAFE_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-encoding",
        "content-security-policy",
        "content-type",
        "etag",
        "permissions-policy",
        "referrer-policy",
        "strict-transport-security",
        "x-content-type-options",
    }
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


class ReleaseRoutingError(RuntimeError):
    """Routing state or endpoint is unsafe."""


@dataclass(frozen=True, slots=True)
class ReleaseEndpoint:
    release_digest: str
    base_url: str
    manifest_key_id: str

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlsplit(self.base_url)
        if (
            parsed.scheme != "http"
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ReleaseRoutingError("release endpoint must be an HTTP origin")
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
        except ValueError as exc:
            raise ReleaseRoutingError(
                "release endpoint must use an explicit IP"
            ) from exc
        if not address.is_loopback:
            raise ReleaseRoutingError(
                "release endpoint must be a local immutable release service"
            )
        if parsed.port is None:
            raise ReleaseRoutingError("release endpoint requires an explicit port")
        if not self.manifest_key_id:
            raise ReleaseRoutingError("release endpoint manifest key id is required")


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    ratio_basis_points: int
    request_cap: int
    timeout_ms: int
    routing_key_id: str
    ramp_id: str
    policy_digest: str

    def __post_init__(self) -> None:
        if not 1 <= self.ratio_basis_points <= 9999:
            raise ReleaseRoutingError("canary ratio must remain limited")
        if not 1 <= self.request_cap <= 100_000:
            raise ReleaseRoutingError("request cap is invalid")
        if not 10 <= self.timeout_ms <= 10_000:
            raise ReleaseRoutingError("candidate timeout is invalid")
        expected = (
            "sha256:"
            + hashlib.sha256(
                b"trustforge.routing-policy.v1\x00"
                + canonical_json(
                    {
                        "ratio_basis_points": self.ratio_basis_points,
                        "request_cap": self.request_cap,
                        "timeout_ms": self.timeout_ms,
                        "routing_key_id": self.routing_key_id,
                        "ramp_id": self.ramp_id,
                    }
                )
            ).hexdigest()
        )
        if not hmac.compare_digest(self.policy_digest, expected):
            raise ReleaseRoutingError("routing policy digest mismatch")


@dataclass(frozen=True, slots=True)
class RoutingSnapshot:
    ledger_id: str
    phase: str
    desired_phase: str
    activation_status: str
    active: ReleaseEndpoint
    candidate: ReleaseEndpoint
    policy: RoutingPolicy
    candidate_requests: int
    consecutive_errors: int
    stop_after_errors: int
    control_event_head: str
    outcome_head: str
    candidate_blocked: bool = False
    canary_epoch: str = ""
    candidate_model_calls: int = 0
    candidate_cost_microusd: int = 0
    outstanding_cost_reservations: int = 0


class ReleaseRoutingLedger(Protocol):
    def routing_snapshot(self) -> RoutingSnapshot:
        """Re-read and authenticate the entire SSOT ledger."""

    def reserve_candidate(
        self,
        *,
        expected_head: str,
        reservation_id: str,
        cost_budget: CanaryCostBudget | None = None,
    ) -> RoutingSnapshot:
        """Atomically reserve one capped B request before any B side effect."""

    def record_candidate_result(
        self,
        *,
        expected_head: str,
        reservation_id: str,
        ok: bool,
        status_code: int,
        latency_ms: float,
        error_kind: str,
    ) -> RoutingSnapshot:
        """Atomically append outcome and, when required, automatic stop."""

    def emergency_stop(self, *, ledger_id: str, reason: str) -> None:
        """Trip the separate durable one-way stop latch."""

    def candidate_connection(
        self,
        *,
        endpoint: ReleaseEndpoint,
        reservation_id: str,
        connect_timeout: float,
    ) -> AbstractContextManager[http.client.HTTPConnection]:
        """Validate stop state and establish the bounded B socket atomically."""


@dataclass(frozen=True, slots=True)
class RoutedResponse:
    body: bytes
    status_code: int
    release: str
    failed_over: bool
    headers: tuple[tuple[str, str], ...] = ()


class ReleaseABRouter:
    """Data-plane router that authenticates durable state before every B route."""

    def __init__(
        self,
        ledger: ReleaseRoutingLedger,
        keyring: Mapping[str, bytes],
        *,
        pinned_a_fallback: ReleaseEndpoint,
        manifest_keyring: Mapping[str, bytes],
        cost_budget_keyring: Mapping[str, bytes] | None = None,
        response_validator: Callable[[str, RoutedResponse], None] | None = None,
    ):
        self.ledger = ledger
        self.keyring = dict(keyring)
        self.pinned_a_fallback = pinned_a_fallback
        self.manifest_keyring = dict(manifest_keyring)
        self.cost_budget_keyring = dict(cost_budget_keyring or {})
        self.response_validator = response_validator

    def route(
        self,
        *,
        stable_subject: str | None,
        path: str = "/healthz",
        request_headers: Mapping[str, str] | None = None,
        expected_control_head: str | None = None,
        cost_budget: CanaryCostBudget | None = None,
    ) -> RoutedResponse:
        snapshot: RoutingSnapshot | None = None
        reservation_id = ""
        for _attempt in range(4):
            try:
                snapshot = self.ledger.routing_snapshot()
            except Exception:
                return self._request_a_fallback(path, request_headers)
            if snapshot.active != self.pinned_a_fallback:
                return self._request_a_fallback(path, request_headers)
            if (
                expected_control_head is not None
                and snapshot.control_event_head != expected_control_head
            ):
                return self._request_a_fallback(path, request_headers)
            if not self._candidate_selected(snapshot, stable_subject):
                return self._request(
                    snapshot.active,
                    path,
                    release="A",
                    failed_over=False,
                    request_headers=request_headers,
                )
            reservation_id = secrets.token_hex(16)
            try:
                if cost_budget is None:
                    snapshot = self.ledger.reserve_candidate(
                        expected_head=snapshot.outcome_head,
                        reservation_id=reservation_id,
                    )
                else:
                    snapshot = self.ledger.reserve_candidate(
                        expected_head=snapshot.outcome_head,
                        reservation_id=reservation_id,
                        cost_budget=cost_budget,
                    )
                break
            except Exception:
                snapshot = None
                continue
        if snapshot is None:
            return self._request_a_fallback(path, request_headers)
        import time

        started = time.monotonic()
        try:
            with self.ledger.candidate_connection(
                endpoint=snapshot.candidate,
                reservation_id=reservation_id,
                connect_timeout=min(snapshot.policy.timeout_ms / 1000, 0.25),
            ) as connection:
                response = self._request_connected_candidate(
                    connection,
                    snapshot.candidate,
                    path,
                    timeout=snapshot.policy.timeout_ms / 1000,
                    request_headers=request_headers,
                )
            if response.status_code >= 500:
                raise ReleaseRoutingError("candidate_http_5xx")
            if self.response_validator is not None:
                self.response_validator(path, response)
            self.ledger.record_candidate_result(
                expected_head=snapshot.outcome_head,
                reservation_id=reservation_id,
                ok=True,
                status_code=response.status_code,
                latency_ms=(time.monotonic() - started) * 1000,
                error_kind="",
            )
            return response
        except Exception as exc:
            error_kind = (
                "timeout"
                if isinstance(exc, TimeoutError)
                else "candidate_http_or_transport_error"
            )
            try:
                self.ledger.record_candidate_result(
                    expected_head=snapshot.outcome_head,
                    reservation_id=reservation_id,
                    ok=False,
                    status_code=0,
                    latency_ms=(time.monotonic() - started) * 1000,
                    error_kind=error_kind,
                )
            except Exception:
                try:
                    self.ledger.emergency_stop(
                        ledger_id=snapshot.ledger_id,
                        reason="candidate_outcome_unrecordable",
                    )
                except Exception:
                    pass
            return self._request(
                snapshot.active,
                path,
                release="A",
                failed_over=True,
                request_headers=request_headers,
            )

    def _candidate_selected(
        self, snapshot: RoutingSnapshot, stable_subject: str | None
    ) -> bool:
        if (
            snapshot.phase != "canary"
            or snapshot.desired_phase != "canary"
            or snapshot.activation_status != "completed"
            or snapshot.candidate_blocked
            or not stable_subject
            or snapshot.candidate_requests >= snapshot.policy.request_cap
        ):
            return False
        secret = self.keyring.get(snapshot.policy.routing_key_id)
        if secret is None or len(secret) < 32:
            return False
        subject_mac = hmac.new(
            secret,
            b"trustforge.routing-subject.v1\x00" + stable_subject.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        bucket_payload = {
            "subject_mac": subject_mac.hex(),
            "candidate_digest": snapshot.candidate.release_digest,
            "ledger_id": snapshot.ledger_id,
            "ramp_id": snapshot.policy.ramp_id,
        }
        bucket = (
            int.from_bytes(
                hmac.new(
                    secret,
                    b"trustforge.routing-bucket.v1\x00"
                    + canonical_json(bucket_payload),
                    hashlib.sha256,
                ).digest()[:8],
                "big",
            )
            % 10_000
        )
        return bucket < snapshot.policy.ratio_basis_points

    def _request_a_fallback(
        self, path: str, request_headers: Mapping[str, str] | None
    ) -> RoutedResponse:
        return self._request(
            self.pinned_a_fallback,
            path,
            release="A",
            failed_over=True,
            request_headers=request_headers,
        )

    @staticmethod
    def _validate_path(path: str) -> None:
        parsed_path = urllib.parse.urlsplit(path)
        if (
            len(path) > 2048
            or not path.startswith("/")
            or path.startswith("//")
            or parsed_path.scheme
            or parsed_path.netloc
            or not (
                parsed_path.path == "/healthz" or parsed_path.path.startswith("/api/")
            )
        ):
            raise ReleaseRoutingError("request path is not allowlisted")

    def _request_connected_candidate(
        self,
        connection: http.client.HTTPConnection,
        endpoint: ReleaseEndpoint,
        path: str,
        *,
        timeout: float,
        request_headers: Mapping[str, str] | None,
    ) -> RoutedResponse:
        """Send only through the socket established in the atomic stop handoff."""
        self._validate_path(path)
        if connection.sock is None:
            raise ReleaseRoutingError("candidate connection was not established")
        connection.sock.settimeout(timeout)
        connection.request(
            "GET",
            "/.well-known/trustforge-release-manifest",
            headers={"Connection": "keep-alive"},
        )
        manifest_response = connection.getresponse()
        manifest_raw = manifest_response.read(32_769)
        if manifest_response.status != 200 or len(manifest_raw) > 32_768:
            raise ReleaseRoutingError("release manifest probe failed")
        self._verify_manifest_payload(endpoint, manifest_raw)
        # http.client would silently reconnect here. That would cross the stop
        # boundary, so a candidate that closed the authenticated socket fails A.
        if connection.sock is None:
            raise ReleaseRoutingError(
                "candidate closed authenticated connection before request"
            )
        headers = dict(request_headers or {})
        headers["Connection"] = "close"
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        body = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) > _MAX_RESPONSE_BYTES:
            raise ReleaseRoutingError("release response exceeds size limit")
        return RoutedResponse(
            body=body,
            status_code=int(response.status),
            release="B",
            failed_over=False,
            headers=self._safe_headers(response.headers),
        )

    def _request(
        self,
        endpoint: ReleaseEndpoint,
        path: str,
        *,
        release: str,
        failed_over: bool,
        timeout: float = 5.0,
        request_headers: Mapping[str, str] | None = None,
    ) -> RoutedResponse:
        self._verify_endpoint_manifest(endpoint, timeout=timeout)
        self._validate_path(path)
        url = endpoint.base_url.rstrip("/") + path
        request = urllib.request.Request(
            url,
            headers=dict(request_headers or {}),
            method="GET",
        )
        try:
            with _NO_REDIRECT_OPENER.open(request, timeout=timeout) as response:
                body = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(body) > _MAX_RESPONSE_BYTES:
                    raise ReleaseRoutingError("release response exceeds size limit")
                return RoutedResponse(
                    body=body,
                    status_code=int(response.status),
                    release=release,
                    failed_over=failed_over,
                    headers=self._safe_headers(response.headers),
                )
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                raise ReleaseRoutingError("release redirect is forbidden") from exc
            body = exc.read(_MAX_RESPONSE_BYTES + 1)
            if len(body) > _MAX_RESPONSE_BYTES:
                raise ReleaseRoutingError("release response exceeds size limit")
            return RoutedResponse(
                body=body,
                status_code=int(exc.code),
                release=release,
                failed_over=failed_over,
                headers=self._safe_headers(exc.headers),
            )

    @staticmethod
    def _safe_headers(headers) -> tuple[tuple[str, str], ...]:
        result = []
        for name, value in headers.items():
            lowered = name.lower()
            if lowered not in _SAFE_RESPONSE_HEADERS or "\n" in value or "\r" in value:
                continue
            if lowered == "content-encoding" and value.lower() not in {
                "br",
                "gzip",
                "identity",
            }:
                continue
            result.append((name, value))
        return tuple(result)

    def _verify_endpoint_manifest(
        self, endpoint: ReleaseEndpoint, *, timeout: float
    ) -> None:
        url = endpoint.base_url.rstrip("/") + "/.well-known/trustforge-release-manifest"
        try:
            with _NO_REDIRECT_OPENER.open(url, timeout=timeout) as response:
                raw = response.read(32_769)
        except Exception as exc:
            raise ReleaseRoutingError("release manifest probe failed") from exc
        if len(raw) > 32_768:
            raise ReleaseRoutingError("release manifest probe is oversized")
        self._verify_manifest_payload(endpoint, raw)

    def _verify_manifest_payload(self, endpoint: ReleaseEndpoint, raw: bytes) -> None:
        try:
            payload = __import__("json").loads(raw)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ReleaseRoutingError("release manifest probe is invalid") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "schema",
            "artifact_digest",
            "origin",
            "key_id",
            "signature",
        }:
            raise ReleaseRoutingError("release manifest probe schema is invalid")
        unsigned = {key: value for key, value in payload.items() if key != "signature"}
        public_key = self.manifest_keyring.get(payload.get("key_id"))
        if (
            payload.get("schema") != "trustforge.endpoint-manifest/v1"
            or payload.get("artifact_digest") != endpoint.release_digest
            or payload.get("origin") != endpoint.base_url
            or payload.get("key_id") != endpoint.manifest_key_id
        ):
            raise ReleaseRoutingError("served release identity is not authenticated")
        try:
            if public_key is None:
                raise InvalidSignature
            Ed25519PublicKey.from_public_bytes(public_key).verify(
                bytes.fromhex(str(payload.get("signature", ""))),
                b"trustforge.endpoint-manifest.v1\x00" + canonical_json(unsigned),
            )
        except (InvalidSignature, ValueError) as exc:
            raise ReleaseRoutingError(
                "served release manifest signature is invalid"
            ) from exc
