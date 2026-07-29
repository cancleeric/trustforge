"""HTTP contract and protected allowlist for release-level Analyze/Compare canaries."""

from __future__ import annotations

import hashlib
import json
import re
import socket
import struct
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from trustforge.agent.shadow_contracts import canonical_json
from trustforge.canary_cost_budget import (
    CanaryCostBudget,
    CanaryCostBudgetError,
    verify_budget,
)
from trustforge.release_router import ReleaseRoutingError, RoutedResponse, RoutingSnapshot
from trustforge.safe_fs import read_regular_file

ALLOWLIST_PATH = Path("/etc/trustforge/release-router-allowlist.json")
ACTIVATION_CONTRACT = "trustforge.release-http-canary-activation/v2"
ALLOWLIST_SCHEMA = "trustforge.release-http-canary-allowlist/v2"
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,19}\Z")
_QUERY_FIELDS = frozenset({"type", "coin", "coin2", "q", "live", "sample", "real"})
_DEFAULT_QUERY = "分析該幣種近期市場狀況"
_QUERY_DOMAIN = b"trustforge.release-http-canary-query.v1\x00"
_IDENTITY_DOMAIN = b"trustforge.release-http-canary-identity.v1\x00"
_BINDING_DOMAIN = b"trustforge.release-http-canary-request-binding.v1\x00"


@dataclass(frozen=True, slots=True)
class CanaryRequest:
    endpoint: str
    assets: tuple[str, ...]
    query_digest: str
    question_type: str
    live_mode: bool
    sample_mode: bool
    data_mode: str
    llm_mode: str

    @property
    def cost_bearing(self) -> bool:
        return self.llm_mode == "bedrock"


@dataclass(frozen=True, slots=True)
class CanaryAllowlistEntry:
    trusted_identity: str
    endpoint: str
    assets: tuple[str, ...]
    query_digest: str
    question_type: str
    live_mode: bool
    sample_mode: bool
    data_mode: str
    llm_mode: str
    active_release_digest: str
    candidate_release_digest: str
    ramp_id: str
    control_ledger_id: str
    policy_digest: str
    cost_budget: CanaryCostBudget | None = None


class ReleaseHTTPCanaryPolicy:
    """Derive an opaque routing subject only from an exact deployment-bound entry."""

    def __init__(
        self,
        entries: tuple[CanaryAllowlistEntry, ...],
        *,
        trusted_proxy_uid: int | None,
        control_ledger_head: str | None = None,
        budget_keyring: Mapping[str, bytes] | None = None,
        clock=None,
    ):
        self._entries = entries
        self.trusted_proxy_uid = trusted_proxy_uid
        self.control_ledger_head = control_ledger_head
        self._budget_keyring = dict(budget_keyring or {})
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def disabled(cls) -> "ReleaseHTTPCanaryPolicy":
        """Construct the only no-provision state: no identity can ever reach B."""
        return cls((), trusted_proxy_uid=None)

    @classmethod
    def load(
        cls,
        path: Path = ALLOWLIST_PATH,
        *,
        budget_keyring: Mapping[str, bytes] | None = None,
        clock=None,
    ) -> "ReleaseHTTPCanaryPolicy":
        raw, info = read_regular_file(path, maximum_bytes=128 * 1024)
        if info.st_uid != 0 or info.st_mode & 0o077:
            raise ReleaseRoutingError("canary allowlist ownership or mode is unsafe")
        try:
            payload = json.loads(raw)
            if set(payload) != {
                "schema",
                "activation_contract",
                "trusted_proxy_uid",
                "control_ledger_head",
                "entries",
            }:
                raise ValueError
            if payload["schema"] != ALLOWLIST_SCHEMA:
                raise ValueError
            if payload["activation_contract"] != ACTIVATION_CONTRACT:
                raise ValueError
            entries = tuple(_entry(value) for value in payload["entries"])
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
            raise ReleaseRoutingError("canary allowlist is invalid") from exc
        proxy_uid = payload["trusted_proxy_uid"]
        control_head = payload["control_ledger_head"]
        if (
            not isinstance(proxy_uid, int)
            or isinstance(proxy_uid, bool)
            or proxy_uid < 1
            or not isinstance(control_head, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", control_head) is None
            or not entries
            or len(entries) > 1_000
            or len(set(entries)) != len(entries)
        ):
            raise ReleaseRoutingError("canary allowlist entries are invalid")
        return cls(
            entries,
            trusted_proxy_uid=proxy_uid,
            control_ledger_head=control_head,
            budget_keyring=budget_keyring,
            clock=clock,
        )

    def _routing_admission(
        self,
        *,
        trusted_identity: str | None,
        path: str,
        snapshot: RoutingSnapshot,
        cost_budget: CanaryCostBudget | None = None,
    ) -> tuple[str | None, str | None, CanaryCostBudget | None]:
        if (
            self.trusted_proxy_uid is None
            or self.control_ledger_head != snapshot.control_event_head
            or trusted_identity is None
            or not (1 <= len(trusted_identity.encode()) <= 256)
        ):
            return None, None, None
        request = parse_canary_request(path)
        if request is None:
            return None, None, None
        match = CanaryAllowlistEntry(
            trusted_identity=trusted_identity,
            endpoint=request.endpoint,
            assets=request.assets,
            query_digest=request.query_digest,
            question_type=request.question_type,
            live_mode=request.live_mode,
            sample_mode=request.sample_mode,
            data_mode=request.data_mode,
            llm_mode=request.llm_mode,
            active_release_digest=snapshot.active.release_digest,
            candidate_release_digest=snapshot.candidate.release_digest,
            ramp_id=snapshot.policy.ramp_id,
            control_ledger_id=snapshot.ledger_id,
            policy_digest=snapshot.policy.policy_digest,
        )
        matching_entry = next(
            (
                entry
                for entry in self._entries
                if CanaryAllowlistEntry(
                    **{
                        name: getattr(entry, name)
                        for name in entry.__dataclass_fields__
                        if name != "cost_budget"
                    }
                )
                == match
            ),
            None,
        )
        if matching_entry is None:
            return None, None, None
        if request.sample_mode:
            return None, None, None
        binding = _request_binding(trusted_identity, request, snapshot)
        request_digest = request_binding_digest(
            trusted_identity, request, snapshot
        )
        # live/Bedrock is never admitted merely because cohort assignment
        # selected B. It additionally needs an exact signed monetary budget.
        if request.cost_bearing:
            cost_budget = cost_budget or matching_entry.cost_budget
            if cost_budget is None:
                return None, None, None
            try:
                verify_budget(
                    cost_budget,
                    keyring=self._budget_keyring,
                    now=self._clock(),
                    deployment_ledger_id=snapshot.ledger_id,
                    canary_epoch=snapshot.canary_epoch,
                    active_artifact_digest=snapshot.active.release_digest,
                    candidate_artifact_digest=snapshot.candidate.release_digest,
                    ramp_id=snapshot.policy.ramp_id,
                    routing_policy_digest=snapshot.policy.policy_digest,
                    request_binding_digest=request_digest,
                )
            except CanaryCostBudgetError:
                return None, None, None
        subject = "sha256:" + hashlib.sha256(
            b"trustforge.release-http-canary-subject.v1\x00"
            + canonical_json(binding)
        ).hexdigest()
        return subject, snapshot.control_event_head, cost_budget

    def routing_subject(
        self,
        *,
        trusted_identity: str | None,
        path: str,
        snapshot: RoutingSnapshot,
        cost_budget: CanaryCostBudget | None = None,
    ) -> tuple[str | None, str | None]:
        """Return opaque cohort subject/head without exposing identity or query."""
        subject, head, _budget = self._routing_admission(
            trusted_identity=trusted_identity,
            path=path,
            snapshot=snapshot,
            cost_budget=cost_budget,
        )
        return subject, head

    def routing_admission(
        self,
        *,
        trusted_identity: str | None,
        path: str,
        snapshot: RoutingSnapshot,
    ) -> tuple[str | None, str | None, CanaryCostBudget | None]:
        """Separate deterministic cohort identity from signed budget admission."""
        return self._routing_admission(
            trusted_identity=trusted_identity,
            path=path,
            snapshot=snapshot,
        )

    def authenticated_identity(
        self, connection: socket.socket, claimed_identity: str | None
    ) -> str | None:
        """Trust the proxy-owned header only when Unix peer credentials match."""
        if self.trusted_proxy_uid is None:
            return None
        try:
            if hasattr(connection, "getpeereid"):
                peer_uid = connection.getpeereid()[0]  # type: ignore[attr-defined]
            else:
                raw = connection.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
                )
                _pid, peer_uid, _gid = struct.unpack("3i", raw)
        except (AttributeError, OSError, struct.error):
            return None
        return claimed_identity if peer_uid == self.trusted_proxy_uid else None


def _request_binding(
    trusted_identity: str,
    request: CanaryRequest,
    snapshot: RoutingSnapshot,
) -> dict[str, object]:
    return {
        "endpoint": request.endpoint,
        "assets": list(request.assets),
        "query_digest": request.query_digest,
        "question_type": request.question_type,
        "live_mode": request.live_mode,
        "sample_mode": request.sample_mode,
        "data_mode": request.data_mode,
        "llm_mode": request.llm_mode,
        "trusted_identity_digest": _secret_digest(
            _IDENTITY_DOMAIN, trusted_identity
        ),
        "active_release_digest": snapshot.active.release_digest,
        "candidate_release_digest": snapshot.candidate.release_digest,
        "ramp_id": snapshot.policy.ramp_id,
        "control_ledger_id": snapshot.ledger_id,
        "control_head": snapshot.control_event_head or snapshot.canary_epoch,
        "policy_digest": snapshot.policy.policy_digest,
    }


def request_binding_digest(
    trusted_identity: str,
    request: CanaryRequest,
    snapshot: RoutingSnapshot,
) -> str:
    """Return the opaque exact-request identity used by signed cost budgets."""
    return "sha256:" + hashlib.sha256(
        _BINDING_DOMAIN
        + canonical_json(_request_binding(trusted_identity, request, snapshot))
    ).hexdigest()


def _entry(value: object) -> CanaryAllowlistEntry:
    if not isinstance(value, dict):
        raise ValueError
    fields = {
        "trusted_identity",
        "endpoint",
        "assets",
        "query_digest",
        "question_type",
        "live_mode",
        "sample_mode",
        "data_mode",
        "llm_mode",
        "active_release_digest",
        "candidate_release_digest",
        "ramp_id",
        "control_ledger_id",
        "policy_digest",
        "cost_budget",
    }
    if set(value) != fields or value["endpoint"] not in {"analyze", "compare"}:
        raise ValueError
    identity = value["trusted_identity"]
    assets = value["assets"]
    if not isinstance(identity, str) or not (1 <= len(identity.encode()) <= 256):
        raise ValueError
    if not isinstance(assets, list) or not assets:
        raise ValueError
    normalized = tuple(_asset(asset) for asset in assets)
    if len(normalized) != (2 if value["endpoint"] == "compare" else 1):
        raise ValueError
    if (
        not isinstance(value["live_mode"], bool)
        or not isinstance(value["sample_mode"], bool)
        or value["question_type"] not in {"multi_source", "comparison"}
        or value["data_mode"] not in {"live", "sample"}
        or value["llm_mode"] not in {"off", "bedrock"}
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["query_digest"])
    ):
        raise ValueError
    encoded_budget = value["cost_budget"]
    if encoded_budget is not None:
        if not isinstance(encoded_budget, dict):
            raise ValueError
        encoded_budget = CanaryCostBudget(**encoded_budget)
    text_fields = {
        name: value[name]
        for name in fields
        - {
            "assets",
            "endpoint",
            "trusted_identity",
            "live_mode",
            "sample_mode",
            "cost_budget",
        }
    }
    if any(not isinstance(item, str) or not item for item in text_fields.values()):
        raise ValueError
    return CanaryAllowlistEntry(
        trusted_identity=identity,
        endpoint=value["endpoint"],
        assets=normalized,
        live_mode=value["live_mode"],
        sample_mode=value["sample_mode"],
        cost_budget=encoded_budget,
        **text_fields,
    )


def _asset(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError
    normalized = value.strip().upper()
    if not _SYMBOL.fullmatch(normalized):
        raise ValueError
    return normalized


def parse_canary_request(path: str) -> CanaryRequest | None:
    """Recognize only the production Analyze/Compare GET contract."""
    try:
        parsed = urllib.parse.urlsplit(path)
        if parsed.path != "/api/analyze" or parsed.fragment:
            return None
        pairs = urllib.parse.parse_qsl(
            parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=32
        )
    except (ValueError, UnicodeError):
        return None
    values: dict[str, list[str]] = {}
    for key, value in pairs:
        values.setdefault(key, []).append(value)
    if set(values) - _QUERY_FIELDS or any(len(value) != 1 for value in values.values()):
        return None
    if any(values.get(flag, ["0"])[0] not in {"0", "1"} for flag in ("live", "sample", "real")):
        return None
    kind = values.get("type", ["multi_source"])[0]
    if kind not in {"multi_source", "comparison"}:
        return None
    coin = values.get("coin", ["BTC"])[0]
    if kind == "comparison":
        coin2 = values.get("coin2", [""])[0]
        raw_assets = coin.split(",") if "," in coin else [coin, coin2]
        if len(raw_assets) != 2:
            return None
        try:
            assets = tuple(_asset(asset) for asset in raw_assets)
        except ValueError:
            return None
        if assets[0] == assets[1]:
            return None
        endpoint = "compare"
    else:
        if values.get("coin2") or "," in coin:
            return None
        try:
            assets = (_asset(coin),)
        except ValueError:
            return None
        endpoint = "analyze"
    query = values.get("q", [_DEFAULT_QUERY])[0]
    if not query or len(query) > 1000:
        return None
    live_mode = values.get("live", ["0"])[0] == "1"
    sample_mode = values.get("sample", ["0"])[0] == "1"
    # Match web's precedence exactly: live > sample > default real-data/off-LLM.
    if live_mode:
        sample_mode = False
        data_mode, llm_mode = "live", "bedrock"
    elif sample_mode:
        data_mode, llm_mode = "sample", "off"
    else:
        data_mode, llm_mode = "live", "off"
    query_digest = _secret_digest(_QUERY_DOMAIN, query)
    return CanaryRequest(
        endpoint,
        assets,
        query_digest,
        kind,
        live_mode,
        sample_mode,
        data_mode,
        llm_mode,
    )


def _secret_digest(domain: bytes, value: str) -> str:
    return "sha256:" + hashlib.sha256(domain + value.encode("utf-8")).hexdigest()


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def validate_analyze_compare_response(path: str, response: RoutedResponse) -> None:
    """Reject malformed B responses before they can escape the router."""
    request = parse_canary_request(path)
    if request is None:
        raise ReleaseRoutingError("candidate path is outside canary contract")
    content_types = [
        value for name, value in response.headers if name.lower() == "content-type"
    ]
    if len(content_types) != 1 or "application/json" not in content_types[0].lower():
        raise ReleaseRoutingError("candidate response is not JSON")
    try:
        payload = json.loads(response.body, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReleaseRoutingError("candidate response JSON is invalid") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        raise ReleaseRoutingError("candidate response envelope is invalid")
    if 200 <= response.status_code < 300:
        if (
            set(payload) != {"ok", "data"}
            or payload["ok"] is not True
            or not isinstance(payload["data"], dict)
        ):
            raise ReleaseRoutingError("candidate success envelope is invalid")
        required = (
            {"report_a", "evidence_a", "report_b", "evidence_b", "comparison_report", "execution"}
            if request.endpoint == "compare"
            else {"report", "evidence", "execution"}
        )
        if not required.issubset(payload["data"]):
            raise ReleaseRoutingError("candidate response schema is invalid")
        data = payload["data"]
        if not isinstance(data.get("version"), str):
            raise ReleaseRoutingError("candidate response schema is invalid")
        report_fields = (
            ("report_a", "evidence_a", "report_b", "evidence_b")
            if request.endpoint == "compare"
            else ("report", "evidence")
        )
        for name in report_fields:
            expected_type = list if name.startswith("evidence") else dict
            if not isinstance(data[name], expected_type):
                raise ReleaseRoutingError("candidate response schema is invalid")
        if not isinstance(data["execution"], dict):
            raise ReleaseRoutingError("candidate response schema is invalid")
    else:
        if (
            set(payload) != {"ok", "error"}
            or payload["ok"] is not False
            or not isinstance(payload["error"], dict)
        ):
            raise ReleaseRoutingError("candidate error envelope is invalid")
        error = payload["error"]
        if (
            set(error) != {"code", "message"}
            or not isinstance(error["code"], str)
            or not isinstance(error["message"], str)
        ):
            raise ReleaseRoutingError("candidate error envelope is invalid")
