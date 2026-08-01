"""Read-only, bounded Hermes production-audit orchestration.

The adapter exposes only bounded AWS read operations and one fixed SSM
read-only command. It never accepts a shell fragment, table name, projection,
or AWS operation from CLI input. Raw remote payloads are reduced in memory and
never written to an evidence bundle.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .hermes_audit_contracts import (
    AggregateSummary,
    AuditBundle,
    AuditContractError,
    AuditLimits,
    AuditStatus,
    AuditTarget,
    ControlPlane,
    ControlState,
    DataPlane,
    DEFAULT_READ_BUDGETS,
    DeadLetterSummary,
    EffectiveControl,
    EvidenceRef,
    ReadBudget,
    ReleaseComparison,
    ReleaseComparisonStatus,
    SAFE_RELEASE_RE,
    SYSTEMD_UNIT_ALLOWLIST,
    TABLE_TYPE_ALLOWLIST,
    TableAudit,
    UnitEvidence,
    canonical_json,
    has_secret_like_value,
    redact_or_reject_remote_payload,
    sha256_digest,
)
from .hermes_audit_signing import EvidenceAttestationV1

REMOTE_SCHEMA_VERSION = "hermes-audit-ssm.v1"
MAX_SSM_OUTPUT_BYTES = 256 * 1024
SSM_POLL_SECONDS = 1.0
TABLE_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{3,255}\Z")
_CONFIG_ALLOWLIST = (
    "CACHE_BACKEND",
    "TRUSTFORGE_CACHE_TABLE",
    "TRUSTFORGE_SCHEDULER_RUN_TABLE",
    "TRUSTFORGE_COST_LEDGER_TABLE",
    "TRUSTFORGE_HERMES_AUTONOMY_ENABLED",
    "TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED",
    "TRUSTFORGE_AGOS_ENABLED",
    "TRUSTFORGE_COIN_POOL",
    "TRUSTFORGE_BEDROCK_DAILY_USD_CAP",
    "TRUSTFORGE_RUNTIME_SWITCH",
    "TRUSTFORGE_ALLOW_PRODUCTION_CONTINUOUS",
    "TRUSTFORGE_ENV",
    "TRUSTFORGE_RUNTIME_STATE_PATH",
    "TRUSTFORGE_HOME",
    "TRUSTFORGE_SHARED_ANALYSIS_DB_PATH",
)
_TABLE_NAME_FLAGS = (
    "TRUSTFORGE_CACHE_TABLE",
    "TRUSTFORGE_SCHEDULER_RUN_TABLE",
    "TRUSTFORGE_COST_LEDGER_TABLE",
)
# Distinct from a genuinely-unset table-name flag ("absent"): the remote flag
# was set but to a value that fails TABLE_NAME_RE (e.g. too short/long, illegal
# characters, or -- to make the sentinel itself impossible to spoof as a real
# table name -- it also contains ":" which TABLE_NAME_RE's charset excludes).
# Emitted by `config_value()` inside STATIC_SSM_COMMAND (kept in sync with the
# literal there) and consumed by `_validate_ssm_snapshot`/`TableBinding.configured`
# so "present but invalid" fails closed like "present but not approved", never
# collapsing into "absent" and silently adopting the static approved table.
_TABLE_NAME_INVALID_SENTINEL = "table-name:invalid"

# Immutable program text: no input interpolation, no systemctl cat/full unit
# output, no remote mutation, and no stdout containing an Environment value or
# journal text. `Environment` is inspected in-process solely to reduce the
# named allowlisted flags to a tri-state/presence value before JSON emission.
STATIC_SSM_COMMAND = r'''python3 - <<'PY'
import hashlib, json, os, platform, re, select, sqlite3, subprocess, time
UNITS = ("hermes-cycle.timer", "hermes-cycle.service", "trustforge-analysis-flow.service", "fetch-scheduler.timer", "fetch-scheduler.service")
FLAGS = ("CACHE_BACKEND", "TRUSTFORGE_CACHE_TABLE", "TRUSTFORGE_SCHEDULER_RUN_TABLE", "TRUSTFORGE_COST_LEDGER_TABLE", "TRUSTFORGE_HERMES_AUTONOMY_ENABLED", "TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED", "TRUSTFORGE_AGOS_ENABLED", "TRUSTFORGE_COIN_POOL", "TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "TRUSTFORGE_RUNTIME_SWITCH", "TRUSTFORGE_ALLOW_PRODUCTION_CONTINUOUS", "TRUSTFORGE_ENV", "TRUSTFORGE_RUNTIME_STATE_PATH", "TRUSTFORGE_HOME", "TRUSTFORGE_SHARED_ANALYSIS_DB_PATH")
TABLE_NAME_FLAGS = ("TRUSTFORGE_CACHE_TABLE", "TRUSTFORGE_SCHEDULER_RUN_TABLE", "TRUSTFORGE_COST_LEDGER_TABLE")
TABLE_NAME_INVALID_SENTINEL = "table-name:invalid"
DEAD_LETTER_ROW_LIMIT = 32
# Human-curated, deliberately static allowlist of exception class names that
# may legitimately prefix `analysis_dead_letters.error` (see
# docs/plans/PLAN-HERMES-PR1197-REMEDIATION-2026-07-31.md 5.1(3)): anything
# else, including any secret-like text, is reduced to a fixed sentinel and
# the raw error text never leaves this process.
ERROR_CLASS_ALLOWLIST = frozenset((
    "TimeoutError", "ConnectionError", "ConnectionResetError", "ConnectionRefusedError",
    "OSError", "ValueError", "RuntimeError", "KeyError", "PermissionError",
    "MultiAngleCapacityError", "MultiAngleBudgetError", "MultiAngleAuthorityError",
    "MultiAngleIdempotencyConflictError", "MultiAngleRequestInProgressError",
    "BedrockThrottlingException", "ClientError",
))
def classify_error(text):
    # `text` never leaves this function as-is: only an allowlisted class
    # name or the fixed sentinel below is ever returned.
    token = re.split(r"[:\s]", text or "", maxsplit=1)[0]
    return token if token in ERROR_CLASS_ALLOWLIST else "unclassified-error"
def dead_letters(path, limit):
    # Fail-closed: any missing file/table, lock, or malformed row collapses
    # to None (caller substitutes the "unavailable" sentinel), never raises.
    try:
        if not path or not os.path.isfile(path):
            return None
        connection = sqlite3.connect("file:" + path + "?mode=ro", uri=True, timeout=1)
        try:
            # Belt-and-suspenders on top of the read-only URI connection
            # (see analysis_flow.py's own mode=ro convention); busy_timeout
            # here is shorter than analysis_flow's own writer busy_timeout
            # so a concurrent WAL writer cannot exhaust this SSM command's
            # own read budget.
            connection.execute("PRAGMA busy_timeout=1000")
            connection.execute("PRAGMA query_only=1")
            cursor = connection.execute(
                "SELECT job_id, stage, coin, attempts, error, failed_at FROM analysis_dead_letters "
                "ORDER BY failed_at DESC LIMIT ?", (limit + 1,),
            )
            rows = cursor.fetchall()
        finally:
            connection.close()
        truncated = len(rows) > limit
        output = []
        for job_id, stage, coin, attempts, error, failed_at in rows[:limit]:
            output.append({
                "job_id": job_id if isinstance(job_id, str) else "",
                "coin": coin if isinstance(coin, str) else "",
                "stage": stage if isinstance(stage, str) else "",
                "attempt": attempts,
                "error_class": classify_error(error if isinstance(error, str) else None),
                "retry_state": "dead-lettered",
                "release_identity": None,
            })
        return {"available": True, "truncated": truncated, "rows": output}
    except Exception:
        return None
def config_value(name, service_env):
    if name in TABLE_NAME_FLAGS:
        # A key genuinely missing from service_env (never set by the unit)
        # is "absent". A key that IS present -- even mapped to "" by a
        # misconfigured `Environment=NAME=` with no value -- is present but
        # malformed, and must stay distinguishable from genuinely absent:
        # collapsing either case into "absent" would let a syntactically
        # invalid (or empty) remote override be silently miscategorized as
        # "not configured" and fall back to the static approved table
        # upstream.
        if name not in service_env:
            return "absent"
        raw = service_env[name]
        return raw if re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", raw) else TABLE_NAME_INVALID_SENTINEL
    raw = service_env.get(name, "")
    return "configured" if raw else "absent"
def call(*args, limit=8192, timeout=3):
    process = None
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        output, deadline = bytearray(), time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                return None
            ready, _, _ = select.select([process.stdout], [], [], remaining)
            if not ready:
                if process.poll() is not None:
                    break
                continue
            chunk = os.read(process.stdout.fileno(), min(1024, limit - len(output) + 1))
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > limit:
                process.kill()
                return None
        if process.wait(timeout=max(0.1, deadline - time.monotonic())) != 0:
            return None
        return output.decode("utf-8", "replace").strip()
    except Exception:
        return None
    finally:
        if process is not None and process.poll() is None:
            process.kill()
def state(value):
    return value if value in ("enabled", "disabled", "active", "inactive", "failed") else "unknown"
def unsigned(value):
    return int(value) if isinstance(value, str) and value.isdecimal() and int(value) <= 9223372036854775807 else None
def digest(path):
    try:
        if not path or not os.path.isfile(path) or os.path.getsize(path) > 1048576:
            return None
        with open(path, "rb") as handle:
            return "sha256:" + hashlib.sha256(handle.read()).hexdigest()
    except Exception:
        return None
def environment_values(unit):
    # The full Environment string never leaves this process or stdout.
    raw, values = call("systemctl", "show", unit, "--property=Environment", "--value"), {}
    for token in (raw or "").split():
        key, separator, value = token.partition("=")
        if separator and key in FLAGS:
            values[key] = value
    return values
def flag_state(value):
    return "enabled" if value == "1" else "disabled" if value == "0" else "unknown"
def parse_runtime_bool(value):
    value = (value or "").strip().lower()
    return True if value in ("1", "true", "yes", "on", "start", "enabled") else False if value in ("0", "false", "no", "off", "stop", "disabled") else None
def runtime_guard_state(service_env):
    # Mirror trustforge.runtime_control.runtime_control() using the fixed
    # hermes-cycle.service environment, not the SSM process environment.
    env_switch = parse_runtime_bool(service_env.get("TRUSTFORGE_RUNTIME_SWITCH"))
    production = (service_env.get("TRUSTFORGE_ENV", "").strip().lower() in ("prod", "production") or service_env.get("CACHE_BACKEND", "").strip().lower() == "dynamodb")
    allowed = parse_runtime_bool(service_env.get("TRUSTFORGE_ALLOW_PRODUCTION_CONTINUOUS")) is True
    if env_switch is not None:
        return "disabled" if production and env_switch and not allowed else flag_state("1" if env_switch else "0")
    if production:
        # production_default is evaluated separately so Dynamo admin config can
        # override the unset production default, exactly as hermes.py does.
        return "unknown"
    home = service_env.get("TRUSTFORGE_HOME", "/opt/trustforge")
    state_path = service_env.get("TRUSTFORGE_RUNTIME_STATE_PATH") or os.path.join(home, "out", "trustforge-runtime-control.json")
    try:
        with open(state_path, "rb") as handle:
            raw = handle.read(513)
        if len(raw) > 512:
            return "unknown"
        value = json.loads(raw.decode("utf-8"))
        return flag_state("1" if value.get("enabled") is True else "0" if value.get("enabled") is False else "unknown")
    except FileNotFoundError:
        return "enabled"
    except Exception:
        return "unknown"
def journal_error_count(name):
    raw = call("journalctl", "--no-pager", "--quiet", "--lines=100", "--unit", name, "--since=-24h", "--priority=err", limit=8192, timeout=5)
    return None if raw is None else min(raw.count("\n") + (1 if raw else 0), 100)
def unit(name, service_env):
    fragment = call("systemctl", "show", name, "--property=FragmentPath", "--value")
    dropins = (call("systemctl", "show", name, "--property=DropInPaths", "--value") or "").split()
    result = call("systemctl", "show", name, "--property=Result", "--value")
    return {"unit":name,"enabled":state(call("systemctl","is-enabled",name)),"active":state(call("systemctl","is-active",name)),"result":result if isinstance(result, str) and result.replace("-","").isalnum() else "unknown","unit_sha256":digest(fragment),"drop_in_sha256":digest(dropins[0]) if len(dropins)==1 else None,"hermes_unit_env":flag_state(service_env.get("TRUSTFORGE_HERMES_AUTONOMY_ENABLED", "")) if name=="hermes-cycle.service" else "unknown","timer_next_monotonic_usec":unsigned(call("systemctl","show",name,"--property=NextElapseUSecMonotonic","--value")) if name.endswith(".timer") else None,"timer_last_trigger_monotonic_usec":unsigned(call("systemctl","show",name,"--property=LastTriggerUSecMonotonic","--value")) if name.endswith(".timer") else None,"journal_error_count":journal_error_count(name)}
def bounded_text(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read(4097)
        text = raw.decode("utf-8", "replace").strip()
        return text if len(raw) <= 4096 and text and len(text) <= 256 and re.fullmatch(r"[A-Za-z0-9._+@-]+", text) else None
    except Exception:
        return None
# ---- driver: executes once per SSM RunCommand invocation ----
service_env = environment_values("hermes-cycle.service")
units = [unit(name, service_env) for name in UNITS]
home = service_env.get("TRUSTFORGE_HOME", "/opt/trustforge")
db_path = service_env.get("TRUSTFORGE_SHARED_ANALYSIS_DB_PATH") or os.path.join(home, "out", "trustforge.sqlite3")
payload = {"schema_version":"hermes-audit-ssm.v1","units":units,"control":{"runtime_guard":runtime_guard_state(service_env),"unit_env":flag_state(service_env.get("TRUSTFORGE_HERMES_AUTONOMY_ENABLED", "")),"production_default":"disabled"},"config":{name:config_value(name, service_env) for name in FLAGS},"release":{"version":bounded_text("/opt/trustforge/VERSION"),"python":platform.python_version(),"manifest_sha256":digest("/opt/trustforge/manifest.json")},"dead_letters":dead_letters(db_path, DEAD_LETTER_ROW_LIMIT) or {"available": False, "truncated": False, "rows": []}}
print(json.dumps(payload, sort_keys=True, separators=(",",":")))
PY'''
STATIC_SSM_COMMAND_DIGEST = "sha256:" + hashlib.sha256(
    b"trustforge.hermes-static-ssm.v1\x00" + STATIC_SSM_COMMAND.encode("utf-8")
).hexdigest()


class AuditBlocked(RuntimeError):
    """A preflight dependency is unavailable; no fallback is permitted."""


class AuditPartial(RuntimeError):
    """An allowed collector did not provide enough evidence."""


@dataclass(frozen=True, slots=True)
class AwsAuditClients:
    sts: Any
    ssm: Any
    dynamodb: Any


def create_aws_clients(region: str, limits: AuditLimits) -> AwsAuditClients:
    """Lazy-create bounded AWS clients only for a non-dry production audit."""
    import boto3
    from botocore.config import Config

    timeout = min(item.timeout_seconds for item in limits.read_budgets.values())
    retries = min(item.retry_limit for item in limits.read_budgets.values())
    config = Config(
        connect_timeout=timeout,
        read_timeout=timeout,
        retries={"mode": "standard", "max_attempts": retries},
    )
    return AwsAuditClients(
        boto3.client("sts", region_name=region, config=config),
        boto3.client("ssm", region_name=region, config=config),
        boto3.client("dynamodb", region_name=region, config=config),
    )


def _limits_dict(limits: AuditLimits) -> dict[str, Any]:
    return {
        "overall_deadline_seconds": limits.overall_deadline_seconds,
        "read_budgets": {
            name: {
                "request_limit": item.request_limit,
                "item_limit": item.item_limit,
                "byte_limit": item.byte_limit,
                "timeout_seconds": item.timeout_seconds,
                "retry_limit": item.retry_limit,
            }
            for name, item in limits.read_budgets.items()
        },
    }


def _validated_output_dir(output_dir: Path) -> Path:
    allowed = Path(__file__).resolve().parents[2] / "out"
    requested = Path(os.path.abspath(os.fspath(output_dir)))
    try:
        relative = requested.relative_to(allowed)
    except ValueError as exc:
        raise AuditContractError("audit output directory must be inside ignored repository out/") from exc
    # Do not allow an ignored output path to redirect evidence through a
    # symlink. Check the existing portion of every ancestor, including out/.
    for index in range(len(relative.parts) + 1):
        if allowed.joinpath(*relative.parts[:index]).is_symlink():
            raise AuditContractError("audit output directory or ancestor must not be a symlink")
    return requested


def dry_run_plan(
    target: AuditTarget,
    output_dir: Path,
    limits: AuditLimits,
    expected_release: str | None,
) -> dict[str, Any]:
    """Return a local-only plan; do not construct clients or contact AWS."""
    if expected_release is not None and SAFE_RELEASE_RE.fullmatch(expected_release) is None:
        raise AuditContractError("expected release has invalid grammar")
    destination = _validated_output_dir(output_dir)
    return {
        "mode": "dry-run",
        "target": {"region": target.region, "instance_id": target.instance_id},
        "output_dir": str(destination),
        "expected_release": expected_release,
        "static_ssm_command_sha256": STATIC_SSM_COMMAND_DIGEST,
        "aws_operations": [
            "sts:GetCallerIdentity",
            "ssm:DescribeInstanceInformation",
            "ssm:SendCommand(read-only-static)",
            "ssm:GetCommandInvocation",
            "dynamodb:DescribeTable",
            "dynamodb:DescribeTimeToLive",
            "dynamodb:GetItem",
            "dynamodb:Scan(limit-and-projection-bounded)",
        ],
        "limits": _limits_dict(limits),
        "no_mutation": True,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ensure_call_window(
    deadline: float | None,
    now: Callable[[], float],
    timeout_seconds: int,
    reason: str,
) -> None:
    if deadline is not None and deadline - now() < timeout_seconds:
        raise AuditPartial(reason)


def _identity_hash(
    clients: AwsAuditClients,
    *,
    deadline: float | None = None,
    now: Callable[[], float] = time.monotonic,
    timeout_seconds: int = 1,
) -> str:
    if deadline is not None:
        current = now()
        if current >= deadline:
            raise AuditBlocked("audit-deadline-exhausted")
        if deadline - current < timeout_seconds:
            raise AuditBlocked("audit-deadline-call-window-exhausted")
    try:
        identity = clients.sts.get_caller_identity()
    except Exception as exc:
        raise AuditBlocked("sts-get-caller-identity-failed") from exc
    if not isinstance(identity, Mapping) or not all(
        isinstance(identity.get(key), str) and identity[key]
        for key in ("Account", "Arn", "UserId")
    ):
        raise AuditBlocked("sts-identity-schema-invalid")
    return sha256_digest({key: identity[key] for key in ("Account", "Arn", "UserId")})


def preflight_target(
    clients: AwsAuditClients,
    target: AuditTarget,
    *,
    deadline: float | None = None,
    now: Callable[[], float] = time.monotonic,
    timeout_seconds: int = 1,
) -> None:
    if deadline is not None and now() >= deadline:
        raise AuditPartial("audit-deadline-exhausted")
    _ensure_call_window(deadline, now, timeout_seconds, "audit-deadline-call-window-exhausted")
    try:
        response = clients.ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [target.instance_id]}],
            MaxResults=1,
        )
    except Exception as exc:
        raise AuditBlocked("ssm-target-preflight-failed") from exc
    entries = response.get("InstanceInformationList") if isinstance(response, Mapping) else None
    if not isinstance(entries, list) or len(entries) != 1:
        raise AuditBlocked("ssm-target-not-managed")
    entry = entries[0]
    if (
        not isinstance(entry, Mapping)
        or entry.get("InstanceId") != target.instance_id
        or entry.get("PingStatus") != "Online"
    ):
        raise AuditBlocked("ssm-target-not-online")


def _bounded_ssm_snapshot(
    clients: AwsAuditClients,
    target: AuditTarget,
    limits: AuditLimits,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    deadline: float | None = None,
) -> Mapping[str, Any]:
    budget = limits.read_budgets["ssm"]
    deadline = deadline if deadline is not None else now() + min(limits.overall_deadline_seconds, budget.timeout_seconds * 3)
    requests = 0
    if now() >= deadline:
        raise AuditPartial("audit-deadline-exhausted")
    if requests >= budget.request_limit:
        raise AuditPartial("ssm-request-budget-exhausted")
    _ensure_call_window(deadline, now, budget.timeout_seconds, "audit-deadline-call-window-exhausted")
    try:
        started = clients.ssm.send_command(
            InstanceIds=[target.instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [STATIC_SSM_COMMAND]},
            TimeoutSeconds=budget.timeout_seconds,
            Comment="trustforge-hermes-production-audit-static-read-v1",
        )
        requests += 1
        command_id = started["Command"]["CommandId"]
    except AuditPartial:
        raise
    except Exception as exc:
        raise AuditPartial("ssm-static-command-start-failed") from exc
    while requests < budget.request_limit:
        remaining = deadline - now()
        if remaining <= 0:
            raise AuditPartial("audit-deadline-exhausted")
        try:
            if requests >= budget.request_limit:
                raise AuditPartial("ssm-request-budget-exhausted")
            _ensure_call_window(deadline, now, budget.timeout_seconds, "audit-deadline-call-window-exhausted")
            invocation = clients.ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=target.instance_id,
                PluginName="aws:runShellScript",
            )
            requests += 1
        except AuditPartial:
            raise
        except Exception as exc:
            raise AuditPartial("ssm-static-command-read-failed") from exc
        status = invocation.get("Status") if isinstance(invocation, Mapping) else None
        if status == "Success":
            stdout = invocation.get("StandardOutputContent", "")
            if not isinstance(stdout, str) or len(stdout.encode("utf-8")) > MAX_SSM_OUTPUT_BYTES:
                raise AuditPartial("ssm-static-output-bounds-invalid")
            try:
                return _validate_ssm_snapshot(json.loads(stdout))
            except (TypeError, json.JSONDecodeError) as exc:
                raise AuditPartial("ssm-static-output-json-invalid") from exc
        if status in {"Cancelled", "TimedOut", "Failed", "Cancelling"}:
            raise AuditPartial("ssm-static-command-unsuccessful")
        remaining = deadline - now()
        if remaining <= 0:
            raise AuditPartial("audit-deadline-exhausted")
        sleep(min(SSM_POLL_SECONDS, remaining))
    raise AuditPartial("ssm-static-command-timeout-or-budget-exhausted")


def _is_optional_nonnegative(value: Any) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)


_DEAD_LETTER_ROW_KEYS = frozenset(
    {"job_id", "coin", "stage", "attempt", "error_class", "retry_state", "release_identity"}
)


def _validate_ssm_snapshot(value: Any) -> Mapping[str, Any]:
    expected = {"schema_version", "units", "control", "config", "release", "dead_letters"}
    if (
        not isinstance(value, Mapping)
        or set(value) != expected
        or value.get("schema_version") != REMOTE_SCHEMA_VERSION
    ):
        raise AuditContractError("SSM snapshot schema is invalid")
    redact_or_reject_remote_payload(value)
    units = value.get("units")
    if not isinstance(units, list) or len(units) != len(SYSTEMD_UNIT_ALLOWLIST):
        raise AuditContractError("SSM snapshot unit count is invalid")
    required = {
        "unit",
        "enabled",
        "active",
        "result",
        "unit_sha256",
        "drop_in_sha256",
        "hermes_unit_env",
        "timer_next_monotonic_usec",
        "timer_last_trigger_monotonic_usec",
        "journal_error_count",
    }
    for unit, name in zip(units, SYSTEMD_UNIT_ALLOWLIST, strict=True):
        if not isinstance(unit, Mapping) or set(unit) != required or unit.get("unit") != name:
            raise AuditContractError("SSM snapshot unit schema is invalid")
        if unit.get("enabled") not in {"enabled", "disabled", "unknown"}:
            raise AuditContractError("SSM enabled state is invalid")
        if unit.get("active") not in {"active", "inactive", "failed", "unknown"}:
            raise AuditContractError("SSM active state is invalid")
        if unit.get("hermes_unit_env") not in {"enabled", "disabled", "unknown"}:
            raise AuditContractError("SSM unit env state is invalid")
        if not isinstance(unit.get("result"), str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", unit["result"]):
            raise AuditContractError("SSM result is invalid")
        if not all(_is_optional_nonnegative(unit.get(key)) for key in ("timer_next_monotonic_usec", "timer_last_trigger_monotonic_usec", "journal_error_count")):
            raise AuditContractError("SSM bounded summary is invalid")
    control = value.get("control")
    if (
        not isinstance(control, Mapping)
        or set(control) != {"runtime_guard", "unit_env", "production_default"}
        or any(control.get(key) not in {"enabled", "disabled", "unknown"} for key in control)
    ):
        raise AuditContractError("SSM snapshot control schema is invalid")
    config = value.get("config")
    if not isinstance(config, Mapping) or set(config) != set(_CONFIG_ALLOWLIST):
        raise AuditContractError("SSM snapshot config schema is invalid")
    for name, item in config.items():
        if name in _TABLE_NAME_FLAGS:
            if (
                item not in ("absent", _TABLE_NAME_INVALID_SENTINEL)
                and (not isinstance(item, str) or TABLE_NAME_RE.fullmatch(item) is None)
            ):
                raise AuditContractError("SSM snapshot config schema is invalid")
        elif item not in {"configured", "absent"}:
            raise AuditContractError("SSM snapshot config schema is invalid")
    release = value.get("release")
    if (
        not isinstance(release, Mapping)
        or set(release) != {"version", "python", "manifest_sha256"}
        or release.get("version") is not None and not isinstance(release.get("version"), str)
        or not isinstance(release.get("python"), str)
        or release.get("manifest_sha256") is not None and (not isinstance(release.get("manifest_sha256"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", release["manifest_sha256"]))
    ):
        raise AuditContractError("SSM snapshot release schema is invalid")
    dead_letters = value.get("dead_letters")
    if (
        not isinstance(dead_letters, Mapping)
        or set(dead_letters) != {"available", "truncated", "rows"}
        or not isinstance(dead_letters.get("available"), bool)
        or not isinstance(dead_letters.get("truncated"), bool)
        or not isinstance(dead_letters.get("rows"), list)
        or len(dead_letters["rows"]) > DEFAULT_READ_BUDGETS["local-io"].item_limit
        or (not dead_letters["available"] and (dead_letters["rows"] or dead_letters["truncated"]))
    ):
        raise AuditContractError("SSM snapshot dead-letter schema is invalid")
    for row in dead_letters["rows"]:
        if (
            not isinstance(row, Mapping)
            or set(row) != _DEAD_LETTER_ROW_KEYS
            or any(
                not isinstance(row.get(key), str)
                for key in ("job_id", "coin", "stage", "error_class", "retry_state")
            )
            or isinstance(row.get("attempt"), bool)
            or not isinstance(row.get("attempt"), (int, str))
            or (row.get("release_identity") is not None and not isinstance(row.get("release_identity"), str))
        ):
            raise AuditContractError("SSM snapshot dead-letter row schema is invalid")
    return value


def _state(value: Any) -> ControlState:
    return ControlState(value) if value in {item.value for item in ControlState} else ControlState.UNKNOWN


def _unit_evidence(snapshot: Mapping[str, Any], status: AuditStatus) -> tuple[UnitEvidence, ...]:
    output = []
    for raw in snapshot["units"]:
        active = (
            ControlState.ENABLED
            if raw["active"] == "active"
            else ControlState.DISABLED
            if raw["active"] in {"inactive", "failed"}
            else ControlState.UNKNOWN
        )
        output.append(
            UnitEvidence(
                raw["unit"],
                status,
                _state(raw["enabled"]),
                active,
                raw["result"],
                raw["unit_sha256"],
                raw["drop_in_sha256"],
                raw["timer_next_monotonic_usec"],
                raw["timer_last_trigger_monotonic_usec"],
                raw["journal_error_count"],
            )
        )
    return tuple(output)


def _comparison(field: str, expected: str | None, deployed: str | None) -> ReleaseComparison:
    status = (
        ReleaseComparisonStatus.UNKNOWN
        if expected is None or deployed is None
        else ReleaseComparisonStatus.MATCH
        if expected == deployed
        else ReleaseComparisonStatus.MISMATCH
    )
    return ReleaseComparison(field, expected, deployed, status)


def control_from_snapshot(
    snapshot: Mapping[str, Any],
    dynamodb_state: ControlState,
    expected_release: str | None,
) -> ControlPlane:
    remote, release = snapshot["control"], snapshot["release"]
    # Table-name flags may now carry the real remote table name (validated
    # against TABLE_NAME_RE) instead of a bare "configured"/"absent" tri-state
    # -- but `redacted_config` must never surface that raw name, so every
    # entry is normalized back to tri-state before it can reach the bundle.
    config = {
        name.replace("_", "-").lower(): ("absent" if value == "absent" else "configured")
        for name, value in snapshot["config"].items()
    }
    config["python"] = str(release["python"])
    runtime_guard = _state(remote["runtime_guard"])
    unit_env = _state(remote["unit_env"])
    production_default = _state(remote["production_default"])
    effective = next(
        (state for state in (runtime_guard, dynamodb_state, unit_env, production_default)
         if state is not ControlState.UNKNOWN),
        ControlState.UNKNOWN,
    )
    return ControlPlane(
        _unit_evidence(snapshot, AuditStatus.COMPLETE),
        EffectiveControl(
            runtime_guard,
            dynamodb_state,
            unit_env,
            production_default,
            effective,
        ),
        config,
        tuple(
            [
                _comparison("expected-release", expected_release, release["version"]),
                _comparison("manifest-digest", None, release["manifest_sha256"]),
                _comparison("config-digest", None, sha256_digest(snapshot["config"])),
            ]
            + [
                _comparison(f"unit-{index}-digest", None, raw["unit_sha256"])
                for index, raw in enumerate(snapshot["units"])
            ]
        ),
    )


def _placeholder_control(status: AuditStatus) -> ControlPlane:
    units = tuple(
        UnitEvidence(name, status, ControlState.UNKNOWN, ControlState.UNKNOWN, "unknown", None, None)
        for name in SYSTEMD_UNIT_ALLOWLIST
    )
    return ControlPlane(
        units,
        EffectiveControl(
            ControlState.UNKNOWN,
            ControlState.UNKNOWN,
            ControlState.UNKNOWN,
            ControlState.DISABLED,
            ControlState.DISABLED,
        ),
        {},
        (),
    )


def _placeholder_data(status: AuditStatus) -> DataPlane:
    tables = tuple(TableAudit(name, status, 0, 0, 0, False) for name in TABLE_TYPE_ALLOWLIST)
    summary = AggregateSummary(status, 0, False)
    return DataPlane(tables, summary, summary, summary)


_APPROVED_TABLE_NAMES = {
    "admin-config": "trustforge-connector-cache",
    "connector-cache": "trustforge-connector-cache",
    "scheduler-run": "trustforge-scheduler-runs",
    "cost-ledger": "trustforge-cost-ledger",
}
# Which remote `config` flag (see _TABLE_NAME_FLAGS) names each table type's
# binding. admin-config shares connector-cache's physical table/flag.
_TABLE_TYPE_NAME_FLAGS = {
    "admin-config": "TRUSTFORGE_CACHE_TABLE",
    "connector-cache": "TRUSTFORGE_CACHE_TABLE",
    "scheduler-run": "TRUSTFORGE_SCHEDULER_RUN_TABLE",
    "cost-ledger": "TRUSTFORGE_COST_LEDGER_TABLE",
}
_REQUIRED_KEYS = {
    "admin-config": {"source_id", "coin"},
    "connector-cache": {"source_id", "coin"},
    "scheduler-run": {"run_id", "ts"},
    "cost-ledger": {"run_id", "ts"},
}
_PROJECTIONS = {
    "connector-cache": ("source_id", "coin", "fetched_at", "ttl"),
    "scheduler-run": ("run_id", "ts", "status", "release_identity", "error_class"),
    "cost-ledger": ("run_id", "ts", "status", "release_identity", "cost_usd", "error_class"),
}
_ADMIN_PROJECTION = ("hermes_autonomy_enabled", "version", "updated_at")


def _projection(names: tuple[str, ...]) -> tuple[str, dict[str, str]]:
    """Alias every projected attribute so DynamoDB reserved words never reach the wire.

    `status`, `version`, and `ttl` are all reserved; an unaliased projection is
    rejected with ValidationException, which would silently turn every healthy
    production table into `insufficient-evidence`. Aliasing all names, not just
    today's reserved ones, keeps the collector correct as the reserved list grows.
    """
    return ", ".join(f"#{name}" for name in names), {f"#{name}": name for name in names}
_TABLE_REQUIRED_FIELDS = {
    "admin-config": {"hermes_autonomy_enabled"},
    "connector-cache": {"source_id", "coin", "fetched_at", "ttl"},
    "scheduler-run": {"run_id", "ts", "status"},
    "cost-ledger": {"run_id", "ts", "status", "cost_usd"},
}
_TABLE_ALLOWED_FIELDS = {
    **_TABLE_REQUIRED_FIELDS,
    "admin-config": {"hermes_autonomy_enabled", "version", "updated_at"},
    "connector-cache": {"source_id", "coin", "fetched_at", "ttl"},
    "scheduler-run": {"run_id", "ts", "status", "release_identity", "error_class"},
    "cost-ledger": {"run_id", "ts", "status", "release_identity", "cost_usd", "error_class"},
}
_DYNAMO_NONNEGATIVE_NUMBER_RE = re.compile(r"(?:0|[1-9]\d*)(?:\.\d+)?\Z")
_DYNAMO_SAFE_TEXT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}\Z")
_DYNAMO_SAFE_TEXT_FIELDS = {"source_id", "run_id", "status", "release_identity", "error_class", "version"}
_DYNAMO_NUMERIC_FIELDS = {"fetched_at", "ttl", "cost_usd"}


def _is_nonnegative_number(value: Any) -> bool:
    return isinstance(value, str) and _DYNAMO_NONNEGATIVE_NUMBER_RE.fullmatch(value) is not None


def _is_utc_or_nonnegative_epoch(value: Any) -> bool:
    if _is_nonnegative_number(value):
        return True
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _is_safe_dynamo_text(value: Any) -> bool:
    return (
        isinstance(value, str)
        and _DYNAMO_SAFE_TEXT_RE.fullmatch(value) is not None
        and not has_secret_like_value(value)
    )


@dataclass(frozen=True, slots=True)
class TableBinding:
    table_type: str
    table_name: str

    def __post_init__(self) -> None:
        if (
            self.table_type not in TABLE_TYPE_ALLOWLIST
            or self.table_name != _APPROVED_TABLE_NAMES.get(self.table_type)
        ):
            raise AuditContractError("table binding is not approved")

    @classmethod
    def configured(cls, remote_config: Mapping[str, str] | None = None) -> tuple["TableBinding", ...]:
        """Return the approved production table bindings.

        `remote_config` (if given) is the already schema-validated SSM
        snapshot ``config`` mapping -- never `os.environ` (that stays
        permanently unable to redirect a binding). A table-name flag's
        remote value is only ever adopted when it matches *this* table
        type's own approved name; any other value that isn't literally
        "absent" -- a syntactically valid but non-approved name, *or* the
        `_TABLE_NAME_INVALID_SENTINEL` produced upstream for a present-but-
        malformed value -- is fail-closed: the binding for that type is
        dropped entirely (never silently falls back to the static table),
        so the caller ends up treating that table as unavailable rather
        than reading whatever table the remote value actually pointed at
        (or guessing at one for a malformed value).
        """
        bindings = []
        for table_type in TABLE_TYPE_ALLOWLIST:
            approved = _APPROVED_TABLE_NAMES.get(table_type)
            if approved is None:
                continue
            table_name = approved
            if remote_config is not None:
                flag_name = _TABLE_TYPE_NAME_FLAGS.get(table_type)
                remote_value = remote_config.get(flag_name) if flag_name else None
                if remote_value is not None and remote_value != "absent":
                    # Covers both "syntactically valid but not this type's
                    # approved name" and the invalid-value sentinel: neither
                    # is ever adopted, and neither falls back silently.
                    if remote_value != approved:
                        continue
                    table_name = remote_value
            bindings.append(cls(table_type, table_name))
        return tuple(bindings)


class DynamoAuditReader:
    """Bounded reader with no write, query-expression, or table CLI surface."""

    def __init__(
        self,
        client: Any,
        limits: AuditLimits,
        bindings: tuple[TableBinding, ...] | None = None,
        *,
        deadline: float | None = None,
        now: Callable[[], float] = time.monotonic,
    ):
        self._client = client
        self._limits = limits
        self._bindings = bindings if bindings is not None else TableBinding.configured()
        self._deadline = deadline
        self._now = now
        expected_types = tuple(item for item in TABLE_TYPE_ALLOWLIST if item != "durable-dead-letter")
        types = tuple(item.table_type for item in self._bindings)
        # A fail-closed table-name resolution (see TableBinding.configured) may
        # legitimately drop a binding, so bindings are no longer required to
        # cover every expected type -- but must still be a duplicate-free,
        # order-preserving subset of it (never an unknown type, never
        # reordered, never repeated).
        if len(set(types)) != len(types) or tuple(item for item in expected_types if item in types) != types:
            raise AuditContractError("Dynamo bindings must be an ordered subset of the approved type allowlist")

    def collect(
        self,
    ) -> tuple[ControlState, DataPlane, tuple[DeadLetterSummary, ...], tuple[EvidenceRef, ...], tuple[str, ...]]:
        rows: dict[str, list[Mapping[str, Any]]] = {}
        audits: list[TableAudit] = []
        refs: list[EvidenceRef] = []
        warnings: list[str] = []
        requests = 0
        for table_type in TABLE_TYPE_ALLOWLIST:
            if table_type == "durable-dead-letter":
                audit = TableAudit(table_type, AuditStatus.INSUFFICIENT_EVIDENCE, 0, 0, 0, False)
                table_rows = []
                ref = EvidenceRef(
                    "dynamodb-read-v1", _utc_now(), "durable-dead-letter-sqlite-only",
                    False, sha256_digest({"table_type": table_type, "status": "not-a-dynamodb-source"}),
                )
                warning = "dynamodb-durable-dead-letter-insufficient-evidence"
            else:
                if self._deadline is not None and self._now() >= self._deadline:
                    raise AuditPartial("audit-deadline-exhausted")
                binding = next((item for item in self._bindings if item.table_type == table_type), None)
                if binding is None:
                    # Fail-closed table-name resolution dropped this type's
                    # binding (see TableBinding.configured): never attempt a
                    # Dynamo call, just report it as unavailable.
                    audit = TableAudit(table_type, AuditStatus.INSUFFICIENT_EVIDENCE, 0, 0, 0, False)
                    table_rows = []
                    ref = EvidenceRef(
                        "dynamodb-read-v1", _utc_now(), f"{table_type}-table-name-not-approved",
                        False, sha256_digest({"table_type": table_type, "status": "not-approved"}),
                    )
                    warning = f"dynamodb-{table_type}-table-name-not-approved"
                else:
                    audit, table_rows, ref, warning = self._collect_table(binding, requests)
            requests += audit.requests_used
            audits.append(audit)
            rows[table_type] = table_rows
            refs.append(ref)
            if warning:
                warnings.append(warning)
        by_type = {item.table_type: item for item in audits}
        scheduler = AggregateSummary(by_type["scheduler-run"].status, len(rows["scheduler-run"]), by_type["scheduler-run"].truncated)
        cache = AggregateSummary(by_type["connector-cache"].status, len(rows["connector-cache"]), by_type["connector-cache"].truncated)
        cost = AggregateSummary(by_type["cost-ledger"].status, len(rows["cost-ledger"]), by_type["cost-ledger"].truncated)
        return _admin_state(rows["admin-config"]), DataPlane(tuple(audits), scheduler, cache, cost), _dead_letters(rows["durable-dead-letter"]), tuple(refs), tuple(warnings)

    def _reserve_request(self, used: int) -> int:
        if self._deadline is not None and self._now() >= self._deadline:
            raise AuditPartial("audit-deadline-exhausted")
        limit = self._limits.read_budgets["dynamodb"].request_limit
        if used >= limit:
            raise AuditPartial("dynamodb-request-budget-exhausted")
        _ensure_call_window(
            self._deadline, self._now, self._limits.read_budgets["dynamodb"].timeout_seconds,
            "audit-deadline-call-window-exhausted",
        )
        return used + 1

    def _collect_table(
        self,
        binding: TableBinding,
        requests_used: int,
    ) -> tuple[TableAudit, list[Mapping[str, Any]], EvidenceRef, str | None]:
        budget = self._limits.read_budgets["dynamodb"]
        requests = requests_used
        ttl_status = ControlState.UNKNOWN
        try:
            requests = self._reserve_request(requests)
            meta = self._client.describe_table(TableName=binding.table_name)
            key_schema = meta.get("Table", {}).get("KeySchema", []) if isinstance(meta, Mapping) else []
            if {item.get("AttributeName") for item in key_schema if isinstance(item, Mapping)} != _REQUIRED_KEYS[binding.table_type]:
                raise AuditPartial("dynamodb-key-schema-drift")
            requests = self._reserve_request(requests)
            ttl = self._client.describe_time_to_live(TableName=binding.table_name)
            ttl_raw = ttl.get("TimeToLiveDescription", {}).get("TimeToLiveStatus") if isinstance(ttl, Mapping) else None
            ttl_status = ControlState.ENABLED if ttl_raw == "ENABLED" else ControlState.DISABLED if ttl_raw == "DISABLED" else ControlState.UNKNOWN
            if binding.table_type == "admin-config":
                requests = self._reserve_request(requests)
                expression, attribute_names = _projection(_ADMIN_PROJECTION)
                response = self._client.get_item(
                    TableName=binding.table_name,
                    Key={"source_id": {"S": "__admin_config__"}, "coin": {"S": "_"}},
                    ProjectionExpression=expression,
                    ExpressionAttributeNames=attribute_names,
                    ConsistentRead=False,
                )
                raw_items = [response["Item"]] if isinstance(response, Mapping) and isinstance(response.get("Item"), Mapping) else []
            else:
                requests = self._reserve_request(requests)
                expression, attribute_names = _projection(_PROJECTIONS[binding.table_type])
                response = self._client.scan(
                    TableName=binding.table_name,
                    ProjectionExpression=expression,
                    ExpressionAttributeNames=attribute_names,
                    Limit=budget.item_limit,
                    ConsistentRead=False,
                )
                raw_items = response.get("Items", []) if isinstance(response, Mapping) and isinstance(response.get("Items"), list) else []
            if len(raw_items) > budget.item_limit:
                raise AuditPartial("dynamodb-item-budget-exceeded")
            redact_or_reject_remote_payload(raw_items)
            raw_encoded = canonical_json(raw_items)
            if len(raw_encoded) > budget.byte_limit:
                raise AuditPartial("dynamodb-raw-byte-budget-exceeded")
            compact = []
            for item in raw_items:
                decoded = _decode_item(item, binding.table_type) if isinstance(item, Mapping) else None
                if decoded is None:
                    raise AuditPartial("dynamodb-item-schema-invalid")
                compact.append(decoded)
            encoded = canonical_json(compact)
            if len(encoded) > budget.byte_limit:
                raise AuditPartial("dynamodb-byte-budget-exceeded")
            truncated = bool(response.get("LastEvaluatedKey")) if isinstance(response, Mapping) else False
            status = AuditStatus.COMPLETE if compact and not truncated else AuditStatus.INSUFFICIENT_EVIDENCE
            ref = EvidenceRef("dynamodb-read-v1", _utc_now(), f"{binding.table_type}-allowlisted-summary", truncated, sha256_digest(compact))
            table_requests = requests - requests_used
            return TableAudit(binding.table_type, status, table_requests, len(compact), len(encoded), truncated, ttl_status), compact, ref, None
        except AuditContractError:
            raise
        except AuditPartial as exc:
            if str(exc) in {"audit-deadline-exhausted", "dynamodb-request-budget-exhausted"}:
                raise
            error_class = "SchemaOrBudgetError"
        except Exception as exc:
            error_class = type(exc).__name__
        warning = f"dynamodb-{binding.table_type}-{error_class}"
        ref = EvidenceRef("dynamodb-read-v1", _utc_now(), f"{binding.table_type}-unavailable", False, sha256_digest({"warning": warning}))
        table_requests = requests - requests_used
        return TableAudit(binding.table_type, AuditStatus.INSUFFICIENT_EVIDENCE, table_requests, 0, 0, False, ttl_status), [], ref, warning


def _decode_value(value: Any) -> Any:
    if not isinstance(value, Mapping) or len(value) != 1:
        return None
    kind, raw = next(iter(value.items()))
    if kind == "S" and isinstance(raw, str):
        return raw
    if kind == "N" and isinstance(raw, str) and re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
        return raw
    if kind == "BOOL" and isinstance(raw, bool):
        return raw
    return None


def _decode_item(value: Mapping[str, Any], table_type: str) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    allowed = _TABLE_ALLOWED_FIELDS.get(table_type, set())
    if not value or any(str(key) not in allowed for key in value):
        return None
    decoded: dict[str, Any] = {}
    for key, item in value.items():
        item_value = _decode_value(item)
        if item_value is None:
            return None
        decoded[str(key)] = item_value
    if not _TABLE_REQUIRED_FIELDS[table_type].issubset(decoded):
        return None
    for name in _TABLE_REQUIRED_FIELDS[table_type]:
        item_value = decoded[name]
        if name == "hermes_autonomy_enabled" and not isinstance(item_value, bool):
            return None
        if name == "coin" and (not isinstance(item_value, str) or re.fullmatch(r"[A-Z]{2,16}", item_value) is None):
            return None
        if name in _DYNAMO_NUMERIC_FIELDS and not _is_nonnegative_number(item_value):
            return None
        if name == "ts" and not _is_utc_or_nonnegative_epoch(item_value):
            return None
        if name in {"source_id", "run_id", "status"} and not _is_safe_dynamo_text(item_value):
            return None
    for name in _DYNAMO_SAFE_TEXT_FIELDS & decoded.keys():
        if not _is_safe_dynamo_text(decoded[name]):
            return None
    if "updated_at" in decoded and not _is_utc_or_nonnegative_epoch(decoded["updated_at"]):
        return None
    return decoded


def _admin_state(rows: list[Mapping[str, Any]]) -> ControlState:
    if not rows:
        return ControlState.UNKNOWN
    return ControlState.ENABLED if rows[0].get("hermes_autonomy_enabled") is True else ControlState.DISABLED if rows[0].get("hermes_autonomy_enabled") is False else ControlState.UNKNOWN


def _dead_letters(rows: list[Mapping[str, Any]]) -> tuple[DeadLetterSummary, ...]:
    output = []
    for item in rows:
        job, coin, stage, attempt, error, retry = (item.get(name) for name in ("job_id", "coin", "stage", "attempt", "error_class", "retry_state"))
        if not all(isinstance(value, str) and value for value in (job, coin, stage, error, retry)):
            continue
        try:
            output.append(DeadLetterSummary(sha256_digest({"job_id": job}), coin.upper(), stage, int(attempt or "0"), error, retry, item.get("release_identity") if isinstance(item.get("release_identity"), str) else None))
        except (ValueError, AuditContractError):
            continue
    return tuple(output)


def _dead_letter_evidence(
    remote: Mapping[str, Any], budget: ReadBudget
) -> tuple[TableAudit, tuple[DeadLetterSummary, ...], EvidenceRef, str | None]:
    """Turn an already schema-validated `dead_letters` SSM snapshot section
    into durable-dead-letter evidence, overlaying the DynamoDB collector's
    placeholder for that table type.

    Fail-closed: an unavailable source (missing file/table, lock, or any
    other read failure inside the SSM script) or an over-budget payload
    degrades this one table to INSUFFICIENT_EVIDENCE without raising --
    mirroring `_collect_table`'s own local error handling -- rather than
    failing the whole audit. An *available* table with zero rows is
    reported COMPLETE: absence of dead letters is not itself evidence of
    missing evidence (see plan R4 / test #2).
    """
    table_type = "durable-dead-letter"
    if not remote.get("available"):
        ref = EvidenceRef(
            "local-io-read-v1", _utc_now(), "durable-dead-letter-unavailable",
            False, sha256_digest({"table_type": table_type, "status": "unavailable"}),
        )
        return (
            TableAudit(table_type, AuditStatus.INSUFFICIENT_EVIDENCE, 0, 0, 0, False),
            (), ref, "dynamodb-durable-dead-letter-insufficient-evidence",
        )
    letters = _dead_letters(remote.get("rows", []))
    encoded = canonical_json([asdict(item) for item in letters])
    if len(encoded) > budget.byte_limit:
        ref = EvidenceRef(
            "local-io-read-v1", _utc_now(), "durable-dead-letter-unavailable",
            False, sha256_digest({"table_type": table_type, "status": "byte-budget-exceeded"}),
        )
        return (
            TableAudit(table_type, AuditStatus.INSUFFICIENT_EVIDENCE, 0, 0, 0, False),
            (), ref, "local-io-byte-budget-exceeded",
        )
    truncated = bool(remote.get("truncated"))
    status = AuditStatus.INSUFFICIENT_EVIDENCE if truncated else AuditStatus.COMPLETE
    ref = EvidenceRef(
        "local-io-read-v1", _utc_now(), "durable-dead-letter-summary",
        truncated, sha256_digest([asdict(item) for item in letters]),
    )
    audit_row = TableAudit(table_type, status, 0, len(letters), len(encoded), truncated)
    warning = "dynamodb-durable-dead-letter-insufficient-evidence" if status is not AuditStatus.COMPLETE else None
    return audit_row, letters, ref, warning


def _new_audit_id() -> str:
    return "audit-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:8]


def build_blocked_bundle(target: AuditTarget, reason: str, limits: AuditLimits | None = None) -> AuditBundle:
    active = limits or AuditLimits.defaults()
    return AuditBundle.build(audit_id=_new_audit_id(), captured_at=_utc_now(), target=target, invoker_identity_hash=sha256_digest({"blocked": "identity-unavailable"}), limits=active, overall_status=AuditStatus.BLOCKED, blockers=(reason,), control_plane=_placeholder_control(AuditStatus.BLOCKED), data_plane=_placeholder_data(AuditStatus.BLOCKED))


def run_audit(
    target: AuditTarget,
    clients: AwsAuditClients,
    *,
    limits: AuditLimits | None = None,
    expected_release: str | None = None,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> AuditBundle:
    """Collect one bounded audit; failures become explicit blocked/partial evidence."""
    if expected_release is not None and SAFE_RELEASE_RE.fullmatch(expected_release) is None:
        raise AuditContractError("expected release has invalid grammar")
    active = limits or AuditLimits.defaults()
    deadline = now() + active.overall_deadline_seconds
    try:
        invoker = _identity_hash(
            clients, deadline=deadline, now=now,
            timeout_seconds=min(item.timeout_seconds for item in active.read_budgets.values()),
        )
        if now() >= deadline:
            raise AuditPartial("audit-deadline-exhausted")
        preflight_target(
            clients, target, deadline=deadline, now=now,
            timeout_seconds=active.read_budgets["ssm"].timeout_seconds,
        )
    except AuditBlocked as exc:
        return build_blocked_bundle(target, str(exc), active)
    except AuditPartial as exc:
        return AuditBundle.build(
            audit_id=_new_audit_id(), captured_at=_utc_now(), target=target,
            invoker_identity_hash=invoker, limits=active,
            overall_status=AuditStatus.PARTIAL, blockers=(str(exc),),
            control_plane=_placeholder_control(AuditStatus.PARTIAL),
            data_plane=_placeholder_data(AuditStatus.INSUFFICIENT_EVIDENCE),
        )
    try:
        if now() >= deadline:
            raise AuditPartial("audit-deadline-exhausted")
        snapshot = _bounded_ssm_snapshot(clients, target, active, now, sleep, deadline)
        if now() >= deadline:
            raise AuditPartial("audit-deadline-exhausted")
        dynamodb_state, data, _, refs, warnings = DynamoAuditReader(
            clients.dynamodb, active,
            bindings=TableBinding.configured(remote_config=snapshot["config"]),
            deadline=deadline, now=now,
        ).collect()
        # Overlay the DynamoDB collector's durable-dead-letter placeholder with
        # the real read-only SQLite evidence gathered by the same SSM command
        # (see _dead_letter_evidence); matched by the placeholder's fixed
        # reduction_rule/warning constants, never by list position.
        dead_letter_audit, letters, dead_letter_ref, dead_letter_warning = _dead_letter_evidence(
            snapshot["dead_letters"], active.read_budgets["local-io"],
        )
        data = replace(
            data,
            table_audits=tuple(
                dead_letter_audit if item.table_type == "durable-dead-letter" else item
                for item in data.table_audits
            ),
        )
        refs = tuple(
            dead_letter_ref if ref.reduction_rule == "durable-dead-letter-sqlite-only" else ref
            for ref in refs
        )
        cleaned_warnings = []
        for warning in warnings:
            if warning == "dynamodb-durable-dead-letter-insufficient-evidence":
                if dead_letter_warning:
                    cleaned_warnings.append(dead_letter_warning)
                continue
            cleaned_warnings.append(warning)
        warnings = tuple(cleaned_warnings)
        control = control_from_snapshot(snapshot, dynamodb_state, expected_release)
        mismatch = any(item.status is ReleaseComparisonStatus.MISMATCH for item in control.release_comparison)
        release_unknown = any(item.status is ReleaseComparisonStatus.UNKNOWN for item in control.release_comparison)
        insufficient = any(item.status is not AuditStatus.COMPLETE for item in data.table_audits)
        unknown_control = control.effective_control.effective is ControlState.UNKNOWN
        blockers = []
        if mismatch:
            blockers.append("release-mismatch")
        if release_unknown:
            blockers.append("release-evidence-unknown")
        if unknown_control:
            blockers.append("control-state-unknown")
        if insufficient:
            blockers.append("insufficient-evidence")
        if warnings:
            blockers.append("collector-warning")
        status = AuditStatus.PARTIAL if blockers else AuditStatus.COMPLETE
        refs = (EvidenceRef("ssm-static-command-v1", _utc_now(), "allowlisted-control-summary", False, sha256_digest(snapshot)),) + refs
        clean_warnings = tuple(value[:128] for value in warnings[:32] if re.fullmatch(r"[A-Za-z0-9-]+", value[:128]))
        return AuditBundle.build(audit_id=_new_audit_id(), captured_at=_utc_now(), target=target, invoker_identity_hash=invoker, limits=active, overall_status=status, warnings=clean_warnings, blockers=tuple(blockers), control_plane=control, data_plane=data, dead_letters=letters, evidence_refs=refs)
    except AuditContractError:
        return AuditBundle.build(audit_id=_new_audit_id(), captured_at=_utc_now(), target=target, invoker_identity_hash=invoker, limits=active, overall_status=AuditStatus.INTEGRITY_FAILURE, blockers=("integrity-redaction-failure",), control_plane=_placeholder_control(AuditStatus.INTEGRITY_FAILURE), data_plane=_placeholder_data(AuditStatus.INTEGRITY_FAILURE))
    except AuditPartial as exc:
        return AuditBundle.build(audit_id=_new_audit_id(), captured_at=_utc_now(), target=target, invoker_identity_hash=invoker, limits=active, overall_status=AuditStatus.PARTIAL, blockers=(str(exc),), control_plane=_placeholder_control(AuditStatus.PARTIAL), data_plane=_placeholder_data(AuditStatus.INSUFFICIENT_EVIDENCE))


def write_evidence_bundle(
    bundle: AuditBundle,
    output_dir: Path,
    *,
    signer: Callable[[AuditBundle], EvidenceAttestationV1],
) -> Path:
    """Write a local bundle atomically; never uploads, commits, or uses a network API.

    Every call signs the bundle via ``signer`` and writes the resulting
    attestation as ``ATTESTATION.json`` -- unconditionally, for every
    ``overall_status`` (including blocked/partial/integrity-failure), so
    evidence is never left with only a self-consistent SHA-256 that its own
    author could recompute.
    """
    root = _validated_output_dir(output_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = root / bundle.audit_id
    staging = root / f".{bundle.audit_id}.{uuid.uuid4().hex}.incomplete"
    created = False
    try:
        staging.mkdir(parents=True, exist_ok=False, mode=0o700)
        created = True
        evidence_path = staging / "evidence.json"
        evidence_path.write_bytes(canonical_json(bundle.to_dict()) + b"\n")
        summary_path = staging / "summary.md"
        summary_path.write_text("\n".join(("# Hermes Production Audit", "", f"- Status: `{bundle.overall_status.value}`", f"- Audit ID: `{bundle.audit_id}`", f"- Evidence digest: `{bundle.canonical_payload_sha256}`", f"- Blockers: `{', '.join(bundle.blockers) if bundle.blockers else 'none'}`", "- Disposition: this evidence is read-only and does not authorize feature flags or rollout.")) + "\n", encoding="utf-8")
        attestation = signer(bundle)
        if (
            not isinstance(attestation, EvidenceAttestationV1)
            or attestation.audit_id != bundle.audit_id
            or attestation.canonical_payload_sha256 != bundle.canonical_payload_sha256
        ):
            raise AuditContractError("evidence attestation does not match this audit bundle")
        attestation_path = staging / "ATTESTATION.json"
        attestation_path.write_bytes(canonical_json(attestation.to_dict()) + b"\n")
        checksums = "\n".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in (evidence_path, summary_path, attestation_path)) + "\n"
        (staging / "SHA256SUMS").write_text(checksums, encoding="utf-8")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"audit evidence destination already exists: {bundle.audit_id}")
        staging.rename(destination)
        return destination
    except Exception:
        if created:
            shutil.rmtree(staging, ignore_errors=True)
        raise
