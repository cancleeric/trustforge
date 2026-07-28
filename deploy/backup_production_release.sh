#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
REGION="${REGION:-ap-southeast-2}"
RECEIPT="${TRUSTFORGE_BACKUP_RECEIPT:?TRUSTFORGE_BACKUP_RECEIPT is required}"
BACKUP_ROOT="${TRUSTFORGE_BACKUP_ROOT:-$PWD/out/release-train/backups}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="trustforge-deploy-${ACCOUNT}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d)"
VERIFY="$(mktemp -d)"
trap 'rm -rf "$WORK" "$VERIFY"' EXIT

mkdir -p "$BACKUP_ROOT" "$(dirname "$RECEIPT")"
chmod 700 "$BACKUP_ROOT"
aws s3 cp "s3://${BUCKET}/pointers/active.json" "$WORK/active.json" --region "$REGION" >/dev/null
aws s3 cp "s3://${BUCKET}/pointers/previous.json" "$WORK/previous.json" --region "$REGION" >/dev/null
DIGEST="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["digest"])' "$WORK/active.json")"
[[ "$DIGEST" =~ ^[0-9a-f]{64}$ ]] || { echo "invalid active artifact digest" >&2; exit 1; }
aws s3 cp "s3://${BUCKET}/artifacts/${DIGEST}/artifact.zip" "$WORK/artifact.zip" --region "$REGION" >/dev/null
aws s3 cp "s3://${BUCKET}/artifacts/${DIGEST}/manifest.json" "$WORK/manifest.json" --region "$REGION" >/dev/null
printf '%s  artifact.zip\n' "$DIGEST" > "$WORK/SHA256SUMS"
(cd "$WORK" && shasum -a 256 -c SHA256SUMS)
bash deploy/verify_cost_ledger_pitr.sh --verify

ARCHIVE="$BACKUP_ROOT/trustforge-production-${STAMP}-${DIGEST:0:12}.tar.gz"
tar -C "$WORK" -czf "$ARCHIVE" active.json previous.json artifact.zip manifest.json SHA256SUMS
tar -C "$VERIFY" -xzf "$ARCHIVE"
(cd "$VERIFY" && shasum -a 256 -c SHA256SUMS)
python3 - "$VERIFY/active.json" "$VERIFY/previous.json" "$VERIFY/manifest.json" <<'PY'
import json, sys
for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise SystemExit(f"backup JSON is not an object: {path}")
PY
python3 - "$RECEIPT" "$ARCHIVE" "$DIGEST" <<'PY'
import json, os, sys, tempfile
from pathlib import Path

destination, archive, digest = map(Path, sys.argv[1:])
payload = {
    "archive": str(archive.resolve(strict=True)),
    "artifact_digest": str(digest),
    "restore_verified": True,
}
fd, temporary = tempfile.mkstemp(prefix=".backup-receipt.", dir=destination.parent)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
os.chmod(temporary, 0o600)
os.replace(temporary, destination)
PY
echo "$ARCHIVE"
