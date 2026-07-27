#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
elif [[ $# -ne 0 ]]; then
  echo "usage: $0 [--dry-run]" >&2
  exit 64
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SOURCE="$ROOT_DIR/deploy/trustforge-release-router.service"
NGINX_SOURCE="$ROOT_DIR/deploy/trustforge-release-router.nginx.conf"
SYSUSERS_SOURCE="$ROOT_DIR/deploy/trustforge-release-router.sysusers.conf"
TMPFILES_SOURCE="$ROOT_DIR/deploy/trustforge-release-router.tmpfiles.conf"
UNIT_TARGET="/etc/systemd/system/trustforge-release-router.service"
NGINX_TARGET="/etc/nginx/snippets/trustforge-release-router.conf"
SYSUSERS_TARGET="/etc/sysusers.d/trustforge-release-router.conf"
TMPFILES_TARGET="/etc/tmpfiles.d/trustforge-release-router.conf"

for source in "$UNIT_SOURCE" "$NGINX_SOURCE" "$SYSUSERS_SOURCE" "$TMPFILES_SOURCE"; do
  [[ -f "$source" && ! -L "$source" ]] || {
    echo "required regular source is absent: $source" >&2
    exit 2
  }
done

if $DRY_RUN; then
  printf '%s\n' \
    "install -o root -g root -m 0644 $UNIT_SOURCE $UNIT_TARGET" \
    "install -o root -g root -m 0644 $NGINX_SOURCE $NGINX_TARGET" \
    "install -o root -g root -m 0644 $SYSUSERS_SOURCE $SYSUSERS_TARGET" \
    "install -o root -g root -m 0644 $TMPFILES_SOURCE $TMPFILES_TARGET" \
    "systemd-sysusers $SYSUSERS_TARGET" \
    "systemd-tmpfiles --create $TMPFILES_TARGET" \
    "systemctl daemon-reload" \
    "nginx -t" \
    "systemctl enable --now trustforge-release-router.service" \
    "systemctl reload nginx"
  exit 0
fi

[[ "$(id -u)" -eq 0 ]] || {
  echo "installation requires root" >&2
  exit 77
}

install -o root -g root -m 0644 "$UNIT_SOURCE" "$UNIT_TARGET"
install -o root -g root -m 0644 "$NGINX_SOURCE" "$NGINX_TARGET"
install -o root -g root -m 0644 "$SYSUSERS_SOURCE" "$SYSUSERS_TARGET"
install -o root -g root -m 0644 "$TMPFILES_SOURCE" "$TMPFILES_TARGET"
systemd-sysusers "$SYSUSERS_TARGET"
systemd-tmpfiles --create "$TMPFILES_TARGET"
systemctl daemon-reload
nginx -t
systemctl enable --now trustforge-release-router.service
systemctl reload nginx
