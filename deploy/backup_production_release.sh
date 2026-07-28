#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
REGION="${REGION:-ap-southeast-2}"
RECEIPT="${TRUSTFORGE_BACKUP_RECEIPT:?TRUSTFORGE_BACKUP_RECEIPT is required}"
RUN_ID="${TRUSTFORGE_RELEASE_RUN_ID:?TRUSTFORGE_RELEASE_RUN_ID is required}"
BACKUP_ROOT="${TRUSTFORGE_BACKUP_ROOT:-$PWD/out/release-train/backups}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="trustforge-deploy-${ACCOUNT}"
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

ARCHIVE="$BACKUP_ROOT/trustforge-production-${DIGEST}.tar.gz"
if [ ! -f "$ARCHIVE" ]; then
  tar -C "$WORK" -czf "$ARCHIVE" active.json previous.json artifact.zip manifest.json SHA256SUMS
fi
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
ARCHIVE_SHA256="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
python3 - "$RECEIPT" "$ARCHIVE" "$DIGEST" "$RUN_ID" "$ARCHIVE_SHA256" <<'PY'
import json, os, sys, tempfile
from pathlib import Path

destination = Path(sys.argv[1])
archive = Path(sys.argv[2])
digest, run_id, archive_sha256 = sys.argv[3:]
payload = {
    "schema": "trustforge.production-backup/v1",
    "run_id": run_id,
    "archive": str(archive.resolve(strict=True)),
    "archive_sha256": archive_sha256,
    "artifact_digest": digest,
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
