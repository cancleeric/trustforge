#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
output=$(mktemp -d)
trap 'rm -rf "$output"' EXIT
cd "$repo_root"
TRUSTFORGE_ROOT=/srv/trustforge TRUSTFORGE_PYTHON=/usr/bin/python3.14 \
  bash deploy/render_intrinsic_promotion_scheduler.sh "$output"
grep -q 'Persistent=true' "$output/trustforge-intrinsic-promotion.timer"
grep -q 'RandomizedDelaySec=120' "$output/trustforge-intrinsic-promotion.timer"
grep -q 'NoNewPrivileges=true' "$output/trustforge-intrinsic-promotion.service"
grep -q 'Restart=on-failure' "$output/trustforge-intrinsic-promotion.service"
grep -q ' run --shadow-db ' "$output/trustforge-intrinsic-promotion.service"
grep -q 'ReadOnlyPaths=/var/lib/trustforge/shadow/shadow.sqlite3' \
  "$output/trustforge-intrinsic-promotion.service"
for exact in \
  'User=trustforge-receipt' \
  'Group=trustforge-release' \
  'UMask=0077' \
  'LoadCredential=receipt-keyring.json:/etc/trustforge/keys/intrinsic-promotion-receipt.json' \
  'LoadCredential=shadow-release-identity.json:/etc/trustforge/shadow-release-identity.json' \
  'ProtectSystem=strict' \
  'PrivateDevices=true' \
  'ProtectKernelModules=true' \
  'RestrictAddressFamilies=AF_UNIX' \
  'CapabilityBoundingSet=' \
  'AmbientCapabilities=' \
  'ReadWritePaths=/var/lib/trustforge/security-ledger' \
  'ReadOnlyPaths=/opt/trustforge/manifest.json' \
  'ReadOnlyPaths=/opt/trustforge/app.zip'; do
  grep -Fxq "$exact" "$output/trustforge-intrinsic-promotion.service"
done
[[ $(grep -c '^ExecStart=' "$output/trustforge-intrinsic-promotion.service") -eq 1 ]]
grep -q 'ReadWritePaths=/var/lib/trustforge/security-ledger' \
  "$output/trustforge-intrinsic-promotion.service"
! grep -Eq 'systemctl|/etc/systemd/system' \
  deploy/render_intrinsic_promotion_scheduler.sh
