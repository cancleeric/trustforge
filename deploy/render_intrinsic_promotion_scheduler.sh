#!/usr/bin/env bash
set -euo pipefail

# #996 is render-only: it never mutates the service manager or deploys.
[[ $# -eq 1 ]] || { echo "usage: $0 OUTPUT_DIR" >&2; exit 2; }
output_dir=$1
mkdir -p "$output_dir"
chmod 700 "$output_dir"

sed "s|@PYTHON@|${TRUSTFORGE_PYTHON:-/usr/bin/python3}|g; s|@ROOT@|${TRUSTFORGE_ROOT:-/opt/trustforge}|g" \
  deploy/systemd/trustforge-intrinsic-promotion.service.in \
  > "$output_dir/trustforge-intrinsic-promotion.service"
cp deploy/systemd/trustforge-intrinsic-promotion.timer.in \
  "$output_dir/trustforge-intrinsic-promotion.timer"
chmod 600 "$output_dir/"*
