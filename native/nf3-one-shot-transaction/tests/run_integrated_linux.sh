#!/usr/bin/env bash
set -euo pipefail

helper="${1:?usage: run_integrated_linux.sh HELPER NF2_RLIB EXPECTED_HELPER_SHA256 [SECURE_PARENT]}"
rlib="${2:?missing linked NF2 rlib}"
expected_helper="${3:?missing commit-bound helper SHA-256}"
parent="${4:-/root}"
evidence_dir="${5:?missing root-owned evidence directory}"
expected_rlib=bada9d9e97d961c7660b55678c518e56d1b3867b36a489d18648e0b6f26aa22b
expected_source=2c948fcca2c9194fce13e212e449739e5ecaa2b35256e7709b929b7822c85983
expected_profile_receipt=7f53b287a6944a5978b02dfcd35e50b5955be28107ac457369a70d22115f79a5
expected_foundation=d4d080f116e5967e2dd7c8cca02e471f754484ca529b48f22c2106ed8c819568
blocked=77

block() { echo "BLOCKED_EXTERNAL_LINUX: $*" >&2; exit "$blocked"; }

[ "$(uname -s)" = Linux ] || block "Linux required"
[ "$(uname -m)" = x86_64 ] || block "x86_64 required"
[ "$(id -u)" = 0 ] || block "root required"
[ -n "${INVOCATION_ID:-}" ] || block "systemd service invocation required"
[ -e /proc/self/status ] || block "/proc required"
systemctl is-system-running >/dev/null 2>&1 ||
  [ "$(systemctl is-system-running 2>/dev/null)" = degraded ] ||
  block "running systemd required"
if [ -s /run/systemd/container ] ||
  grep -Eiq '(docker|containerd|kubepods|podman|lxc)' /proc/1/cgroup; then
  block "container host rejected"
fi
[ -d "$parent" ] && [ ! -L "$parent" ] || block "secure parent required"
[ "$(stat -c %u "$parent")" = 0 ] || block "secure parent owner"
[ "$(stat -c %a "$parent")" = 700 ] || block "secure parent mode"
[ -d "$evidence_dir" ] && [ ! -L "$evidence_dir" ] || block "evidence directory absent"
[ "$(stat -c %u "$evidence_dir")" = 0 ] || block "evidence directory owner"
[ "$(stat -c %a "$evidence_dir")" = 700 ] || block "evidence directory mode"
[ -x "$helper" ] && [ ! -L "$helper" ] || block "integrated helper absent"
[ -f "$rlib" ] && [ ! -L "$rlib" ] || block "linked NF2 rlib absent"
actual_helper="$(sha256sum "$helper" | cut -d' ' -f1)"
actual_rlib="$(sha256sum "$rlib" | cut -d' ' -f1)"
[ "$actual_helper" = "$expected_helper" ] || block "integrated helper digest mismatch"
[ "$actual_rlib" = "$expected_rlib" ] || block "linked NF2 rlib digest mismatch"

deadline() {
  local seconds
  read -r seconds _ </proc/uptime
  seconds="${seconds%%.*}"
  echo $(((seconds + 240) * 1000000000))
}
new_store() { mktemp -d -p "$parent" trustforge-nf3-integrated-XXXXXXXX; }
new_witness() {
  local witness
  witness="$(mktemp -p "$parent" trustforge-nf3-witness-XXXXXXXX)"
  chown 0:0 "$witness"
  chmod 600 "$witness"
  echo "$witness"
}
count_stage() {
  local witness="$1" stage="$2"
  grep -c " stage=${stage} " "$witness" || true
}
hex64() {
  [ "${#1}" = 64 ] && case "$1" in *[!0-9a-f]*) return 1;; *) return 0;; esac
}
strict_committed() {
  local line="$1"
  set -- $line
  [ "$#" = 12 ] || { echo "invalid committed evidence field count" >&2; exit 1; }
  [ "$1" = INTEGRATED_COMMITTED ] || exit 1
  hex64 "${2#transaction=}" && [ "$2" != "${2#transaction=}" ] || exit 1
  hex64 "${3#request=}" && [ "$3" != "${3#request=}" ] || exit 1
  hex64 "${4#store=}" && [ "$4" != "${4#store=}" ] || exit 1
  case "$5" in terminal_head=*.record) ;; *) exit 1 ;; esac
  [ "$6" = "foundation=$expected_foundation" ] || exit 1
  case "$7" in boot=[0-9a-f-]*) ;; *) exit 1 ;; esac
  case "$8" in deadline=[0-9]*) ;; *) exit 1 ;; esac
  [ "$9" = executor_profile=evidence ] || exit 1
  [ "${10}" = "executor_source=$expected_source" ] || exit 1
  [ "${11}" = "executor_rlib=$expected_rlib" ] || exit 1
  [ "${12}" = "executor_profile_receipt=$expected_profile_receipt" ] || exit 1
}
strict_witness() {
  local witness="$1" expected_stage="$2" line
  while IFS= read -r line; do
    set -- $line
    [ "$#" = 12 ] || { echo "invalid witness field count" >&2; exit 1; }
    [ "$1" = v1 ] && [ "$2" = "stage=$expected_stage" ] || exit 1
    hex64 "${3#transaction=}" && [ "$3" != "${3#transaction=}" ] || exit 1
    hex64 "${4#request=}" && [ "$4" != "${4#request=}" ] || exit 1
    hex64 "${5#store=}" && [ "$5" != "${5#store=}" ] || exit 1
    [ "$6" = "foundation=$expected_foundation" ] || exit 1
    case "$7" in boot=[0-9a-f-]*) ;; *) exit 1 ;; esac
    case "$8" in deadline=[0-9]*) ;; *) exit 1 ;; esac
    [ "$9" = executor_profile=evidence ] || exit 1
    [ "${10}" = "executor_source=$expected_source" ] || exit 1
    [ "${11}" = "executor_rlib=$expected_rlib" ] || exit 1
    [ "${12}" = "executor_profile_receipt=$expected_profile_receipt" ] || exit 1
  done < <(grep " stage=${expected_stage} " "$witness")
}
validate_witness_file() {
  local witness="$1" line total=0
  while IFS= read -r line; do
    total=$((total + 1))
    set -- $line
    [ "$#" = 12 ] || { echo "unknown/malformed witness frame" >&2; exit 1; }
    [ "$1" = v1 ] || exit 1
    case "$2" in stage=ATTEMPT|stage=DEFINITE_SUCCESS) ;; *) exit 1 ;; esac
    hex64 "${3#transaction=}" && [ "$3" != "${3#transaction=}" ] || exit 1
    hex64 "${4#request=}" && [ "$4" != "${4#request=}" ] || exit 1
    hex64 "${5#store=}" && [ "$5" != "${5#store=}" ] || exit 1
    [ "$6" = "foundation=$expected_foundation" ] || exit 1
    case "$7" in boot=[0-9a-f-]*) ;; *) exit 1 ;; esac
    case "$8" in deadline=[0-9]*) ;; *) exit 1 ;; esac
    [ "$9" = executor_profile=evidence ] || exit 1
    [ "${10}" = "executor_source=$expected_source" ] || exit 1
    [ "${11}" = "executor_rlib=$expected_rlib" ] || exit 1
    [ "${12}" = "executor_profile_receipt=$expected_profile_receipt" ] || exit 1
  done <"$witness"
  [ "$total" = 2 ] || { echo "unexpected witness frame total" >&2; exit 1; }
}
assert_tombstoned() {
  local store="$1" head
  head="$(find "$store/heads" -maxdepth 1 -type f -name '*.record' -printf '%f\n' |
    sort | tail -n 1)"
  [ -n "$head" ] || { echo "terminal head absent" >&2; exit 1; }
  grep -q '^state=TOMBSTONED$' "$store/heads/$head" || {
    echo "terminal head is not TOMBSTONED: $head" >&2
    exit 1
  }
  echo "TOMBSTONED store=$(tr -d '\n' <"$store/store-id") terminal_head=$head"
}
record_case() {
  local index="$1" fault="$2" store="$3" witness="$4" log="$5"
  local case_dir head witness_sha log_sha head_sha
  case_dir="$evidence_dir/case-$(printf '%03d' "$index")"
  mkdir -m 700 "$case_dir"
  head="$(find "$store/heads" -maxdepth 1 -type f -name '*.record' -printf '%f\n' |
    sort | tail -n 1)"
  cp -- "$witness" "$case_dir/witness.txt"
  cp -- "$log" "$case_dir/process.log"
  cp -- "$store/heads/$head" "$case_dir/terminal.record"
  witness_sha="$(sha256sum "$case_dir/witness.txt" | cut -d' ' -f1)"
  log_sha="$(sha256sum "$case_dir/process.log" | cut -d' ' -f1)"
  head_sha="$(sha256sum "$case_dir/terminal.record" | cut -d' ' -f1)"
  python3 -c 'import json,sys; print(json.dumps({
    "actual":{"attempt":1,"definite_success":1,"retry_attempt_delta":0,
      "terminal_state":"TOMBSTONED"},
    "case":int(sys.argv[1]),"fault":sys.argv[2],
    "expected":{"attempt":1,"definite_success":1,"retry_attempt_delta":0,
      "terminal_state":"TOMBSTONED"},
    "log_sha256":sys.argv[3],"terminal_head":sys.argv[4],
    "terminal_record_sha256":sys.argv[5],"witness_sha256":sys.argv[6]},
    sort_keys=True,separators=(",",":")))' \
    "$index" "$fault" "$log_sha" "$head" "$head_sha" "$witness_sha" \
    >"$case_dir/evidence.json"
  sha256sum "$case_dir/evidence.json" | cut -d' ' -f1 \
    >"$case_dir/evidence.json.sha256"
  chmod 600 "$case_dir"/*
}
run_integrated() {
  local witness="$1"; shift
  env TRUSTFORGE_NF3_EXECUTION_WITNESS="$witness" "$helper" integrated "$@"
}

tx1=1111111111111111111111111111111111111111111111111111111111111111
tx2=2222222222222222222222222222222222222222222222222222222222222222

positive="$(new_store)"; positive_witness="$(new_witness)"
"$helper" provision "$positive"
positive_line="$(run_integrated "$positive_witness" "$positive" "$tx1" "$(deadline)")"
strict_committed "$positive_line"
[ "$(count_stage "$positive_witness" ATTEMPT)" = 1 ]
[ "$(count_stage "$positive_witness" DEFINITE_SUCCESS)" = 1 ]
strict_witness "$positive_witness" ATTEMPT
strict_witness "$positive_witness" DEFINITE_SUCCESS
validate_witness_file "$positive_witness"
if run_integrated "$positive_witness" "$positive" "$tx1" "$(deadline)"; then
  echo "same transaction replay executed" >&2; exit 1
fi
[ "$(count_stage "$positive_witness" ATTEMPT)" = 1 ]
[ "$(count_stage "$positive_witness" DEFINITE_SUCCESS)" = 1 ]

for case_index in $(seq 1 60); do
  uncertain="$(new_store)"; uncertain_witness="$(new_witness)"
  uncertain_log="${uncertain}.log"
  "$helper" provision "$uncertain"
  fault_selector=$((case_index % 3))
  if [ "$fault_selector" = 0 ]; then
    fault_kind=SIGKILL
    env TRUSTFORGE_NF3_EXECUTION_WITNESS="$uncertain_witness" \
      TRUSTFORGE_NF3_INTEGRATION_HOOK=AFTER_NF2_SUCCESS \
      "$helper" integrated "$uncertain" "$tx1" "$(deadline)" >"$uncertain_log" 2>&1 &
    pid=$!
    for _ in $(seq 1 400); do
      grep -q '^INTEGRATION_PAUSED stage=AFTER_NF2_SUCCESS$' "$uncertain_log" && break
      kill -0 "$pid" 2>/dev/null || { cat "$uncertain_log" >&2; exit 1; }
      sleep 0.05
    done
    grep -q '^INTEGRATION_PAUSED stage=AFTER_NF2_SUCCESS$' "$uncertain_log"
    kill -KILL "$pid"; wait "$pid" 2>/dev/null || true
  else
    error_kind=EIO
    [ "$fault_selector" = 2 ] && error_kind=ENOSPC
    fault_kind="$error_kind"
    if env TRUSTFORGE_NF3_EXECUTION_WITNESS="$uncertain_witness" \
      TRUSTFORGE_NF3_INTEGRATION_HOOK=AFTER_NF2_SUCCESS \
      TRUSTFORGE_NF3_INTEGRATION_ERROR="$error_kind" \
      "$helper" integrated "$uncertain" "$tx1" "$(deadline)" \
      >"$uncertain_log" 2>&1; then
      echo "$error_kind after-success fault unexpectedly committed" >&2
      exit 1
    fi
  fi
  [ "$(count_stage "$uncertain_witness" ATTEMPT)" = 1 ]
  [ "$(count_stage "$uncertain_witness" DEFINITE_SUCCESS)" = 1 ]
  strict_witness "$uncertain_witness" ATTEMPT
  strict_witness "$uncertain_witness" DEFINITE_SUCCESS
  validate_witness_file "$uncertain_witness"
  if run_integrated "$uncertain_witness" "$uncertain" "$tx1" "$(deadline)"; then
    echo "uncertain NF2 success was retried in case $case_index" >&2; exit 1
  fi
  assert_tombstoned "$uncertain"
  [ "$(count_stage "$uncertain_witness" ATTEMPT)" = 1 ]
  [ "$(count_stage "$uncertain_witness" DEFINITE_SUCCESS)" = 1 ]
  record_case "$case_index" "$fault_kind" "$uncertain" \
    "$uncertain_witness" "$uncertain_log"
  rm -rf -- "$uncertain"
  rm -f -- "$uncertain_witness" "$uncertain_log"
done

concurrent="$(new_store)"; concurrent_witness="$(new_witness)"
concurrent_log="${concurrent}.log"
"$helper" provision "$concurrent"
pids=()
for index in $(seq 1 32); do
  tx="$(printf '%064x' "$index")"
  run_integrated "$concurrent_witness" "$concurrent" "$tx" "$(deadline)" \
    >>"$concurrent_log" 2>&1 &
  pids+=("$!")
done
for pid in "${pids[@]}"; do wait "$pid" || true; done
[ "$(count_stage "$concurrent_witness" ATTEMPT)" = 1 ]
[ "$(count_stage "$concurrent_witness" DEFINITE_SUCCESS)" = 1 ]
[ "$(grep -c '^INTEGRATED_COMMITTED ' "$concurrent_log")" = 1 ]
while IFS= read -r committed; do strict_committed "$committed"; done \
  < <(grep '^INTEGRATED_COMMITTED ' "$concurrent_log")
strict_witness "$concurrent_witness" ATTEMPT
strict_witness "$concurrent_witness" DEFINITE_SUCCESS
validate_witness_file "$concurrent_witness"

stale="$(new_store)"; stale_witness="$(new_witness)"
"$helper" provision "$stale"
if run_integrated "$stale_witness" "$stale" "$tx2" 1; then
  echo "stale request unexpectedly succeeded" >&2; exit 1
fi
[ ! -s "$stale_witness" ] || { echo "stale request reached NF2" >&2; exit 1; }

burn_failure="$(new_store)"; burn_failure_witness="$(new_witness)"
"$helper" provision "$burn_failure"
if env TRUSTFORGE_NF3_EXECUTION_WITNESS="$burn_failure_witness" \
  TRUSTFORGE_NF3_HOOK_ARTIFACT=BURN \
  TRUSTFORGE_NF3_HOOK_STAGE=AFTER_FDATASYNC \
  TRUSTFORGE_NF3_HOOK_ERROR=EIO \
  "$helper" integrated "$burn_failure" "$tx2" "$(deadline)"; then
  echo "burn fsync injection unexpectedly reached NF2" >&2
  exit 1
fi
[ ! -s "$burn_failure_witness" ] || {
  echo "burn failure emitted an ATTEMPT witness" >&2
  exit 1
}

rm -rf -- "$positive" "$concurrent" "$stale" "$burn_failure"
rm -f -- \
  "$positive_witness" "$concurrent_witness" "$stale_witness" \
  "$burn_failure_witness" "$concurrent_log"
echo "PASS NF3 integrated positive/replay/concurrency/uncertain-success"
echo "helper_sha256=$actual_helper"
echo "linked_nf2_rlib_sha256=$actual_rlib"
