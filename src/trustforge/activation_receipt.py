"""Activation receipt system: append-only, S3-backed, immutable evidence
of every activation attempt (success / rollback / rollback-failed).

Receipts are written to ``s3://<bucket>/pointers/receipts/index.jsonl`` as
one JSON line per activation attempt.

Usage::

    from trustforge.activation_receipt import ActivationReceipt, write_receipt_to_s3

    receipt = ActivationReceipt(
        activation_target="trustforge-demo",
        owner_id="deployer:abc123",
        candidate_digest="abc123def456",
        previous_active_digest="789012345678",
        status="completed",
        ...
    )
    write_receipt_to_s3(receipt, bucket="trustforge-deploy-123456789012",
                        region="ap-southeast-2")
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Literal


ReceiptStatus = Literal["completed", "rolled_back", "rollback_failed", "aborted"]


@dataclass(frozen=True)
class ActivationReceipt:
    """One activation attempt record, serialized as a JSONL line."""

    activation_target: str
    owner_id: str
    candidate_digest: str
    previous_active_digest: str
    status: str  # ReceiptStatus
    build_timestamp: str
    started_at: str
    finished_at: str = ""
    error: str = ""
    rollback_triggered: bool | int = False
    rollback_succeeded: bool | int = False
    receipt_version: str = "trustforge.activation-receipt/v1"

    def to_json_line(self) -> str:
        d = asdict(self)
        d["rollback_triggered"] = bool(self.rollback_triggered)
        d["rollback_succeeded"] = bool(self.rollback_succeeded)
        return json.dumps(d, sort_keys=True, ensure_ascii=False) + "\n"

    @classmethod
    def from_json_line(cls, line: str) -> ActivationReceipt:
        d = json.loads(line)
        return cls(**d)


RECEIPTS_KEY = "pointers/receipts/index.jsonl"


def write_receipt_to_s3(
    receipt: ActivationReceipt,
    *,
    bucket: str | None = None,
    region: str | None = None,
) -> bool:
    """Append *receipt* as one JSON line to the receipt index in S3.

    Returns True on success, False if the S3 write failed (non-fatal).
    """
    try:
        import boto3
        if region is None:
            region = os.getenv("AWS_REGION", "us-east-1")
        if bucket is None:
            account = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
            bucket = f"trustforge-deploy-{account}"
        s3 = boto3.client("s3", region_name=region)
        existing = ""
        try:
            resp = s3.get_object(Bucket=bucket, Key=RECEIPTS_KEY)
            body = resp["Body"].read().decode("utf-8")
            if body and not body.endswith("\n"):
                body += "\n"
            existing = body
        except Exception:
            existing = ""
        body = existing + receipt.to_json_line()
        s3.put_object(Bucket=bucket, Key=RECEIPTS_KEY, Body=body.encode("utf-8"))
        return True
    except Exception:
        return False


def read_receipts_from_s3(
    *,
    bucket: str | None = None,
    region: str | None = None,
) -> list[ActivationReceipt]:
    """Read all receipt entries from the S3 index.

    Returns an empty list if the key does not exist or the read fails.
    """
    try:
        import boto3
        if region is None:
            region = os.getenv("AWS_REGION", "us-east-1")
        if bucket is None:
            account = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
            bucket = f"trustforge-deploy-{account}"
        s3 = boto3.client("s3", region_name=region)
        resp = s3.get_object(Bucket=bucket, Key=RECEIPTS_KEY)
        body = resp["Body"].read().decode("utf-8")
        lines = [line for line in body.splitlines() if line.strip()]
        return [ActivationReceipt.from_json_line(line) for line in lines]
    except Exception:
        return []


def write_receipt_local(
    receipt: ActivationReceipt,
    *,
    path: str | None = None,
) -> bool:
    """Append *receipt* as one JSON line to a local file.

    Used in tests / offline development.  Returns True on success.
    """
    try:
        from pathlib import Path
        if path is None:
            home = os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[2]))
            path = str(Path(home) / "out" / "activation_receipts.jsonl")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        target.write_text(existing + receipt.to_json_line(), encoding="utf-8")
        return True
    except OSError:
        return False


def read_receipts_local(
    *,
    path: str | None = None,
) -> list[ActivationReceipt]:
    """Read all receipt entries from a local file."""
    try:
        from pathlib import Path
        if path is None:
            home = os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[2]))
            path = str(Path(home) / "out" / "activation_receipts.jsonl")
        target = Path(path)
        if not target.exists():
            return []
        body = target.read_text(encoding="utf-8")
        lines = [line for line in body.splitlines() if line.strip()]
        return [ActivationReceipt.from_json_line(line) for line in lines]
    except (OSError, json.JSONDecodeError):
        return []
