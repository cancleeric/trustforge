#!/usr/bin/env python3
"""Apply S3 retention policy for immutable A/B artifacts.

Computes the protected set from S3 index + pointers and produces a dry-run report.
Deletion only with explicit --execute flag.

Usage:
    python scripts/apply_retention_policy.py --dry-run
    python scripts/apply_retention_policy.py --execute --force

Env:
    S3_BUCKET: override bucket name (default: trustforge-deploy-<ACCT>)
    AWS_REGION / REGION: override region (default: ap-southeast-2)
    OBSERVATION_WINDOW_HOURS: override observation window (default: 24)
    CANARY_WINDOW_MINUTES: override canary window (default: 10)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from trustforge.artifact_retention import (
    RetentionPolicy,
    apply_retention_policy,
    render_retention_report,
)

DEFAULT_REGION = os.environ.get("AWS_REGION") or os.environ.get("REGION") or "ap-southeast-2"


def _get_bucket(region: str) -> tuple[str, str]:
    acct = subprocess.run(
        ["aws", "sts", "get-caller-identity", "--region", region, "--query", "Account", "--output", "text"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    bucket = os.environ.get("S3_BUCKET", f"trustforge-deploy-{acct}")
    return bucket, acct


def _list_artifacts(bucket: str, region: str) -> list[str]:
    result = subprocess.run(
        ["aws", "s3api", "list-objects-v2", "--bucket", bucket, "--region", region,
         "--prefix", "artifacts/", "--delimiter", "/",
         "--query", "CommonPrefixes[].Prefix"],
        capture_output=True, text=True, check=True,
    )
    prefixes = json.loads(result.stdout)
    digests = []
    for p in prefixes:
        idx = p.find("artifacts/") + len("artifacts/")
        digest = p[idx:].rstrip("/")
        if digest and len(digest) == 64:
            digests.append(digest)
    return digests


def _load_index(bucket: str, region: str) -> list[dict]:
    try:
        result = subprocess.run(
            ["aws", "s3", "cp", f"s3://{bucket}/artifacts/index.jsonl", "-", "--region", region],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        return []
    entries = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _load_pointer(bucket: str, region: str, key: str) -> dict | None:
    try:
        result = subprocess.run(
            ["aws", "s3", "cp", f"s3://{bucket}/{key}", "-", "--region", region],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def _delete_artifact(bucket: str, region: str, digest: str, dry_run: bool = True) -> bool:
    prefix = f"artifacts/{digest}/"
    if dry_run:
        print(f"  [DRY-RUN] would delete s3://{bucket}/{prefix}")
        return True
    try:
        keys_result = subprocess.run(
            ["aws", "s3api", "list-objects-v2", "--bucket", bucket, "--region", region,
             "--prefix", prefix, "--query", "Contents[].Key"],
            capture_output=True, text=True, check=True,
        )
        keys = json.loads(keys_result.stdout)
        if keys:
            objects = [{"Key": k} for k in keys]
            subprocess.run(
                ["aws", "s3api", "delete-objects", "--bucket", bucket, "--region", region,
                 "--delete", json.dumps({"Objects": objects})],
                capture_output=True, text=True, check=True,
            )
            print(f"  Deleted {len(keys)} objects under {prefix}")
        return True
    except subprocess.CalledProcessError as exc:
        print(f"  ERROR deleting {prefix}: {exc}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply S3 retention policy for artifacts")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Dry-run mode (default)")
    parser.add_argument("--execute", action="store_true", help="Actually delete artifacts")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    if not args.execute:
        print("=== DRY-RUN MODE ===")

    region = os.environ.get("AWS_REGION") or os.environ.get("REGION") or DEFAULT_REGION
    bucket, acct = _get_bucket(region)
    print(f"Bucket: {bucket}  Region: {region}  Account: {acct}")

    all_digests = _list_artifacts(bucket, region)
    print(f"Found {len(all_digests)} artifact digests in S3")

    index_entries = _load_index(bucket, region)
    print(f"Index entries: {len(index_entries)}")

    active_ptr = _load_pointer(bucket, region, "pointers/active.json")
    candidate_ptr = _load_pointer(bucket, region, "pointers/candidate.json")
    previous_ptr = _load_pointer(bucket, region, "pointers/previous.json")

    for entry in index_entries:
        digest = entry.get("digest", "")
        refs = []
        if active_ptr and active_ptr.get("digest") == digest:
            refs.append("pointers/active.json")
        if candidate_ptr and candidate_ptr.get("digest") == digest:
            refs.append("pointers/candidate.json")
        if previous_ptr and previous_ptr.get("digest") == digest:
            refs.append("pointers/previous.json")
        if refs:
            entry["pointers_referenced"] = refs

    obs_hours = int(os.environ.get("OBSERVATION_WINDOW_HOURS", "24"))
    canary_min = int(os.environ.get("CANARY_WINDOW_MINUTES", "10"))
    policy = RetentionPolicy(
        observation_window_hours=obs_hours,
        canary_window_minutes=canary_min,
    )

    protected, eligible = apply_retention_policy(index_entries, all_digests, policy)
    print(render_retention_report(protected, eligible, index_entries))
    print(f"\nProtected: {len(protected)}  Eligible: {len(eligible)}")
    print(f"Observation window: {obs_hours}h  Canary window: {canary_min}min")

    if not eligible:
        print("No artifacts eligible for deletion.")
        return 0

    if args.execute:
        if not args.force:
            confirm = input(f"\nDelete {len(eligible)} artifacts? [y/N] ")
            if confirm.lower() not in ("y", "yes"):
                print("Aborted.")
                return 0
        print(f"\nDeleting {len(eligible)} artifacts...")
        failed = 0
        for d in sorted(eligible):
            if not _delete_artifact(bucket, region, d, dry_run=not args.execute):
                failed += 1
        print(f"\nDone: {len(eligible) - failed} deleted, {failed} failed")

    print("\nTo delete: re-run with --execute --force")
    return 0


if __name__ == "__main__":
    sys.exit(main())
