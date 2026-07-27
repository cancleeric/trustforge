"""Release-level A/B HTTP router; never imports or modifies the Trust Kernel."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Mapping, Protocol

from trustforge.agent.shadow_contracts import canonical_json


class ReleaseRoutingError(RuntimeError):
    """Routing state or endpoint is unsafe."""


@dataclass(frozen=True, slots=True)
class ReleaseEndpoint:
    release_digest: str
    base_url: str
    manifest_key_id: str

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme != "http" or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ReleaseRoutingError("release endpoint must be an HTTP origin")
        try:
            address = ipaddress.ip_address(parsed.hostname or "")
        except ValueError as exc:
            raise ReleaseRoutingError("release endpoint must use an explicit IP") from exc
        if not address.is_loopback:
            raise ReleaseRoutingError("release endpoint must be a local immutable release service")
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
        expected = "sha256:" + hashlib.sha256(
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
    ledger_head: str


class ReleaseRoutingLedger(Protocol):
    def routing_snapshot(self) -> RoutingSnapshot:
        """Re-read and authenticate the entire SSOT ledger."""

    def reserve_candidate(
        self,
        *,
        expected_head: str,
        reservation_id: str,
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


@dataclass(frozen=True, slots=True)
class RoutedResponse:
    body: bytes
    status_code: int
    release: str
    failed_over: bool


class ReleaseABRouter:
    """Data-plane router that authenticates durable state before every B route."""

    def __init__(
        self,
        ledger: ReleaseRoutingLedger,
        keyring: Mapping[str, bytes],
        *,
        pinned_a_fallback: ReleaseEndpoint,
        manifest_keyring: Mapping[str, bytes],
    ):
        self.ledger = ledger
        self.keyring = dict(keyring)
        self.pinned_a_fallback = pinned_a_fallback
        self.manifest_keyring = dict(manifest_keyring)

    def route(self, *, stable_subject: str | None, path: str = "/") -> RoutedResponse:
        snapshot: RoutingSnapshot | None = None
        reservation_id = ""
        for _attempt in range(4):
            try:
                snapshot = self.ledger.routing_snapshot()
            except Exception:
                return self._request_a_fallback(path)
            if snapshot.active != self.pinned_a_fallback:
                return self._request_a_fallback(path)
            if not self._candidate_selected(snapshot, stable_subject):
                return self._request(snapshot.active, path, release="A", failed_over=False)
            reservation_id = secrets.token_hex(16)
            try:
                snapshot = self.ledger.reserve_candidate(
                    expected_head=snapshot.ledger_head,
                    reservation_id=reservation_id,
                )
                break
            except Exception:
                snapshot = None
                continue
        if snapshot is None:
            return self._request_a_fallback(path)
        import time
        started = time.monotonic()
        try:
            response = self._request(
                snapshot.candidate,
                path,
                release="B",
                failed_over=False,
                timeout=snapshot.policy.timeout_ms / 1000,
            )
            if response.status_code >= 500:
                raise ReleaseRoutingError("candidate_http_5xx")
                self.ledger.record_candidate_result(
                    expected_head=snapshot.ledger_head,
                    reservation_id=reservation_id,
                    ok=True,
                status_code=response.status_code,
                latency_ms=(time.monotonic() - started) * 1000,
                error_kind="",
            )
            return response
        except Exception as exc:
            error_kind = (
                "timeout" if isinstance(exc, TimeoutError)
                else "candidate_http_or_transport_error"
            )
            try:
                self.ledger.record_candidate_result(
                    expected_head=snapshot.ledger_head,
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
            return self._request(snapshot.active, path, release="A", failed_over=True)

    def _candidate_selected(
        self, snapshot: RoutingSnapshot, stable_subject: str | None
    ) -> bool:
        if (
            snapshot.phase != "canary"
            or snapshot.desired_phase != "canary"
            or snapshot.activation_status != "completed"
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
        bucket = int.from_bytes(
            hmac.new(
                secret,
                b"trustforge.routing-bucket.v1\x00" + canonical_json(bucket_payload),
                hashlib.sha256,
            ).digest()[:8],
            "big",
        ) % 10_000
        return bucket < snapshot.policy.ratio_basis_points

    def _request_a_fallback(self, path: str) -> RoutedResponse:
        return self._request(
            self.pinned_a_fallback,
            path,
            release="A",
            failed_over=True,
        )

    def _request(
        self,
        endpoint: ReleaseEndpoint,
        path: str,
        *,
        release: str,
        failed_over: bool,
        timeout: float = 5.0,
    ) -> RoutedResponse:
        self._verify_endpoint_manifest(endpoint, timeout=timeout)
        if not path.startswith("/") or path.startswith("//"):
            raise ReleaseRoutingError("request path must be origin-relative")
        url = endpoint.base_url.rstrip("/") + path
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                body = response.read(4 * 1024 * 1024 + 1)
                if len(body) > 4 * 1024 * 1024:
                    raise ReleaseRoutingError("release response exceeds size limit")
                return RoutedResponse(
                    body=body,
                    status_code=int(response.status),
                    release=release,
                    failed_over=failed_over,
                )
        except urllib.error.HTTPError as exc:
            body = exc.read(4 * 1024 * 1024 + 1)
            return RoutedResponse(
                body=body,
                status_code=int(exc.code),
                release=release,
                failed_over=failed_over,
            )

    def _verify_endpoint_manifest(
        self, endpoint: ReleaseEndpoint, *, timeout: float
    ) -> None:
        url = (
            endpoint.base_url.rstrip("/")
            + "/.well-known/trustforge-release-manifest"
        )
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                raw = response.read(32_769)
        except Exception as exc:
            raise ReleaseRoutingError("release manifest probe failed") from exc
        if len(raw) > 32_768:
            raise ReleaseRoutingError("release manifest probe is oversized")
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
        secret = self.manifest_keyring.get(payload.get("key_id"))
        expected = (
            hmac.new(
                secret,
                b"trustforge.endpoint-manifest.v1\x00" + canonical_json(unsigned),
                hashlib.sha256,
            ).hexdigest()
            if secret is not None else ""
        )
        if (
            payload.get("schema") != "trustforge.endpoint-manifest/v1"
            or payload.get("artifact_digest") != endpoint.release_digest
            or payload.get("origin") != endpoint.base_url
            or payload.get("key_id") != endpoint.manifest_key_id
            or not hmac.compare_digest(str(payload.get("signature", "")), expected)
        ):
            raise ReleaseRoutingError("served release identity is not authenticated")
