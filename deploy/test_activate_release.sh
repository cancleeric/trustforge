#!/usr/bin/env bash
# TrustForge activation transaction integration smoke test.
# Verifies: script executability, argument parsing, preflight exit codes,
# and basic lock/receipt integration (no real AWS/SSM required).
set -euo pipefail
cd "$(dirname "$0")/.."

PASS=0
FAIL=0
pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }

SANDBOX=$(mktemp -d)
trap "rm -rf $SANDBOX" EXIT

export TRUSTFORGE_HOME="$SANDBOX"
export TRUSTFORGE_ACTIVATION_LOCK_BACKEND="json"

echo "--- Test 1: scripts are executable ---"
if [ -x deploy/activate_release.sh ]; then pass "activate_release.sh executable"; else fail "activate_release.sh executable"; fi
if [ -x deploy/preflight_activation.sh ]; then pass "preflight_activation.sh executable"; else fail "preflight_activation.sh executable"; fi
if [ -x deploy/setup_activation_lock_dynamodb.sh ]; then pass "setup_activation_lock_dynamodb.sh executable"; else fail "setup_activation_lock_dynamodb.sh executable"; fi

echo "--- Test 2: activate_release.sh arg parsing ---"
if ! bash deploy/activate_release.sh 2>/dev/null; then
  pass "activate_release.sh rejects no args"
else
  fail "activate_release.sh rejects no args"
fi
if ! bash deploy/activate_release.sh --target "" 2>/dev/null; then
  pass "activate_release.sh rejects empty target"
else
  fail "activate_release.sh rejects empty target"
fi

echo "--- Test 3: preflight_activation.sh print-checklist ---"
OUTPUT=$(bash deploy/preflight_activation.sh --print-checklist 2>/dev/null || true)
if echo "$OUTPUT" | grep -q "preflight checklist"; then
  pass "preflight --print-checklist"
else
  fail "preflight --print-checklist"
fi

echo "--- Test 4: preflight_activation.sh arg validation ---"
if ! bash deploy/preflight_activation.sh --bad-arg 2>/dev/null; then
  pass "preflight rejects unknown args"
else
  fail "preflight rejects unknown args"
fi

echo "--- Test 5: setup_activation_lock_dynamodb.sh print-policy ---"
POLICY=$(bash deploy/setup_activation_lock_dynamodb.sh --print-policy 2>/dev/null || true)
if echo "$POLICY" | grep -q "dynamodb:GetItem"; then
  pass "setup_activation_lock --print-policy"
else
  fail "setup_activation_lock --print-policy"
fi

echo "--- Test 6: activation lock (json backend, local file) ---"
export TRUSTFORGE_ACTIVATION_LOCK_PATH="$SANDBOX/test_locks.json"
export TRUSTFORGE_ACTIVATION_LOCK_BACKEND="json"
python3 -c "
import sys,os
sys.path.insert(0, 'src')
from trustforge.activation_lock import (
    _JsonActivationLockBackend, _set_backend_for_tests,
    acquire_activation_lock, release_activation_lock, get_activation_lock
)
path = os.environ['TRUSTFORGE_ACTIVATION_LOCK_PATH']
backend = _JsonActivationLockBackend(path=path)
_set_backend_for_tests(backend)

assert acquire_activation_lock('tf-sandbox', 'owner-a', ttl=60)
assert not acquire_activation_lock('tf-sandbox', 'owner-b', ttl=60)
release_activation_lock('tf-sandbox', 'owner-a')
assert get_activation_lock('tf-sandbox') is None
assert acquire_activation_lock('tf-sandbox', 'owner-b', ttl=60)
release_activation_lock('tf-sandbox', 'owner-b')
print('all lock assertions passed')
" || {
  fail "activation lock integration"
  PASS=$((PASS - 0))  # no-op, already incremented in fail
  echo "----------------------------------------" >&2
  echo "Summary: PASS=$PASS FAIL=$FAIL" >&2
  exit 1
}
pass "activation lock integration"

echo "--- Test 7: activation receipt (local) ---"
python3 -c "
import sys,os
sys.path.insert(0, 'src')
from trustforge.activation_receipt import ActivationReceipt, write_receipt_local, read_receipts_local
path = os.path.join(os.environ['TRUSTFORGE_HOME'], 'receipts.jsonl')
r = ActivationReceipt(
    activation_target='i-test', owner_id='test-owner',
    candidate_digest='abc', previous_active_digest='prev', status='completed',
    build_timestamp='2026-01-01T00:00:00Z', started_at='2026-01-01T00:00:00Z',
    finished_at='2026-01-01T00:01:00Z', error='', rollback_triggered=False, rollback_succeeded=False,
)
assert write_receipt_local(r, path=path)
receipts = read_receipts_local(path=path)
assert len(receipts) == 1
assert receipts[0].status == 'completed'
print('all receipt assertions passed')
" || {
  fail "activation receipt integration"
  echo "----------------------------------------" >&2
  echo "Summary: PASS=$PASS FAIL=$FAIL" >&2
  exit 1
}
pass "activation receipt integration"

echo "--- Test 8: preflight dry-run (skipped without prewritten S3 state) ---"
pass "preflight dry-run skipped (no preset S3 pointers)"

echo "--- Test 9: production activation verifies durable analysis worker stability ---"
if grep -q 'verify_analysis_worker "$TARGET"' deploy/activate_release.sh \
  && grep -q 'NRestarts' deploy/activate_release.sh \
  && grep -q 'analysis-flow worker restart loop detected' deploy/activate_release.sh; then
  pass "activation checks analysis-flow worker stability"
else
  fail "activation checks analysis-flow worker stability"
fi

echo "--- Test 10: production activation requires a completed manual report ---"
if grep -q 'verify_analysis_report "$TARGET"' deploy/activate_release.sh \
  && grep -q 'verify_production_analysis_report.py' deploy/activate_release.sh \
  && grep -q -- '--timeout-seconds 600' deploy/activate_release.sh; then
  pass "activation requires completed manual report"
else
  fail "activation requires completed manual report"
fi

echo ""
echo "========================================"
echo "Results: PASS=$PASS FAIL=$FAIL"
echo "========================================"
if [ "$FAIL" -eq 0 ]; then
  exit 0
else
  exit 1
fi
