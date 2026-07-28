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
DEST_ROOT="${TRUSTFORGE_INSTALL_ROOT:-}"
UNIT_SOURCE="$ROOT_DIR/deploy/trustforge-release-router.service"
NGINX_SOURCE="$ROOT_DIR/deploy/trustforge-release-router.nginx.conf"
SYSUSERS_SOURCE="$ROOT_DIR/deploy/trustforge-release-router.sysusers.conf"
TMPFILES_SOURCE="$ROOT_DIR/deploy/trustforge-release-router.tmpfiles.conf"
UNIT_TARGET="$DEST_ROOT/etc/systemd/system/trustforge-release-router.service"
NGINX_TARGET="$DEST_ROOT/etc/nginx/snippets/trustforge-release-router.conf"
SYSUSERS_TARGET="$DEST_ROOT/etc/sysusers.d/trustforge-release-router.conf"
TMPFILES_TARGET="$DEST_ROOT/etc/tmpfiles.d/trustforge-release-router.conf"
CONFIG_ROOT="$DEST_ROOT/etc/trustforge"
LEDGER_ROOT="$DEST_ROOT/var/lib/trustforge/security-ledger"

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
    "setpriv trustforge-operator python3 scripts/deployment_readiness.py status" \
    "test -r runtime config, public keyring, signed endpoint manifests and A/B units" \
    "systemctl daemon-reload" \
    "nginx -t" \
    "systemctl start trustforge-release-router.service # not enabled until smoke passes" \
    "systemctl reload nginx" \
    "curl --unix-socket /run/trustforge/release-router.sock http://localhost/healthz" \
    "curl --netrc-file /dev/fd/9 --cacert <pinned-ca> https://<hostname>/healthz" \
    "systemctl enable trustforge-release-router.service"
  exit 0
fi

[[ "$(id -u)" -eq 0 ]] || {
  echo "installation requires root" >&2
  exit 77
}

mkdir -p "$(dirname "$UNIT_TARGET")" "$(dirname "$NGINX_TARGET")" \
  "$(dirname "$SYSUSERS_TARGET")" "$(dirname "$TMPFILES_TARGET")" \
  "$DEST_ROOT/var/tmp"
BACKUP_DIR="$(mktemp -d "$DEST_ROOT/var/tmp/trustforge-router-install.XXXXXX")"
chmod 0700 "$BACKUP_DIR"
TARGETS=("$UNIT_TARGET" "$NGINX_TARGET" "$SYSUSERS_TARGET" "$TMPFILES_TARGET")
EXISTED=()
for index in "${!TARGETS[@]}"; do
  target="${TARGETS[$index]}"
  if [[ -f "$target" && ! -L "$target" ]]; then
    cp -p "$target" "$BACKUP_DIR/$index"
    EXISTED[$index]=true
  else
    EXISTED[$index]=false
  fi
done
SERVICE_WAS_ACTIVE=false
systemctl is-active --quiet trustforge-release-router.service && SERVICE_WAS_ACTIVE=true
OLD_MAIN_PID="$(systemctl show -p MainPID --value trustforge-release-router.service 2>/dev/null || echo 0)"
rollback_install() {
  status=$?
  systemctl stop trustforge-release-router.service >/dev/null 2>&1 || true
  for index in "${!TARGETS[@]}"; do
    if ${EXISTED[$index]}; then
      install -o root -g root -m 0644 "$BACKUP_DIR/$index" "${TARGETS[$index]}"
    else
      rm -f -- "${TARGETS[$index]}"
    fi
  done
  systemctl daemon-reload >/dev/null 2>&1 || true
  nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
  if $SERVICE_WAS_ACTIVE; then
    systemctl start trustforge-release-router.service >/dev/null 2>&1 || true
  fi
  rm -rf -- "$BACKUP_DIR"
  exit "$status"
}
trap rollback_install ERR INT TERM

install -o root -g root -m 0644 "$UNIT_SOURCE" "$UNIT_TARGET"
install -o root -g root -m 0644 "$NGINX_SOURCE" "$NGINX_TARGET"
install -o root -g root -m 0644 "$SYSUSERS_SOURCE" "$SYSUSERS_TARGET"
install -o root -g root -m 0644 "$TMPFILES_SOURCE" "$TMPFILES_TARGET"
systemd-sysusers "$SYSUSERS_TARGET"
systemd-tmpfiles --create "$TMPFILES_TARGET"
# Deliberate non-reversible host mutations: sysusers may create the two service
# identities/group and usermod may add nginx to trustforge-release. Rollback
# restores service artifacts/state but never deletes identities or memberships.
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
for prerequisite in \
  "$CONFIG_ROOT/release-router-runtime.json" \
  "$CONFIG_ROOT/release-router-runtime-keys.json" \
  "$LEDGER_ROOT/control/bootstrap.json" \
  "$LEDGER_ROOT/router-outcomes/bootstrap.json"; do
  [[ -f "$prerequisite" && ! -L "$prerequisite" ]] || {
    echo "release router is not provisioned: missing $prerequisite" >&2
    exit 79
  }
done
systemctl cat trustforge-a.service >/dev/null
systemctl cat trustforge-b.service >/dev/null
setpriv \
  --reuid=trustforge-operator \
  --regid=trustforge-operator \
  --init-groups \
  python3 "$ROOT_DIR/scripts/deployment_readiness.py" status >/dev/null || {
  echo "release ledgers/configuration did not authenticate; provision or migrate first" >&2
  exit 80
}
systemctl daemon-reload
nginx -t
if $SERVICE_WAS_ACTIVE; then
  systemctl restart trustforge-release-router.service
else
  systemctl start trustforge-release-router.service
fi
NEW_MAIN_PID="$(systemctl show -p MainPID --value trustforge-release-router.service)"
[[ "$NEW_MAIN_PID" =~ ^[1-9][0-9]*$ ]] || {
  echo "release router did not expose a live MainPID" >&2
  false
}
if $SERVICE_WAS_ACTIVE && [[ "$NEW_MAIN_PID" == "$OLD_MAIN_PID" ]]; then
  echo "release router upgrade did not replace the running process" >&2
  false
fi
systemctl reload nginx
setpriv \
  "--reuid=$NGINX_WORKER_USER" \
  "--regid=$NGINX_WORKER_USER" \
  --init-groups \
  python3 -c \
  'import socket; s=socket.socket(socket.AF_UNIX); s.connect("/run/trustforge/release-router.sock"); s.close()'
curl --fail --silent --show-error --unix-socket /run/trustforge/release-router.sock \
  -H 'X-TrustForge-Trusted-Subject: installer-smoke' http://localhost/healthz >/dev/null
SMOKE_NETRC="${TRUSTFORGE_SMOKE_NETRC:?set path to root-only smoke netrc}"
SMOKE_CA="${TRUSTFORGE_SMOKE_CA:?set path to pinned CA bundle}"
SMOKE_HOST="${TRUSTFORGE_SMOKE_HOST:?set authenticated TLS hostname}"
for secret_file in "$SMOKE_NETRC" "$SMOKE_CA"; do
  [[ -f "$secret_file" && ! -L "$secret_file" && "$(stat -c %u "$secret_file")" == 0 ]] || {
    echo "smoke credential/CA file is unsafe" >&2
    false
  }
done
[[ "$(stat -c %a "$SMOKE_NETRC")" == 600 ]] || {
  echo "smoke netrc must be root-only 0600" >&2
  false
}
exec 9<"$SMOKE_NETRC"
[[ "$(stat -Lc %u /proc/$$/fd/9)" == 0 && "$(stat -Lc %a /proc/$$/fd/9)" == 600 ]] || {
  echo "opened smoke netrc descriptor metadata changed" >&2
  false
}
curl --fail --silent --show-error --netrc-file /dev/fd/9 \
  --cacert "$SMOKE_CA" --resolve "$SMOKE_HOST:443:127.0.0.1" \
  "https://$SMOKE_HOST/healthz" >/dev/null
exec 9<&-
systemctl enable trustforge-release-router.service
trap - ERR INT TERM
rm -rf -- "$BACKUP_DIR"
