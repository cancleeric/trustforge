#!/usr/bin/env bash
set -euo pipefail

service_file="${TRUSTFORGE_PREVIEW_SERVICE_FILE:-/etc/systemd/system/trustforge.service}"
requested_raw="${service_file}.preview.requested.$$"
requested_sorted="${service_file}.preview.sorted.$$"
tmp="${service_file}.preview.$$"
trap 'rm -f "$tmp" "$requested_raw" "$requested_sorted"' EXIT
enabled_requested=0
for assignment in "$@"; do
  [[ "$assignment" =~ ^(TRUSTFORGE_PREVIEW_[A-Z0-9_]+)=([A-Za-z0-9_./:-]+)$ ]] || exit 2
  printf '%s\n' "$assignment" >> "$requested_raw"
  [ "${BASH_REMATCH[1]}" = "TRUSTFORGE_PREVIEW_ADMISSION_ENABLED" ] && enabled_requested=1
done
[ "$enabled_requested" -eq 1 ] || exit 2

awk -F= '{latest[$1]=$0} END {for (key in latest) print latest[key]}' \
  "$requested_raw" | LC_ALL=C sort > "$requested_sorted"
inserted=0
while IFS= read -r line; do
  [[ "$line" == Environment=TRUSTFORGE_PREVIEW_* ]] && continue
  printf '%s\n' "$line" >> "$tmp"
  if [ "$line" = "Environment=PYTHONPATH=/opt/trustforge" ]; then
    while IFS= read -r requested_line; do
      printf 'Environment=%s\n' "$requested_line" >> "$tmp"
    done < "$requested_sorted"
    inserted=1
  fi
done < "$service_file"
[ "$inserted" -eq 1 ] || exit 1
chmod 600 "$tmp"
mv "$tmp" "$service_file"
rm -f "$requested_raw" "$requested_sorted"
trap - EXIT
