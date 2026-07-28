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
UNIT_TEMPLATE="$ROOT_DIR/deploy/trustforge-release-router.service"
UNIT_SOURCE="$UNIT_TEMPLATE"
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
EXPECTED_UNIT_SHA256="$(sha256sum "$UNIT_SOURCE" | awk '{print $1}')"

if $DRY_RUN; then
  printf '%s\n' \
    "python3 scripts/verify_release_install_evidence.py # bind unit/runtime/keys/A/B/manifests" \
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

RELEASE_EVIDENCE="${TRUSTFORGE_EXPECTED_RELEASE_EVIDENCE:?set root-only release evidence receipt}"
RELEASE_EVIDENCE_KEYS="${TRUSTFORGE_RELEASE_EVIDENCE_KEYS:?set release evidence public keyring}"
A_ARTIFACT="${TRUSTFORGE_A_ARTIFACT:?set exact retained A artifact}"
B_ARTIFACT="${TRUSTFORGE_B_ARTIFACT:?set exact candidate B artifact}"
ENDPOINT_MANIFESTS="${TRUSTFORGE_ENDPOINT_MANIFESTS:?set signed endpoint manifest bundle}"
ROUTER_ARCHIVE="${TRUSTFORGE_ROUTER_ARCHIVE:?set immutable router application archive}"
ROUTER_TREE_MANIFEST="${TRUSTFORGE_ROUTER_TREE_MANIFEST:?set router tree manifest}"
RUNTIME_LOCK="${TRUSTFORGE_RUNTIME_LOCK:?set exact Python/runtime lock}"
UNIT_SOURCE="${TRUSTFORGE_SIGNED_UNIT:?set signed content-addressed systemd unit}"
EXPECTED_UNIT_SHA256="$(sha256sum "$UNIT_SOURCE" | awk '{print $1}')"
verify_release_inputs() {
  python3 "$ROOT_DIR/scripts/verify_release_install_evidence.py" \
    --evidence "$RELEASE_EVIDENCE" \
    --public-keyring "$RELEASE_EVIDENCE_KEYS" \
    --unit "$UNIT_SOURCE" \
    --runtime "$CONFIG_ROOT/release-router-runtime.json" \
    --keys "$CONFIG_ROOT/release-router-runtime-keys.json" \
    --control-bootstrap "$LEDGER_ROOT/control/bootstrap.json" \
    --control-events "$LEDGER_ROOT/control/events.jsonl" \
    --control-head "$LEDGER_ROOT/control/head.json" \
    --outcome-bootstrap "$LEDGER_ROOT/router-outcomes/bootstrap.json" \
    --a-artifact "$A_ARTIFACT" \
    --b-artifact "$B_ARTIFACT" \
    --endpoint-manifests "$ENDPOINT_MANIFESTS" \
    --router-archive "$ROUTER_ARCHIVE" \
    --router-tree-manifest "$ROUTER_TREE_MANIFEST" \
    --runtime-lock "$RUNTIME_LOCK" >/dev/null
}
verify_release_inputs

RELEASES_ROOT="$DEST_ROOT/opt/trustforge/releases"
RELEASE_DIR="$(
  python3 "$ROOT_DIR/scripts/install_router_release_artifact.py" \
    --archive "$ROUTER_ARCHIVE" \
    --tree-manifest "$ROUTER_TREE_MANIFEST" \
    --runtime-lock "$RUNTIME_LOCK" \
    --releases-root "$RELEASES_ROOT"
)"
ARCHIVE_SHA256="$(sha256sum "$ROUTER_ARCHIVE" | awk '{print $1}')"
RELEASE_EVIDENCE_SHA256="$(sha256sum "$RELEASE_EVIDENCE" | awk '{print $1}')"
[[ "$RELEASE_DIR" == "$RELEASES_ROOT/$ARCHIVE_SHA256" ]] || {
  echo "router release directory is not content addressed" >&2
  exit 83
}
grep -Fx "WorkingDirectory=$RELEASE_DIR" "$UNIT_SOURCE" >/dev/null || {
  echo "signed unit WorkingDirectory does not match release digest" >&2
  exit 84
}
grep -Fx \
  "ExecStart=$RELEASE_DIR/.venv/bin/python -I $RELEASE_DIR/scripts/release_router_service.py" \
  "$UNIT_SOURCE" >/dev/null || {
  echo "signed unit ExecStart does not match release digest" >&2
  exit 85
}
grep -Fx "UnsetEnvironment=PYTHONPATH PYTHONHOME" "$UNIT_SOURCE" >/dev/null || {
  echo "signed unit does not isolate Python imports" >&2
  exit 87
}
grep -Fx "Environment=TRUSTFORGE_RELEASE_DIGEST=$ARCHIVE_SHA256" \
  "$UNIT_SOURCE" >/dev/null || {
  echo "signed unit does not expose the on-process release digest" >&2
  exit 86
}

mkdir -p "$(dirname "$UNIT_TARGET")" "$(dirname "$NGINX_TARGET")" \
  "$(dirname "$SYSUSERS_TARGET")" "$(dirname "$TMPFILES_TARGET")" \
  "$DEST_ROOT/var/tmp" "$DEST_ROOT/var/lib/trustforge"
ROLLBACK_EVIDENCE="$DEST_ROOT/var/lib/trustforge/release-install-rollback-failed.json"
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
PREFLIGHT_INPUT_SHA256="$(
  sha256sum \
    "$CONFIG_ROOT/release-router-runtime.json" \
    "$CONFIG_ROOT/release-router-runtime-keys.json" \
    "$LEDGER_ROOT/control/bootstrap.json" \
    "$LEDGER_ROOT/router-outcomes/bootstrap.json"
)"
SERVICE_WAS_ACTIVE=false
systemctl is-active --quiet trustforge-release-router.service && SERVICE_WAS_ACTIVE=true
OLD_MAIN_PID="$(systemctl show -p MainPID --value trustforge-release-router.service 2>/dev/null || echo 0)"
OLD_RELEASE_DIR="absent"
OLD_EXE_SHA256="absent"
if $SERVICE_WAS_ACTIVE && [[ -z "$DEST_ROOT" && "$OLD_MAIN_PID" =~ ^[1-9][0-9]*$ ]]; then
  OLD_RELEASE_DIR="$(readlink -f "/proc/$OLD_MAIN_PID/cwd")"
  OLD_EXE_SHA256="$(sha256sum "/proc/$OLD_MAIN_PID/exe" | awk '{print $1}')"
fi
PRIOR_UNIT_SHA256="absent"
if ${EXISTED[0]}; then
  PRIOR_UNIT_SHA256="$(sha256sum "$BACKUP_DIR/0" | awk '{print $1}')"
fi
rollback_install() {
  status=$?
  rollback_failed=false
  if systemctl stop trustforge-release-router.service >/dev/null 2>&1; then
    service_stop_code=0
  else
    service_stop_code=$?
    rollback_failed=true
  fi
  artifact_restore_failed=false
  for index in "${!TARGETS[@]}"; do
    if ${EXISTED[$index]}; then
      install -o root -g root -m 0644 "$BACKUP_DIR/$index" "${TARGETS[$index]}" ||
        artifact_restore_failed=true
      cmp -s "$BACKUP_DIR/$index" "${TARGETS[$index]}" ||
        artifact_restore_failed=true
    else
      rm -f -- "${TARGETS[$index]}" || artifact_restore_failed=true
      [[ ! -e "${TARGETS[$index]}" ]] || artifact_restore_failed=true
    fi
  done
  if $artifact_restore_failed; then
    artifact_restore_code=1
    rollback_failed=true
  else
    artifact_restore_code=0
  fi
  daemon_restore_failed=false
  daemon_reload_code=0
  systemctl daemon-reload >/dev/null 2>&1 ||
    { daemon_reload_code=$?; daemon_restore_failed=true; }
  nginx -t >/dev/null 2>&1 ||
    { daemon_reload_code=$?; daemon_restore_failed=true; }
  systemctl reload nginx >/dev/null 2>&1 ||
    { daemon_reload_code=$?; daemon_restore_failed=true; }
  if $daemon_restore_failed; then
    rollback_failed=true
  fi
  service_restore_failed=false
  service_health_code=0
  if $SERVICE_WAS_ACTIVE; then
    systemctl start trustforge-release-router.service >/dev/null 2>&1 ||
      { service_health_code=$?; service_restore_failed=true; }
    systemctl is-active --quiet trustforge-release-router.service ||
      { service_health_code=$?; service_restore_failed=true; }
    restored_pid="$(systemctl show -p MainPID --value trustforge-release-router.service)"
    [[ "$restored_pid" =~ ^[1-9][0-9]*$ ]] ||
      { service_health_code=1; service_restore_failed=true; }
    curl --fail --silent --show-error \
      --unix-socket /run/trustforge/release-router.sock \
      -H 'X-TrustForge-Trusted-Subject: rollback-verify' \
      http://localhost/healthz >/dev/null 2>&1 ||
      { service_health_code=$?; service_restore_failed=true; }
    if [[ -z "$DEST_ROOT" ]]; then
      [[ "$(readlink -f "/proc/$restored_pid/cwd")" == "$OLD_RELEASE_DIR" ]] ||
        { service_health_code=1; service_restore_failed=true; }
      [[ "$(sha256sum "/proc/$restored_pid/exe" | awk '{print $1}')" == \
        "$OLD_EXE_SHA256" ]] ||
        { service_health_code=1; service_restore_failed=true; }
    fi
  fi
  if $service_restore_failed; then
    rollback_failed=true
  fi
  if $rollback_failed; then
    python3 "$ROOT_DIR/scripts/write_release_rollback_evidence.py" \
      --directory "$(dirname "$ROLLBACK_EVIDENCE")" \
      --original-status "$status" \
      --service-stop-code "$service_stop_code" \
      --artifact-restore-code "$artifact_restore_code" \
      --daemon-reload-code "$daemon_reload_code" \
      --service-health-code "$service_health_code" \
      --target-release "$RELEASE_EVIDENCE" \
      --target-evidence-sha256 "$RELEASE_EVIDENCE_SHA256" \
      --target-archive-sha256 "$ARCHIVE_SHA256" \
      --prior-release "$OLD_RELEASE_DIR" \
      --target-unit-sha256 "$EXPECTED_UNIT_SHA256" \
      --prior-unit-sha256 "$PRIOR_UNIT_SHA256" \
      --target-pid "${NEW_MAIN_PID:-0}" \
      --restored-pid "${restored_pid:-0}"
    rm -rf -- "$BACKUP_DIR"
    exit 91
  fi
  rm -f -- "$ROLLBACK_EVIDENCE"
  sync -f "$(dirname "$ROLLBACK_EVIDENCE")"
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
[[ "$(sha256sum "$UNIT_TARGET" | awk '{print $1}')" == "$EXPECTED_UNIT_SHA256" ]] || {
  echo "installed router unit does not match intended release" >&2
  false
}
if [[ -z "$DEST_ROOT" ]]; then
  [[ "$(readlink -f "/proc/$NEW_MAIN_PID/cwd")" == "$RELEASE_DIR" ]] || {
    echo "router process cwd does not match intended release" >&2
    false
  }
  [[ "$(stat -Lc '%d:%i' "/proc/$NEW_MAIN_PID/exe")" == \
    "$(stat -Lc '%d:%i' "$RELEASE_DIR/.venv/bin/python")" ]] || {
    echo "router process executable does not match intended release" >&2
    false
  }
  [[ "$(sha256sum "/proc/$NEW_MAIN_PID/exe" | awk '{print $1}')" == \
    "$(sha256sum "$RELEASE_DIR/.venv/bin/python" | awk '{print $1}')" ]] || false
  tr '\0' '\n' <"/proc/$NEW_MAIN_PID/cmdline" |
    grep -Fx "$RELEASE_DIR/scripts/release_router_service.py" >/dev/null || {
      echo "router process command does not match intended release" >&2
      false
    }
  tr '\0' '\n' <"/proc/$NEW_MAIN_PID/cmdline" | grep -Fx -- "-I" >/dev/null || false
  if tr '\0' '\n' <"/proc/$NEW_MAIN_PID/environ" |
    grep -Eq '^(PYTHONPATH|PYTHONHOME)='; then
    echo "router process inherited unsafe Python import environment" >&2
    false
  fi
  tr '\0' '\n' <"/proc/$NEW_MAIN_PID/environ" |
    grep -Fx "TRUSTFORGE_RELEASE_DIGEST=$ARCHIVE_SHA256" >/dev/null || {
      echo "router process does not attest intended code digest" >&2
      false
    }
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
setpriv \
  --reuid=trustforge-operator \
  --regid=trustforge-operator \
  --init-groups \
  python3 "$ROOT_DIR/scripts/deployment_readiness.py" status >/dev/null
[[ "$(
  sha256sum \
    "$CONFIG_ROOT/release-router-runtime.json" \
    "$CONFIG_ROOT/release-router-runtime-keys.json" \
    "$LEDGER_ROOT/control/bootstrap.json" \
    "$LEDGER_ROOT/router-outcomes/bootstrap.json"
)" == "$PREFLIGHT_INPUT_SHA256" ]] || {
  echo "signed release inputs changed during installation" >&2
  false
}
# Reopen and reauthenticate every signed artifact and the complete control
# chain immediately before the irreversible enable boundary.
verify_release_inputs
systemctl enable trustforge-release-router.service
trap - ERR INT TERM
rm -rf -- "$BACKUP_DIR"
rm -f -- "$ROLLBACK_EVIDENCE"
sync -f "$(dirname "$ROLLBACK_EVIDENCE")"
