#!/usr/bin/env bash
set -euo pipefail

service_file="${TRUSTFORGE_PREVIEW_SERVICE_FILE:-/etc/systemd/system/trustforge.service}"
declare -A requested=()
for assignment in "$@"; do
  [[ "$assignment" =~ ^(TRUSTFORGE_PREVIEW_[A-Z0-9_]+)=([A-Za-z0-9_./:-]+)$ ]] || exit 2
  requested["${BASH_REMATCH[1]}"]="${BASH_REMATCH[2]}"
done
[ -n "${requested[TRUSTFORGE_PREVIEW_ADMISSION_ENABLED]+x}" ] || exit 2

tmp="${service_file}.preview.$$"
trap 'rm -f "$tmp"' EXIT
inserted=0
while IFS= read -r line; do
  [[ "$line" == Environment=TRUSTFORGE_PREVIEW_* ]] && continue
  printf '%s\n' "$line" >> "$tmp"
  if [ "$line" = "Environment=PYTHONPATH=/opt/trustforge" ]; then
    while IFS= read -r key; do
      printf 'Environment=%s=%s\n' "$key" "${requested[$key]}" >> "$tmp"
    done < <(printf '%s\n' "${!requested[@]}" | LC_ALL=C sort)
    inserted=1
  fi
done < "$service_file"
[ "$inserted" -eq 1 ] || exit 1
chmod 600 "$tmp"
mv "$tmp" "$service_file"
trap - EXIT
