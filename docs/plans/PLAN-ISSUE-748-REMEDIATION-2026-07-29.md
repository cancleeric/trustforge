# Issue #748 Remediation Plan

Status: CEO approved after adversarial review rejection.

## Decision

The previous “11/11 complete; only natural data accumulation remains” closure is
withdrawn. Existing shadow-only code stays in place because flag-off parity and
fail-closed behavior protect official scoring. The completion state, not the
safe technical skeleton, is rolled back.

## Work sequence

1. Reopen #872 and create the cost-sensitive holder-data follow-up (#994).
2. Reopen #874 and make conflicted facts replay through the canonical PIT path.
3. Reopen #878 and complete the actual-branch Eye matrix.
4. Build canonical shadow-store promotion evidence (#995).
5. Produce scheduled signed promotion receipts (#996).
6. Bind verified PASS receipts to the existing release gate (#997).
7. Re-audit the eleven merged PRs at their actual merge commits (#998).
8. Keep #879 open and blocked until a real PASS receipt and allowlisted
   Analyze/Compare canary complete.

## Non-negotiable gates

- Every implementation issue stays within 12 hours.
- Full pre-push and commit-bound reviewer attestation are required.
- Judgment/security work requires gray, Harper and `/codex-review`.
- UI work additionally requires Eye CLI against the actual branch.
- No main, release or production mutation is authorized by this remediation.

