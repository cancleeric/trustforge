# Issue #1046 — official intrinsic authority split plan

Owner: Gray (CPO)
Status: CEO review required; plan only
Parent: #1046
Date: 2026-07-30

## Decision

#1046 must be split before further implementation. The repository currently
has neither an authenticated signed **current** promotion disposition nor a
production keyring/current-state authority available to the normal web
process. The accepted phase-4 audit records:

- current recommendation: `BLOCK`;
- signed current promotion disposition: unavailable;
- promotion ledger root/keyring: not supplied;
- production official UI authority: `BLOCKED_AUTHORITY`;
- actual desktop/mobile/zh-TW/en/200% visual evidence: `BLOCKED_EYE`.

Therefore a normal analyze response cannot honestly emit an official state
today. A request-selected ledger, path, key, policy, digest or current state;
an environment-selected trust root; a Python `Protocol`; a private seal; a raw
receipt ID; or a calibration object must not substitute for OS trust.

Commits `a5cc668b14e800e6ce8f0f55832872f569954651` and
`cf02704e3f0428362b043edc96a305e4500e8b24` are unapproved research
candidates. They are void as completion evidence, must not be pushed or merged
as #1046 delivery, and may only be selectively reimplemented after CEO plan
approval.

## Work item UIA — fail-closed typed contract and forced shadow

Estimate: at most 10 hours
Dependencies: existing #875/#876/#878 contracts only
Disposition after completion: keep #1046 open

### Scope

1. Define a narrow backend-to-frontend official-state schema that contains
   only public typed disposition fields. It must contain no private key,
   signature, raw receipt, calibration payload, trust root, filesystem path or
   sensitive subject data.
2. Make the unique normal analyze/comparison response assembly recompute the
   intrinsic assessment from server-owned PIT facts.
3. While no fixed trusted authority exists, force that assembly to emit only
   `mode=shadow`, `affects_official_score=false`, or fail soft to `null`.
4. Reject caller-prefilled or structurally plausible promotion signals,
   including:
   - `mode=official`;
   - receipt ID or raw promotion receipt;
   - release capability;
   - calibration claim/object;
   - arbitrary typed `official_state`.
5. Frontend may parse the typed future contract for isolated fixture testing,
   but raw mode, receipt, capability or calibration data must never elevate
   rendering. The fixture must be explicitly marked non-production.
6. Preserve current official score, direction, decision state and report bytes
   whenever intrinsic state is shadow/blocked/unavailable.

### Required tests

- normal analyze and comparison responses recompute and remain shadow;
- prefilled official/raw receipt/capability/calibration/typed-state payloads
  cannot survive response assembly;
- BLOCK, stale, malformed, forged and unavailable inputs remain shadow/null;
- frontend malformed and downgrade tests;
- no sensitive fields in the public schema;
- full backend/frontend regressions, lint, build and pre-push gate.

### Exit criteria

Gray, Harper and `/codex-review` approve the exact commit; local pre-push is
green; PR names reviewers and records evidence. UIA does **not** close #1046
and does not claim official state is operational.

## Work item UIB — trusted official integration and actual Eye

Estimate: at most 12 hours
Dependencies: accepted and merged #1060, #1065, and a signed current promotion
authority/disposition; coordinate with their corrected dependency graph and
do not introduce a cycle
Starts only after dependencies are on `develop`

### Scope

1. Consume the fixed #1060 OS-backed trust-verifier service through its narrow
   peer-authenticated interface. The normal web request cannot supply a ledger
   path, file descriptor, key, digest, policy, release, control head, current
   state or verifier generation.
2. Consume the #1065 trusted authorization/current-state verdict and the
   authenticated current signed promotion disposition.
3. Backend alone derives `official_state`. Required exact bindings:
   - signer capability and signature;
   - current policy digest;
   - dataset and observation root;
   - active/candidate artifact and release identity;
   - decision `PASS`;
   - issued time, expiry and freshness;
   - current promotion/deployment state and verifier generation;
   - current ledger/control heads and replay/nonce disposition.
4. Latest BLOCK, stale/expired PASS, wrong policy/root/release/generation,
   forged signature, replay, fork, authority outage or malformed response
   fails closed to shadow/blocked. No cached former PASS may remain official
   after current state changes.
5. Attach only the typed public projection to normal analyze and comparison
   responses. Do not expose signatures, keys, raw receipts, authorization
   payloads or internal filesystem/service coordinates.
6. Frontend renders official solely from that backend projection and keeps
   shadow, blocked and error states truthful and distinguishable.

### Required tests and evidence

- real fixed-authority integration tests for PASS → official;
- PASS → BLOCK, expiry, policy/root/release mismatch and verifier restart;
- replay, cross-run, mixed-generation, fork and authority-outage tests;
- request attempts to inject trust inputs are ignored/rejected;
- public response sensitive-field audit;
- desktop and mobile actual UI verification;
- zh-TW and English verification;
- 200% zoom, overflow, state transition and error verification;
- Eye CLI scan plus actual browser screenshots/evidence;
- full pre-push and exact-commit Gray, Harper and `/codex-review`.

If fixed authority or actual browser evidence is unavailable, record
`BLOCKED_AUTHORITY` or `BLOCKED_EYE`; do not close #1046.

## Sequencing

1. CEO reviews and explicitly approves this split plan.
2. Open UIA and UIB child issues, each with the acceptance criteria above and
   estimates no greater than their stated limits.
3. Implement UIA from latest `develop`; selectively reimplement useful ideas
   from the void research candidates only after review.
4. Merge UIA through the standard workflow while #1046 remains open.
5. Wait for #1060, #1065 and signed current authority to merge/become
   authentic.
6. Implement UIB, run actual Eye and all security/replay gates.
7. Close #1046 only after UIA and UIB both pass exact-commit review and the
   production-shaped authority path is personally verified.
