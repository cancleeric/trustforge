#!/usr/bin/env python3
"""Root-only atomic provisioning for the release HTTP canary allowlist."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from trustforge.agent.shadow_contracts import canonical_json
from trustforge.canary_cost_budget import (
    CanaryCostBudget,
    CanaryCostBudgetError,
    verify_budget,
)
from trustforge.release_http_canary import (
    ACTIVATION_CONTRACT,
    ALLOWLIST_SCHEMA,
    ReleaseHTTPCanaryPolicy,
    parse_canary_request,
    request_binding_digest,
)
from trustforge.safe_fs import (
    pinned_directory,
    read_regular_file,
    read_regular_file_at,
    write_atomic_at,
)

try:
    from scripts.verify_release_install_evidence import (
        _control_ledger,
        _json_file,
    )
except ModuleNotFoundError:  # Direct execution adds scripts/, not the repository root.
    from verify_release_install_evidence import _control_ledger, _json_file

REQUEST_SCHEMA = "trustforge.release-http-canary-request/v2"
DEFAULT_COORDINATION_LOCK = Path("/run/trustforge-release-control/coordination.lock")
_LOCATION = "location ~ ^/(healthz|api/)"


def _without_comments(config: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in config.splitlines())


def _balanced_block(config: str, opening: int) -> tuple[str, int]:
    depth = 0
    quoted: str | None = None
    escaped = False
    for offset, character in enumerate(config[opening:], opening):
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if quoted is not None:
            if character == quoted:
                quoted = None
            continue
        if character in {'"', "'"}:
            quoted = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return config[opening : offset + 1], offset + 1
    raise SystemExit("release evidence BLOCK: nginx block is unterminated")


def _brace_delta(line: str) -> int:
    delta = 0
    quoted: str | None = None
    escaped = False
    for character in line:
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif quoted is not None:
            if character == quoted:
                quoted = None
        elif character in {'"', "'"}:
            quoted = character
        elif character == "{":
            delta += 1
        elif character == "}":
            delta -= 1
    return delta


def _direct_scope(block: str) -> str:
    """Return only directives at the immediate depth of one nginx block."""
    depth = 0
    direct: list[str] = []
    for line in block.splitlines():
        before = depth
        depth += _brace_delta(line)
        if before == 1:
            direct.append(line)
    if depth != 0:
        raise SystemExit("release evidence BLOCK: nginx scope is unbalanced")
    return "\n".join(direct)


def _nginx_sections(config: str) -> dict[str, str]:
    markers = list(re.finditer(r"(?m)^# configuration file ([^:\n]+):\s*$", config))
    sections: dict[str, str] = {}
    for index, marker in enumerate(markers):
        path = marker.group(1)
        if path in sections:
            raise SystemExit("release evidence BLOCK: nginx source is duplicated")
        start = marker.end()
        if start < len(config) and config[start] == "\n":
            start += 1
        end = markers[index + 1].start() if index + 1 < len(markers) else len(config)
        sections[path] = config[start:end].rstrip("\n") + "\n"
    return sections


def _verify_nginx_source(source_path: str, source: str) -> None:
    path = Path(source_path)
    if not path.is_absolute():
        raise SystemExit("release evidence BLOCK: nginx source is not absolute")
    raw, info = read_regular_file(path, maximum_bytes=128 * 1024)
    if (
        raw != source.encode()
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o022
        or info.st_nlink != 1
    ):
        raise SystemExit("release evidence BLOCK: nginx source is unsafe")


def _authenticated_include(
    sections: dict[str, str], snippet_path: Path, *, verify_source_files: bool
) -> None:
    include = re.compile(rf"(?m)^\s*include\s+{re.escape(str(snippet_path))}\s*;\s*$")
    for source_path, source in sections.items():
        stripped = _without_comments(source)
        for match in re.finditer(r"(?m)^\s*server\s*\{", stripped):
            opening = stripped.find("{", match.start())
            block, _end = _balanced_block(stripped, opening)
            direct = _direct_scope(block)
            auth_values = re.findall(
                r"(?m)^\s*(?:auth_basic|auth_request)\s+([^;]+)\s*;\s*$",
                direct,
            )
            if include.search(direct) and any(
                value.strip().lower() != "off" for value in auth_values
            ):
                if verify_source_files:
                    _verify_nginx_source(source_path, source)
                return
    raise SystemExit(
        "release evidence BLOCK: router snippet is not included by an auth server"
    )


def _nginx_worker(
    config: str,
    *,
    snippet_path: Path,
    expected_snippet: bytes,
    verify_source_files: bool = False,
) -> tuple[str, int]:
    sections = _nginx_sections(config)
    loaded = sections.get(str(snippet_path))
    try:
        loaded_bytes = loaded.encode() if loaded is not None else b""
    except UnicodeEncodeError as exc:
        raise SystemExit("release evidence BLOCK: nginx snippet is not UTF-8") from exc
    if loaded_bytes != expected_snippet:
        raise SystemExit(
            "release evidence BLOCK: loaded nginx snippet digest/provenance mismatch"
        )
    _authenticated_include(
        sections, snippet_path, verify_source_files=verify_source_files
    )
    users = [
        (source_path, source, user)
        for source_path, source in sections.items()
        for user in re.findall(
            r"(?m)^\s*user\s+([A-Za-z_][A-Za-z0-9_-]*)\s*;",
            _without_comments(source),
        )
    ]
    if len(users) != 1:
        raise SystemExit("release evidence BLOCK: nginx worker user is ambiguous")
    if verify_source_files:
        _verify_nginx_source(users[0][0], users[0][1])
    snippet = _without_comments(loaded)
    starts = [
        match.start()
        for match in re.finditer(
            r"(?m)^\s*location\s+~\s+\^/\(healthz\|api/\)\s*\{", snippet
        )
    ]
    if len(starts) != 1:
        raise SystemExit(
            "release evidence BLOCK: exact router location is absent or duplicated"
        )
    start = starts[0]
    opening = snippet.find("{", start)
    if opening < 0:
        raise SystemExit("release evidence BLOCK: router location is malformed")
    block, _end = _balanced_block(snippet, opening)
    direct = _direct_scope(block)
    if re.search(
        r"(?mi)^\s*(?:auth_basic|auth_request)\s+off\s*;\s*$", direct
    ):
        raise SystemExit(
            "release evidence BLOCK: canary location disables authentication"
        )
    if (
        len(
            re.findall(
                r'(?m)^\s*if\s*\(\$remote_user\s*=\s*""\)\s*'
                r"\{\s*return\s+401\s*;\s*\}\s*$",
                direct,
            )
        )
        != 1
        or len(
            re.findall(
                r"(?m)^\s*proxy_pass\s+"
                r"http://unix:/run/trustforge/release-router\.sock:\s*;\s*$",
                direct,
            )
        )
        != 1
    ):
        raise SystemExit(
            "release evidence BLOCK: router authentication topology is unsafe"
        )
    identity_headers = re.findall(
        r"(?mi)^\s*proxy_set_header\s+"
        r"(X-TrustForge-(?:Stable-Subject|Trusted-Subject|Trusted-Identity))"
        r"\s+([^;]+)\s*;\s*$",
        direct,
    )
    expected_headers = {
        "x-trustforge-stable-subject": '""',
        "x-trustforge-trusted-subject": '""',
        "x-trustforge-trusted-identity": "$remote_user",
    }
    normalized_headers = [
        (name.lower(), value.strip()) for name, value in identity_headers
    ]
    if (
        len(normalized_headers) != len(expected_headers)
        or dict(normalized_headers) != expected_headers
    ):
        raise SystemExit("release evidence BLOCK: router identity headers are unsafe")
    try:
        uid = pwd.getpwnam(users[0][2]).pw_uid
    except KeyError as exc:
        raise SystemExit(
            "release evidence BLOCK: nginx worker identity is absent"
        ) from exc
    if uid <= 0:
        raise SystemExit("release evidence BLOCK: nginx worker must be non-root")
    return users[0][2], uid


def _real_nginx_config() -> str:
    nginx = shutil.which("nginx")
    if nginx is None:
        raise SystemExit(
            "release evidence BLOCK: exact local nginx binary is unavailable"
        )
    try:
        fd = os.open(nginx, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise SystemExit(
            "release evidence BLOCK: nginx binary path is unsafe"
        ) from exc
    try:
        info = os.fstat(fd)
        executable = f"/proc/self/fd/{fd}"
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) & 0o022
            or not stat.S_IMODE(info.st_mode) & 0o111
            or info.st_nlink != 1
            or not Path(executable).exists()
        ):
            raise SystemExit(
                "release evidence BLOCK: nginx binary metadata is unsafe"
            )
        try:
            completed = subprocess.run(
                [executable, "-T"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
                pass_fds=(fd,),
            )
        except (subprocess.SubprocessError, UnicodeError) as exc:
            raise SystemExit(
                "release evidence BLOCK: exact local nginx topology is unavailable"
            ) from exc
        return completed.stdout + completed.stderr
    finally:
        os.close(fd)


def _request(path: Path) -> list[dict]:
    raw, info = read_regular_file(path, maximum_bytes=128 * 1024)
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
        raise SystemExit(
            "canary allowlist request must be root:root 0600 and singly linked"
        )
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit("canary allowlist request is invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "entries"}
        or payload.get("schema") != REQUEST_SCHEMA
        or canonical_json(payload) + b"\n" != raw
        or not isinstance(payload["entries"], list)
        or not payload["entries"]
        or len(payload["entries"]) > 1_000
    ):
        raise SystemExit("canary allowlist request is noncanonical or incomplete")
    normalized: list[dict] = []
    for entry in payload["entries"]:
        if not isinstance(entry, dict) or set(entry) != {
            "trusted_identity",
            "path",
            "live_token_digest",
            "cost_budget",
        }:
            raise SystemExit("canary allowlist request entry is invalid")
        identity, request_path, token_digest, budget = (
            entry["trusted_identity"],
            entry["path"],
            entry["live_token_digest"],
            entry["cost_budget"],
        )
        request = parse_canary_request(request_path) if isinstance(request_path, str) else None
        if (
            not isinstance(identity, str)
            or not (1 <= len(identity.encode()) <= 256)
            or request is None
            or request.sample_mode
            or not isinstance(token_digest, str)
            or (
                request.live_mode
                and re.fullmatch(r"sha256:[0-9a-f]{64}", token_digest) is None
            )
            or (not request.live_mode and token_digest != "")
            or not isinstance(budget, dict)
        ):
            raise SystemExit("canary allowlist request entry is invalid")
        normalized.append(
            {
                "trusted_identity": identity,
                "path": request_path,
                "live_token_digest": token_digest,
                "cost_budget": budget,
            }
        )
    if len({canonical_json(item) for item in normalized}) != len(normalized):
        raise SystemExit("canary allowlist request contains duplicate entries")
    return normalized


def _read_all(fd: int, maximum_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(64 * 1024, maximum_bytes + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum_bytes:
            raise SystemExit("prior allowlist is oversized")


def _payload(
    args: argparse.Namespace,
    nginx_config: str,
    records: list[dict],
    expected_snippet: bytes,
) -> dict:
    _worker, worker_uid = _nginx_worker(
        nginx_config,
        snippet_path=args.nginx_snippet,
        expected_snippet=expected_snippet,
        verify_source_files=True,
    )
    initialized = records[0]["event"]
    runtime = _json_file(args.runtime)
    expected = {
        "control_ledger_id": records[0]["ledger_id"],
        "deployment_initialized_event_hash": records[0]["event_hash"],
        "a_artifact_digest": initialized["active"]["release_digest"],
        "b_artifact_digest": initialized["candidate"]["release_digest"],
        "routing_policy": initialized["policy"],
    }
    if any(runtime.get(name) != value for name, value in expected.items()):
        raise SystemExit("runtime does not match authenticated control initialization")
    key_payload = _json_file(args.keys)
    try:
        budget_keyring = {
            key: bytes.fromhex(value)
            for key, value in key_payload["canary_cost_budget_public"].items()
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit("canary cost budget public keys are invalid") from exc
    snapshot = SimpleNamespace(
        ledger_id=expected["control_ledger_id"],
        active=SimpleNamespace(
            release_digest=expected["a_artifact_digest"]
        ),
        candidate=SimpleNamespace(
            release_digest=expected["b_artifact_digest"]
        ),
        policy=SimpleNamespace(
            ramp_id=initialized["policy"]["ramp_id"],
            policy_digest=initialized["policy"]["policy_digest"],
        ),
        control_event_head=records[-1]["event_hash"],
        canary_epoch=records[-1]["event_hash"],
    )
    entries = []
    for requested in _request(args.request):
        request = parse_canary_request(requested["path"])
        assert request is not None
        digest = request_binding_digest(
            requested["trusted_identity"],
            request,
            snapshot,
            online_stance_mode=True,
            live_token_digest=requested["live_token_digest"],
        )
        try:
            budget = CanaryCostBudget(**requested["cost_budget"])
            verify_budget(
                budget,
                keyring=budget_keyring,
                now=datetime.now(timezone.utc),
                deployment_ledger_id=snapshot.ledger_id,
                canary_epoch=snapshot.canary_epoch,
                active_artifact_digest=snapshot.active.release_digest,
                candidate_artifact_digest=snapshot.candidate.release_digest,
                ramp_id=snapshot.policy.ramp_id,
                routing_policy_digest=snapshot.policy.policy_digest,
                request_binding_digest=digest,
            )
        except (CanaryCostBudgetError, TypeError, ValueError) as exc:
            raise SystemExit(
                "canary allowlist signed budget is invalid"
            ) from exc
        entries.append(
            {
                "trusted_identity": requested["trusted_identity"],
                "endpoint": request.endpoint,
                "assets": list(request.assets),
                "query_digest": request.query_digest,
                "question_type": request.question_type,
                "live_mode": request.live_mode,
                "sample_mode": request.sample_mode,
                "data_mode": request.data_mode,
                "llm_mode": request.llm_mode,
                "online_stance_mode": True,
                "active_release_digest": expected["a_artifact_digest"],
                "candidate_release_digest": expected["b_artifact_digest"],
                "ramp_id": initialized["policy"]["ramp_id"],
                "control_ledger_id": expected["control_ledger_id"],
                "policy_digest": initialized["policy"]["policy_digest"],
                "cost_budget": requested["cost_budget"],
            }
        )
    return {
        "schema": ALLOWLIST_SCHEMA,
        "activation_contract": ACTIVATION_CONTRACT,
        "trusted_proxy_uid": worker_uid,
        "control_ledger_head": records[-1]["event_hash"],
        "entries": entries,
    }


def _publish(output: Path, payload: dict, *, owner_uid: int = 0) -> str:
    encoded = canonical_json(payload) + b"\n"
    ReleaseHTTPCanaryPolicy.from_payload(payload)
    with pinned_directory(output.parent) as parent_fd:
        parent = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != owner_uid
            or stat.S_IMODE(parent.st_mode) & 0o022
        ):
            raise SystemExit("allowlist parent must be root-owned and non-writable")
        prior: bytes | None = None
        try:
            prior_fd = os.open(
                output.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            prior_fd = None
        if prior_fd is not None:
            try:
                prior_info = os.fstat(prior_fd)
                if (
                    not stat.S_ISREG(prior_info.st_mode)
                    or prior_info.st_uid != owner_uid
                    or stat.S_IMODE(prior_info.st_mode) != 0o600
                    or prior_info.st_nlink != 1
                    or prior_info.st_size > 128 * 1024
                ):
                    raise SystemExit("prior allowlist metadata is unsafe")
                prior = _read_all(prior_fd, 128 * 1024)
            finally:
                os.close(prior_fd)
        published = False
        try:
            write_atomic_at(parent_fd, output.name, encoded, immutable=False)
            published = True
            raw, info = read_regular_file_at(
                parent_fd, output.name, maximum_bytes=128 * 1024
            )
            if (
                raw != encoded
                or info.st_uid != owner_uid
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise RuntimeError(
                    "published allowlist metadata/content verification failed"
                )
            ReleaseHTTPCanaryPolicy.from_payload(json.loads(raw))
        except BaseException:
            if published:
                if prior is None:
                    os.unlink(output.name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                else:
                    write_atomic_at(parent_fd, output.name, prior, immutable=False)
            raise
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("canary allowlist provisioning requires root")
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--keys", type=Path, required=True)
    parser.add_argument("--control-bootstrap", type=Path, required=True)
    parser.add_argument("--control-events", type=Path, required=True)
    parser.add_argument("--control-head", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--nginx-snippet", type=Path, required=True)
    parser.add_argument("--expected-nginx-snippet-sha256", required=True)
    parser.add_argument(
        "--coordination-lock",
        type=Path,
        default=DEFAULT_COORDINATION_LOCK,
    )
    args = parser.parse_args()
    nginx_config = _real_nginx_config()
    expected_snippet, snippet_info = read_regular_file(
        args.nginx_snippet, maximum_bytes=128 * 1024
    )
    if (
        snippet_info.st_uid != 0
        or stat.S_IMODE(snippet_info.st_mode) != 0o644
        or snippet_info.st_nlink != 1
        or hashlib.sha256(expected_snippet).hexdigest()
        != args.expected_nginx_snippet_sha256
    ):
        raise SystemExit(
            "release evidence BLOCK: nginx snippet metadata/digest mismatch"
        )
    keys = _json_file(args.keys)
    control_ledger = _control_ledger(
        args, keys, coordination_lock_path=args.coordination_lock
    )
    with control_ledger.coordination_lock():
        records = control_ledger.read()
        if not records or records[0]["event"].get("kind") != "deployment_initialized":
            raise SystemExit("authenticated control ledger is not initialized")
        expected_head = records[-1]["event_hash"]
        payload = _payload(args, nginx_config, records, expected_snippet)
        if control_ledger.read()[-1]["event_hash"] != expected_head:
            raise SystemExit("control head changed during allowlist provisioning")
        print(_publish(args.output, payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
