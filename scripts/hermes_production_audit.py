#!/usr/bin/env python3
"""Run or dry-run the bounded, read-only Hermes production audit."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trustforge.hermes_audit import (
    create_aws_clients,
    dry_run_plan,
    run_audit,
    write_evidence_bundle,
)
from trustforge.hermes_audit_contracts import (
    AuditContractError,
    AuditLimits,
    AuditStatus,
    AuditTarget,
    exit_code_for,
)

_APPROVAL_RECORD_KEYS = {
    "ceo_window",
    "cpo_approval",
    "ciso_approval",
    "second_person_dry_run_review",
    "region",
    "instance_id",
    "expires_at",
}


def _validate_approval_record(path: Path | None, target: AuditTarget) -> None:
    if path is None:
        raise AuditContractError("non-dry audit requires --approval-record")
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
            raise AuditContractError("approval record must be a bounded regular file")
        record = json.loads(path.read_text(encoding="utf-8"))
    except AuditContractError:
        raise
    except Exception as exc:
        raise AuditContractError("approval record cannot be read") from exc
    if not isinstance(record, dict) or set(record) != _APPROVAL_RECORD_KEYS:
        raise AuditContractError("approval record schema is invalid")
    if any(record[key] is not True for key in ("ceo_window", "cpo_approval", "ciso_approval", "second_person_dry_run_review")):
        raise AuditContractError("approval record lacks required human attestations")
    if record["region"] != target.region or record["instance_id"] != target.instance_id:
        raise AuditContractError("approval record target does not match audit target")
    try:
        expires = datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise AuditContractError("approval record expiry is invalid") from exc
    if expires.tzinfo is None or expires.utcoffset() != timezone.utc.utcoffset(expires) or expires <= datetime.now(timezone.utc):
        raise AuditContractError("approval record is expired or not UTC")


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Hermes production audit")
    parser.add_argument("--region", required=True, help="Explicit AWS region")
    parser.add_argument("--instance-id", required=True, help="Explicit SSM-managed EC2 instance ID")
    parser.add_argument("--output-dir", required=True, type=Path, help="Ignored directory under repository out/")
    parser.add_argument("--expected-release", help="Optional expected deployed release identity")
    parser.add_argument("--approval-record", type=Path, help="Required human approval/second-person review record for non-dry audits")
    parser.add_argument("--dry-run", action="store_true", help="Print static plan without constructing AWS clients")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    limits = AuditLimits.defaults()
    try:
        target = AuditTarget(args.region, args.instance_id)
        if args.dry_run:
            print(json.dumps(dry_run_plan(target, args.output_dir, limits, args.expected_release), sort_keys=True))
            return 0
        _validate_approval_record(args.approval_record, target)
        bundle = run_audit(
            target,
            create_aws_clients(target.region, limits),
            limits=limits,
            expected_release=args.expected_release,
        )
        output = write_evidence_bundle(bundle, args.output_dir)
        print(json.dumps({
            "status": bundle.overall_status.value,
            "audit_id": bundle.audit_id,
            "canonical_payload_sha256": bundle.canonical_payload_sha256,
            "output_dir": str(output),
        }, sort_keys=True))
        return exit_code_for(bundle.overall_status)
    except AuditContractError:
        print(json.dumps({"status": AuditStatus.INTERNAL_FAILURE.value, "error_class": "AuditContractError"}), file=sys.stderr)
        return exit_code_for(AuditStatus.INTERNAL_FAILURE)
    except Exception as exc:
        print(json.dumps({"status": AuditStatus.INTERNAL_FAILURE.value, "error_class": type(exc).__name__}), file=sys.stderr)
        return exit_code_for(AuditStatus.INTERNAL_FAILURE)


if __name__ == "__main__":
    raise SystemExit(main())
