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
    "nginx -T # preflight: resolve the configured worker user" \
    "usermod -a -G trustforge-release <resolved-nginx-worker-user>" \
    "id -nG <resolved-nginx-worker-user> # verify trustforge-release membership" \
    "systemctl daemon-reload" \
    "nginx -t" \
    "systemctl enable --now trustforge-release-router.service" \
    "systemctl reload nginx" \
    "setpriv <resolved-nginx-worker-user> python3 -c # connect to router Unix socket"
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
NGINX_CONFIG="$(nginx -T 2>&1)"
NGINX_WORKER_USER="$(
  awk '$1 == "user" {gsub(/;/, "", $2); print $2; exit}' <<<"$NGINX_CONFIG"
)"
[[ -n "$NGINX_WORKER_USER" ]] || {
  echo "nginx worker user is not explicitly configured" >&2
  exit 78
}
id "$NGINX_WORKER_USER" >/dev/null
usermod -a -G trustforge-release "$NGINX_WORKER_USER"
id -nG "$NGINX_WORKER_USER" | tr ' ' '\n' | grep -Fx trustforge-release >/dev/null
systemctl daemon-reload
nginx -t
systemctl enable --now trustforge-release-router.service
systemctl reload nginx
setpriv \
  "--reuid=$NGINX_WORKER_USER" \
  "--regid=$NGINX_WORKER_USER" \
  --init-groups \
  python3 -c \
  'import socket; s=socket.socket(socket.AF_UNIX); s.connect("/run/trustforge/release-router.sock"); s.close()'
