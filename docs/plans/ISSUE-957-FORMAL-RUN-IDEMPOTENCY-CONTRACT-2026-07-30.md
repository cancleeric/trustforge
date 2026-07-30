# Issue #957 — Formal-run idempotency and fresh-rerun contract

Status: approved design contract; implementation is owned by #958
Contract version: `analysis-question/v1`
Target route: `POST /api/analysis-question`
Date: 2026-07-30

## 1. Scope and normative language

This document defines transport idempotency for a chargeable formal analysis run.
It does not claim that the current endpoint implements the contract. `docs/api/openapi.yaml`
MUST be updated atomically with #958, when the behavior is available.

The terms MUST, MUST NOT, SHOULD, and MAY are normative. The contract is distinct
from:

- the existing 300-second manual content deduplication window;
- `/api/analyze` single-flight behavior;
- browser `sessionStorage`;
- preview-plan `client_request_id`;
- multi-angle batch idempotency.

None of those mechanisms is formal-run exactly-once authority.

## 2. Public request

The existing strict request fields remain:

```json
{
  "coin": "BTC",
  "mode": "risk",
  "question": "Assess the current risk.",
  "locale": "zh-Hant",
  "fresh": false
}
```

`locale` remains optional under its existing default. `fresh` is additive,
optional, and defaults to `false`. Unknown properties remain invalid.

Formal transport MUST send:

```http
Idempotency-Key: tf1.202607.AbCdEfGhIjKlMnOpQrStUv
```

Keys MUST use `tf1.<YYYYMM>.<random>` where `random` is 22 unpadded base64url
characters produced from 128 bits of a cryptographically secure RNG. The server
MUST reject duplicate header fields, comma-joined values, control characters,
ambiguous outer whitespace, invalid epochs, and low-entropy/noncanonical keys.
A client MUST generate a new random value for each new user intent and retain it
until it receives a durable receipt. The server publishes a bounded key-epoch
acceptance horizon (initially current and previous UTC month).

Legacy unkeyed behavior, if temporarily retained, MUST be a separately routed,
explicitly allowlisted legacy client path. It MUST NOT return a formal receipt,
compete with keyed formal traffic for the same side effect, or be enabled for
new clients. Formal mode always returns 400 for a missing or malformed key before
any snapshot, job, provider call, or charge.

## 3. Two identities, never one

### 3.1 Transport request fingerprint

Validation occurs before fingerprinting. `request_fingerprint` is a
domain-separated, versioned HMAC-SHA-256 over a length-prefixed tuple:

1. `contract_version = "analysis-question/v1"`
2. `coin = trim(coin).upper()`
3. `mode = trim(mode)` after enum validation
4. `question = trim(question)`
5. `locale = normalize_locale(locale)`
6. `fresh = boolean`

Length-prefixing MUST make field boundaries unambiguous. Question normalization
MUST NOT alter internal whitespace, case, Unicode code points, or apply
NFC/NFKC/case-folding. Omitted `fresh` and `fresh: false` produce the same
fingerprint.

The transport identity is:

```text
(namespace, trusted_caller_scope_hmac, key_hmac)
```

The raw key, raw caller scope, raw question, and request fingerprint MUST NOT
appear in logs or public metrics. Key, fingerprint, and caller pseudonyms MUST
each use a distinct domain-separated HMAC purpose and versioned key ID; a plain
hash is forbidden because common questions and caller identifiers are low
entropy. Rotation MUST preserve retained-record lookup. Caller scope MUST come
from trusted ingress/auth policy, never a client-forwarded identity header.
Missing caller authority fails closed with 503.

Same key and same fingerprint replays the immutable receipt for accepted or
uncertain execution. A `terminal_failed` record instead replays its stored safe
HTTP status/body exactly. Same key and a different fingerprint—including a
change to `locale` or `fresh`—returns 409.

### 3.2 Legacy content identity

The existing 300-second compatibility identity becomes the canonical
`(trusted_caller_or_tenant_visibility_scope_hmac, coin, mode, question)` and
excludes locale, fresh, and key. It is evaluated only after a new transport key
has been acquired. Cross-caller reuse of a receipt, job, or private result is
forbidden. A separately authorized public immutable artifact MAY be shared, but
each caller retains an independently admitted job/receipt/result authority.
`/api/analysis-job` and result reads MUST authorize against the receipt's trusted
scope. The content identity chooses a success disposition; it does not provide
transport exactly-once delivery.

## 4. Success truth table

Every successful new-key path has exactly one disposition:

| Request | 300-second compatible run | Result | Formal job/cost effect |
|---|---|---|---|
| new key, `fresh=false` | none | `created` | new snapshot and job |
| new key, `fresh=false` | same content and locale | `reused` | same job; no second formal-run charge |
| new key, `fresh=false` | same content, different locale and immutable artifacts renderable locally | `relocalized` | same job, new immutable result projection; no source/model dispatch or second formal-run charge |
| new key, `fresh=true` | any | `fresh-created` | new snapshot and job, subject to every security/cost/concurrency gate |
| same key, same fingerprint | accepted/uncertain | replay original receipt/disposition | zero additional side effects or charge |
| same key, same fingerprint | terminal failed | replay stored safe HTTP error | zero additional side effects or charge |

Rules:

- `fresh=true` bypasses only 300-second content reuse.
- Fresh rerun MUST use a new key. Reusing a key with a changed fresh flag is a
  conflict.
- A locale-only user intent MUST use a new key with `fresh=false`. It produces
  `relocalized` only by deterministic, provider-free rendering from immutable
  language-neutral artifacts, retains the job identity, and creates a distinct
  immutable `result_id`. It MUST NOT requeue the existing pipeline or overwrite
  an earlier locale result. If safe local rendering material is unavailable,
  return 409 `relocalization_unavailable` with zero dispatch; the user may then
  request a separately admitted fresh run.
- New data in another locale uses a new key with `fresh=true`.
- Fresh never bypasses identity, validation, quota, budget, concurrency,
  source, model, or egress controls.

## 5. Durable receipt

The public additive receipt is:

```json
{
  "schema_version": "formal-run-receipt/v1",
  "receipt_id": "frc_...",
  "question_id": "q_...",
  "job_id": "job_...",
  "result_id": "result_...",
  "state": "accepted",
  "origin": "manual",
  "disposition": "created",
  "locale": "zh-Hant",
  "created_at": "2026-07-30T08:00:00Z",
  "expires_at": null,
  "fingerprint_version": "analysis-question/v1"
}
```

`disposition` is one of `created`, `reused`, `relocalized`, or
`fresh-created`. Receipt identity and disposition become immutable once bound.
Runtime job progress remains owned by `/api/analysis-job`; it MUST NOT rewrite
receipt identity.

Legacy clients MUST continue to find the existing `question_id`, `job_id`,
`state`, and `origin` fields and tolerate additive fields.

The private durable record MUST contain at least:

```text
namespace, caller_scope_hmac, key_hmac,
fingerprint_version, request_fingerprint_hmac,
receipt_id, state(acquired|bound|execution_uncertain|terminal_failed),
owner_fencing_token, lease_expires_at,
question_id, job_id, result_id, disposition, locale,
operation_id, outbox_state, dispatch_state, provider_operation_id,
cost_policy_version, cost_policy_digest, reservation_id,
max_reserved_cost, settlement_state, reconciliation_state,
created_at, updated_at, terminal_at, expires_at, terminal_error_code,
terminal_http_status, terminal_response_schema_version,
terminal_safe_response_body, terminal_replay_headers, terminal_response_digest
```

`(namespace, caller_scope_hmac, key_hmac)` is unique.

## 6. State machine and atomic ordering

```text
validated
   |
   v
atomic acquire ---- existing/same fingerprint ----> replay immutable receipt
   |              \-- existing/different fingerprint -> 409 conflict
   v
acquired (fenced owner)
   |
   +--> legacy content decision
   +--> reused/relocalized: bind existing/provider-free result (no outbox)
   +--> created/fresh-created: transactional operation/job/outbox/reservation
   |
   v
bound(receipt -> deterministic operation/job/result) ----> immutable replay
   |
   +--> outbox dispatch (at most once)
   +--> execution_uncertain (reconcile; never blind resend)
   \--> terminal_failed ----> exact immutable safe error replay
```

Required ordering:

1. Strict parse, non-consumptive authentication/origin/security validation, and
   trusted caller authority.
2. Atomic key acquisition using the canonical fingerprint.
3. Existing receipt replay or conflict resolution.
4. Only a new owner performs consumptive rate, quota, budget, and concurrency
   admission.
5. In one authoritative transaction, bind the receipt to deterministic
   operation/job/result identity. Only `created` and `fresh-created` allocate a
   chargeable cost reservation and dispatch outbox record. `reused` binds the
   existing authority; `relocalized` binds a provider-free result projection;
   `relocalization_unavailable` binds a terminal safe error. These three paths
   MUST NOT create a chargeable outbox entry.
6. For a chargeable outbox only, a fenced worker dispatches the operation at
   most once.

If the idempotency store and AnalysisFlow database cannot share a transaction,
#958 MUST implement an authoritative transactional outbox/projection with
deterministic operation and job IDs plus uniqueness constraints. Recovery
projects the same identity; it never allocates another.

Fencing protects durable writes but cannot cancel an external call. Therefore a
dispatch claim MUST be persisted before egress with its reservation and stable
provider operation ID. Takeover MAY dispatch only with authoritative proof that
the prior owner never dispatched, or when the provider supports the same
idempotency operation ID. A timeout, crash, or ambiguous connection after
dispatch enters `execution_uncertain`; it MUST reconcile the late result and
MUST NOT resend. Reservation remains held until authoritative settlement or
manual fail-closed resolution. Every terminal failure is durable. Stale owner
late writes are rejected, while a late provider success is reconciled to the
same operation, job, receipt, and ledger entry.

When another live owner holds the lease, the service MAY bounded-follow it.
If it remains pending, return 409 `idempotency_request_in_progress` with
`Retry-After`. 503 is reserved for unavailable authority.

## 7. Shared authority and fail-closed behavior

The production provider MUST support:

- atomic conditional put/CAS or an equivalent transaction;
- strongly consistent reads where required for ownership;
- lease fencing tokens;
- durable terminal receipts;
- transactional operation/job/outbox and cost-reservation identity, either
  locally atomic or by deterministic projection;
- dispatch reconciliation that proves chargeable dispatch count and settlement;
- trusted time and retention/tombstone enforcement.

SQLite MAY be a single-host development/test adapter. Process-local memory,
per-host SQLite, and `.manual-locks`/`flock` MUST NOT be advertised or used as
multi-instance authority.

Shared store, trusted clock, policy, schema, or caller authority unavailable or
timed out returns 503 `idempotency_unavailable` before snapshot creation,
provider dispatch, job creation, or charge. There is no production fail-open
fallback.

Different new keys in the same trusted visibility scope racing on the same
content MUST execute the legacy create/reuse/relocalize decision inside the
shared owner transaction or equivalent serialization boundary. Different
callers/scopes MUST NOT deduplicate private job or result authority.

## 8. Expiry, retention, key epochs, and tombstones

- A nonterminal receipt MUST NOT expire and exposes `expires_at: null`.
- A terminal receipt has a minimum 24-hour replay SLA.
- `expires_at` ends the full receipt replay SLA; it does not make the key new.
- Lookup/replay and new acquisition use separate gates. The service first looks
  up a retained HMAC record across supported key versions; an existing receipt
  replays for its full obligation even after its creation epoch closes.
- Only a not-yet-recorded key is checked against the new-acquisition horizon
  (initially current and previous UTC month). An unknown key outside that horizon
  returns 409 `idempotency_key_unavailable` and cannot create an intent.
- A minimal key-HMAC tombstone remains through its retained obligation. HMAC key
  retirement occurs only after every receipt/tombstone lookup obligation for the
  version ends; it does not reopen an epoch for new acquisition.
- GC MAY remove payload-derived and nonessential data only after its SLA.
- Untrusted clock or GC authority fails closed.

## 9. HTTP outcomes

| Condition | HTTP/code | Required behavior |
|---|---|---|
| missing/malformed key or body | 400 `bad_request` | zero side effects |
| new accepted request | 202 with receipt | one disposition |
| same key + same fingerprint and accepted/uncertain receipt | 202 plus `Idempotency-Replayed: true` | exact same receipt, zero new effects |
| same key + different fingerprint | 409 `idempotency_conflict` | fixed message; disclose no original payload/receipt |
| live owner remains pending | 409 `idempotency_request_in_progress` + `Retry-After` | no second owner/job |
| unknown/expired/tombstoned retired-epoch key | 409 `idempotency_key_unavailable` | constant shape; key cannot be reused |
| shared authority unavailable | 503 `idempotency_unavailable` | fail before all chargeable effects |

An acquired request that fails before dispatch stores `terminal_failed` with a
fixed safe error code, exact HTTP status, immutable versioned canonical safe
response body, replay-header values from a strict allowlist, response digest,
and cost settlement. Same-key replay MUST reproduce that status and public body
without rerunning admission. Failure after a possibly successful dispatch is
`execution_uncertain`, returns 202 with the receipt, holds its reservation, and
is reconciled without resend. A failed intent can run again only with a new key
and fresh admission.

Conflict and unavailable-key messages MUST be constant-shape and MUST NOT act as
a receipt or payload oracle.

## 10. Security, privacy, and cost invariants

Allowed observability fields are limited to receipt-id pseudonym, caller-scope
HMAC prefix, disposition, receipt state, fingerprint version, latency, and fixed
error code. Public logs/metrics MUST NOT contain raw key, caller, question,
fingerprint, IP-derived scope, or request payload.

Transport replay MUST never consume provider budget or create a second charge.
`reused` and `relocalized` MUST not incur a second formal-run provider/source
charge. Relocalization is limited to the provider-free immutable projection
defined in section 4.
`fresh-created` consumes normal formal-run budget exactly once. Admission costs,
if separately metered, MUST be documented by #958 and remain deterministic on
replay.

Guessing a key across caller scopes MUST not reveal another caller's receipt.
Secret rotation MUST preserve lookup for retained records or provide versioned
HMAC verification without weakening isolation.

## 11. Migration and rollback

1. #958 introduces the provider and schema dark, with no public behavior change.
2. Dual-read/observe verifies acquisition, binding, and legacy content outcomes.
3. Any temporary unkeyed compatibility is isolated to an allowlisted legacy
   route/client and cannot race or share formal receipts with keyed traffic.
4. Formal enforcement requires the shared authority and synchronized OpenAPI,
   frontend client, HTTP, restart, and multi-instance tests.
5. Rollback may disable new formal submissions, but MUST preserve durable receipt
   replay and tombstones. It MUST NOT fall back to local locks for keyed traffic.

Existing question/job identifiers, locale lineage, polling, and 300-second
content compatibility remain readable throughout migration.

## 12. Owner boundaries

- #958 owns provider protocol/schema, migrations, acquire/follow/bind/fail,
  fencing/expiry, HTTP header/body/errors, `analysis_flow` integration, frontend
  typed client, OpenAPI synchronization, and runtime tests.
- #965 may reuse this transport model but MUST define
  `analysis-question/v2` for additional open-intent fields. It MUST NOT alter v1
  fingerprint semantics.
- #966 MUST reuse #958's authority and receipt state machine. Preview
  `client_request_id` is untrusted input and MUST NOT become a formal key.
- A later confirmation UI generates and retains a key until receipt, reconnects
  by receipt/job ID after reload, generates a new key for fresh rerun, and uses a
  new key with `fresh=false` for locale-only replay.

## 13. Machine-readable acceptance matrix

The JSON below is normative test input for #958. `job_delta` and `charge_delta`
are relative to the state before each case.

```json
{
  "contract": "analysis-question/v1",
  "cases": [
    {"id":"missing-key","expect":{"http":400,"code":"bad_request","job_delta":0,"charge_delta":0}},
    {"id":"duplicate-or-comma-joined-key-header","expect":{"http":400,"code":"bad_request","job_delta":0}},
    {"id":"low-entropy-or-noncanonical-key","expect":{"http":400,"code":"bad_request","job_delta":0}},
    {"id":"double-click-same-key","expect":{"same_receipt":true,"job_delta":1,"charge_delta":1}},
    {"id":"response-loss-retry","expect":{"replayed":true,"same_job":true,"additional_charge":0}},
    {"id":"reload-retry","expect":{"replayed":true,"same_job":true,"additional_charge":0}},
    {"id":"same-key-different-question","expect":{"http":409,"code":"idempotency_conflict","job_delta":0,"charge_delta":0}},
    {"id":"same-key-different-locale","expect":{"http":409,"code":"idempotency_conflict","job_delta":0,"charge_delta":0}},
    {"id":"same-key-fresh-toggle","expect":{"http":409,"code":"idempotency_conflict","job_delta":0,"charge_delta":0}},
    {"id":"fresh-omitted-vs-false","expect":{"same_fingerprint":true}},
    {"id":"new-key-no-content-match","expect":{"disposition":"created","job_delta":1}},
    {"id":"new-key-same-content-locale-within-300s","expect":{"disposition":"reused","job_delta":0,"formal_charge_delta":0,"reservation_delta":0,"outbox_delta":0}},
    {"id":"new-key-same-content-new-locale-within-300s","expect":{"disposition":"relocalized","job_delta":0,"provider_dispatch_delta":0,"reservation_delta":0,"outbox_delta":0,"immutable_result_id":true}},
    {"id":"relocalize-artifacts-unavailable","expect":{"http":409,"code":"relocalization_unavailable","provider_dispatch_delta":0,"reservation_delta":0,"outbox_delta":0}},
    {"id":"new-key-fresh-within-300s","expect":{"disposition":"fresh-created","job_delta":1,"charge_delta":1}},
    {"id":"same-fresh-key-retry","expect":{"disposition":"fresh-created","replayed":true,"additional_job":0,"additional_charge":0}},
    {"id":"content-after-300s","expect":{"disposition":"created","job_delta":1}},
    {"id":"queued-running-completed-failed-replay","expect":{"same_receipt":true,"additional_job":0,"additional_charge":0}},
    {"id":"two-instance-same-key-race","expect":{"owner_count":1,"receipt_count":1,"job_delta":1}},
    {"id":"two-instance-different-key-same-content-race","expect":{"formal_job_count":1,"dispositions":["created","reused"]}},
    {"id":"two-callers-same-content","expect":{"receipt_or_job_shared":false,"private_result_shared":false,"formal_job_count":2,"independent_admission":true}},
    {"id":"crash-after-acquire-before-reservation","expect":{"same_operation_recovery":true,"chargeable_dispatch_max":1}},
    {"id":"crash-after-reservation-before-job-projection","expect":{"same_reservation":true,"formal_job_max":1}},
    {"id":"crash-after-snapshot-before-job-insert","expect":{"same_operation_recovery":true,"formal_job_max":1}},
    {"id":"crash-after-job-insert-before-receipt-bind","expect":{"same_job":true,"formal_job_max":1}},
    {"id":"crash-after-outbox-claim-before-dispatch","expect":{"proof_required_before_takeover_dispatch":true,"chargeable_dispatch_max":1}},
    {"id":"crash-after-provider-dispatch-before-bind","expect":{"state":"execution_uncertain","automatic_redispatch":false,"chargeable_dispatch_max":1}},
    {"id":"provider-timeout-late-success","expect":{"same_operation_reconciled":true,"chargeable_dispatch_max":1,"ledger_settlement_count":1}},
    {"id":"crash-after-bind-before-response","expect":{"same_job":true,"additional_job":0}},
    {"id":"stale-owner-after-takeover","expect":{"stale_write_rejected":true}},
    {"id":"live-lease-follow-timeout","expect":{"http":409,"code":"idempotency_request_in_progress","retry_after":true}},
    {"id":"store-clock-policy-outage","expect":{"http":503,"code":"idempotency_unavailable","job_delta":0,"charge_delta":0}},
    {"id":"receipt-expired-key-tombstoned","expect":{"http":409,"code":"idempotency_key_unavailable","job_delta":0,"charge_delta":0}},
    {"id":"unknown-retired-epoch-key","expect":{"http":409,"code":"idempotency_key_unavailable","same_public_shape_as_tombstone":true}},
    {"id":"hmac-rotation-with-retained-record","expect":{"same_receipt":true}},
    {"id":"epoch-retirement-after-tombstone-gc","expect":{"http":409,"code":"idempotency_key_unavailable","accepted_as_new":false}},
    {"id":"closed-creation-epoch-retained-nonterminal","expect":{"same_receipt":true,"http":202,"new_acquisition":false}},
    {"id":"closed-creation-epoch-unknown-key","expect":{"http":409,"code":"idempotency_key_unavailable","new_acquisition":false}},
    {"id":"clock-skew-at-epoch-boundary","expect":{"trusted_clock_required":true,"fail_closed_on_uncertainty":true}},
    {"id":"terminal-failure-replay","expect":{"same_http_status":true,"same_public_body":true,"additional_job":0,"additional_charge":0}},
    {"id":"execution-uncertain-replay","expect":{"http":202,"automatic_redispatch":false,"same_reservation":true}},
    {"id":"cross-caller-key-guess","expect":{"receipt_disclosed":false}},
    {"id":"legacy-receipt-reader","expect":{"fields":["question_id","job_id","state","origin"]}},
    {"id":"rollback-authority-preserved","expect":{"receipt_replay":true,"local_fallback":false}}
  ]
}
```

## 14. Approval gates

#957 is complete only when Gray/CPO, CEO, Harper/CISO, and `/codex-review`
approve this contract and `git diff --check` passes. #958 MUST additionally pass
unit, real HTTP, restart, crash, multi-instance, cost-conservation, privacy, and
full pre-push gates before merge to `develop`.
