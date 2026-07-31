# Issue #959 — Canonical claim identity schema contract

Status: design-only contract (gray/CPO draft, CEO-approved pending harper + /codex-review); implementation is owned by #960, adaptation by #941, back-link UI by #942, export by #949
Contract version: `claim-identity/v1`
Owner module (single mint point): `agent/orchestrator.build_report`
Date: 2026-07-31

## 1. Scope and normative language

This document defines the **canonical claim identity** that joins a single analysis
run's Report, Evidence, key-basis, insights, cross-source signal, narrative
citations, and all exports (JSON / CSV / public API). It does not claim the
current code implements the contract. `docs/api/openapi.yaml`, `src/trustforge/
schema.py`, and `src/trustforge_core/contracts.py` MUST be updated atomically
with #960 when the behavior is available.

The terms MUST, MUST NOT, SHOULD, and MAY are normative.

This contract is distinct from — and MUST NOT be confused with — any of the
following existing identities, none of which is the canonical claim identity:

- the source claim fingerprint `claim.id` minted by ingestion/extraction
  (`trust/scoring.py:325` `Claim(id=f"{d.id}#{i}")`, `bedrock.py:585`
  `id=f"{src_doc_id}#llm{i}"`, `ingestion/prices.py:164`, `ingestion/
  hoyabit.py:141`, and the training-only placeholder `trust/source_accuracy.py:277`
  `f"dummy-{coin}-{date}-{j}"`);
- the Evidence free-text role tag `related_claim` (`schema.py:48`, set by
  `agent/orchestrator.py:138` `_scored_to_evidence` and `agent/kernel_projection.py:47`
  `_kernel_scored_to_evidence` — currently `"{coin} 市場判斷"` / `"反方／低信任訊號"`);
- the positional `evidence_idx` integers in `BasisItem` (`schema.py:101`) consumed
  by `frontend/src/components/KeyBasisList.tsx:14`;
- the sealed kernel-internal tuple order of `KernelRunResolution.claim_resolutions`
  (`trustforge_core/contracts.py:174`) enforced positionally by
  `validate_claim_resolution_order` (`contracts.py:190-206`);
- the formal-run transport key / receipt identities of #957.

The source claim fingerprint, the role tag, the array index, and the kernel tuple
order MAY continue to exist internally, but none of them is a permanent joinable
identity. Only the canonical claim_id minted by the report builder is.

### 1.1 Identity guarantee boundary (CEO decision, normative)

The canonical claim identity is guaranteed stable **only within one run and its
exports**. It is **explicitly NOT guaranteed to be identical across a fresh rerun.**

- "One run" = a single execution of the analysis pipeline bound to one
  authoritative `run_scope_id` (defined in §2.2). In production this is the
  formal analysis `job_id` from the #957 receipt (`analysis_flow.py:635`
  `analysis_jobs.job_id`).
- "Its exports" = every serialization derived from that run's `(Report,
  list[Evidence])`: the `analysis_results.payload_json` projection
  (`analysis_flow.py:649`), `/api/analyze` and `/analyze.json` bodies
  (`web.py:5635` `asdict(report)` + `web.py:5349` `_public_evidence_dict`),
  `historical_replay.replay_snapshot` output (`historical_replay.py:60`),
  CSV exports (#949), and the rendered narrative.
- A formal fresh rerun (`formal_run_coordinator.py:131` `fresh=True`, disposition
  `fresh-created` at `:280`) allocates a new `job_id` and therefore **MUST
  produce a disjoint set of canonical claim_ids** from the original run, even if
  the ingested documents, sentence ordering, and source claim fingerprints happen
  to be byte-identical.
- Historical replay is **snapshot-scoped, not invocation-scoped**: its
  `run_scope_id` is the replayed snapshot's `snapshot_id` (§2.2). Replaying the
  *same* `snapshot_id` reproduces the *same* claim_ids (deterministic replay for
  debugging — test `run-scope-same-job-replay`); replaying a *different*
  `snapshot_id` yields disjoint ids. A historical replay is never a "fresh rerun"
  in the #957 sense (it creates no new formal job), so the disjointness rule above
  applies only across different snapshot_ids, not across replay invocations of
  the same snapshot.

Rationale: binding claim identity to the run (not to content) prevents stale
cross-run references — a claim_id found outside its run is unconditionally
non-resolvable, which is the safe failure mode.

**Non-goal (normative):** `claim_id` is a **join key only**. It is not an HMAC,
not a bearer token, and carries no signature; any party knowing a claim's public
attributes and the `run_scope_id` can compute a valid `clm1:` id. Therefore
`claim_id` MUST NOT be used as an authorization, trust, provenance-of-trust, or
anti-tamper signal by any consumer. Authorization of the run itself is owned by
#957's transport/receipt authority, not by claim_id.

## 2. Identity model

### 2.1 Generation algorithm

The canonical claim_id is a **deterministic, versioned, run-scoped** string
computed by a single private helper inside `build_report`. It is NOT random and
NOT an HMAC (it is a public stable identifier, not a secret — unlike the #957
transport key). It is:

```text
canonical_claim_id = "clm1:" + run_scope_id + ":" + claim_local_fingerprint
```

where:

- `clm1` is the scheme/version prefix. Future incompatible identity changes
  MUST bump to `clm2`, … and the parser MUST reject unknown prefixes
  fail-closed (never silently coerce).
- `run_scope_id` is the run identity from §2.2 (non-empty exact string).
- `claim_local_fingerprint` is 16 hex characters (truncated SHA-256) over a
  **domain-separated, length-prefixed** tuple of the claim's stable attributes,
  so that field boundaries are unambiguous:

  1. `purpose = "claim-identity/v1"` (domain separation)
  2. `claim_type` (`fact | inference | opinion` — `schema.py`/`scoring.py:130`)
  3. `direction` (`bullish | bearish | neutral`)
  4. `canonical_source` (via `trustforge_core.source_identity.canonical_source`,
     the same alias-collapsing normalization already used repo-wide — see
     `agent/evidence_grouper.py:78` `_normalize_source` and `scoring.py`)
  5. `doc_id` (the source document identity, e.g. the `Document.id`)
  6. `source_claim_suffix` — the local-within-document part of the source claim
     fingerprint, extracted as the substring **after the last `#`, excluding the
     `#` itself** (so `doc123#0` → `0`, `src#llm0` → `llm0`). If the fingerprint
     contains no `#` (e.g. `price-BTC-ret`, `hoyabit-{coin}-{now}`), the
     **complete source fingerprint** is used as the suffix. The `#` delimiter
     itself is never included in the hashed bytes. This is defined for every
     production fingerprint shape and is unambiguous.
  7. `text` = `claim.text` with **trim only**. Normalization MUST NOT alter
     internal whitespace, case, or Unicode code points, and MUST NOT apply
     NFC/NFKC/case-folding (same rule as #957 §3.1 — altering claim text would
     silently merge distinct claims or split identical ones).

Length-prefixing MUST be applied to every variable-length field, encoded as
`{decimal_length}:{raw_bytes}` (ASCII decimal length, a literal colon delimiter,
then the field's exact bytes) — e.g. field `"abc"` → `"3:abc"`, field `"2abc"` →
`"4:2abc"`. The colon delimiter makes the encoding unambiguous and
collision-free across implementations (`(a,bc)` and `(ab,c)` cannot produce the
same bytes). Fixed-width fields (e.g. the 16-hex output) need no prefix. #960
MUST ship deterministic test vectors (known input → expected fingerprint) so
independent implementations agree.

### 2.2 `run_scope_id` — source of truth

`run_scope_id` is the run's authoritative identity, injected into `build_report`
as a **required, non-empty** parameter (`build_report` at `orchestrator.py:992`
gains `run_scope_id: str`). Its source:

| Caller | `run_scope_id` value | Evidence |
|---|---|---|
| production formal run | the analysis `job_id` bound by the #957 receipt | `analysis_flow.py:635` `analysis_jobs.job_id` |
| `historical_replay.replay_snapshot` | the replayed snapshot's `snapshot_id`, or the content-hash fallback below | `historical_replay.py:33-60` |
| `pipeline.run_agent_pipeline` (direct / non-formal callers) | an explicit, caller-provided colon-free scope (the invoking CLI/adapter's run id) | `pipeline.py` |
| `backfill` (historical backfill runs) | a backfill-run identifier (`backfill-{batch}` or the backfill's own run id), colon-free | `backfill.py` |
| direct tests / offline `build_report` | an explicit fixture string passed by the test | `tests/test_*.py` patterns |

Every caller of `run_agent_pipeline`/`build_report` MUST pass a `run_scope_id`
from a row above or an equivalent explicit, colon-free, non-content-derived
scope; a caller with no documented scope is a contract violation that #960 MUST
close (add the caller's scope source or refactor it through a path that has one).

`run_scope_id` MUST NOT be derived from claim content (that would re-introduce
cross-run stability). It MUST match `^[^:]+$` (colon-free — colons would
ambiguously delimit the canonical id `clm1:{run_scope_id}:{fingerprint}`); an
empty, non-string, or colon-bearing `run_scope_id` fails closed before any
Evidence/key_basis is emitted. Today `job_id` is `flow-{16hex}` (colon-free) and
satisfies this; #957 MUST keep `job_id` colon-free, or #960 must encode it.

`historical_replay.replay_snapshot` derives `run_scope_id` from the replay
snapshot's stable identifier: the snapshot dict's `snapshot_id` if present; else a
**deterministic, content-derived fallback** `replay-{sha256(canonical snapshot content).hexdigest()}` (full 256-bit digest of the snapshot's stable content —
collision-resistant, so two distinct snapshots do not share a scope for any
practical purpose; any theoretical collision is still contained because claim_id
is a join key, not a trust signal, per §1.1); #960 MUST pin this concretely and
pass it into `run_agent_pipeline`/`build_report`. A non-deterministic counter
(`replay-{n}`, process-local sequence) is FORBIDDEN because it collides across
snapshots.

### 2.3 Uniqueness guarantee (within a run)

Within one run, canonical claim_ids MUST be unique across the **complete
canonical registry** (admitted Evidence claims + truncated-but-registered
claims). Because `extract_claims` (`scoring.py:325`) and the LLM extractor
(`bedrock.py:585`) already produce unique source fingerprints per document, the
`(doc_id, source_claim_suffix)` pair is unique pre-dedup. The report builder
therefore:

1. computes the canonical claim_id for each claim in the `scored` list (both
   admitted Evidence and truncated-but-referenced);
2. asserts uniqueness of the resulting complete set; and
3. on the theoretical collision (two distinct claims hashing identically), MUST
   deterministically disambiguate by appending a monotonic counter derived from
   **scored-list order** (`.d2`, `.d3`, …) — scored-list order is the stable
   position in the original `scored` list passed to `build_report`, and it covers
   both admitted and truncated claims uniformly. The collision resolver is the
   ONLY place scored-list order influences identity, and it never promotes a bare
   array index.

Every Evidence row carries exactly one canonical `claim_id` (1:1). **Every
deduplicated (discarded) source fingerprint MUST alias the admitted survivor's
canonical claim_id** in the `source_fingerprint -> canonical_claim_id` map, so
that insights / cross_source_signal / narrative citations that still reference a
discarded claim's fingerprint remap to the survivor's id (otherwise the
§4.2 no-dangling check would fail on those references). Discarded claims are not
separately exposed; their provenance remains reachable via `Evidence.source` /
`source_url` / `data_lineage`.

## 3. Ownership (single mint point)

**`agent/orchestrator.build_report` (`orchestrator.py:992`) is the sole owner and
mint point of the canonical claim_id.** It computes every claim_id exactly once
per run and propagates the same value into Report, Evidence, key_basis, insights,
cross_source_signal, narrative citations, and exports. It maintains a single
`source_fingerprint -> canonical_claim_id` map for that run (including discarded
fingerprints aliasing their survivor — §2.3).

### 3.0 Machine-checkable single-mint gate (normative)

The set of modules permitted to **mint** (construct for emission/assignment to a
`claim_id`/`Evidence`/`BasisItem` field) a string matching `^clm1:` is exactly
`{agent/orchestrator.build_report}`. A pre-push gate (added with #960) MUST
enforce this at the **construction** level — it detects code that builds/assigns a
`clm1:` id as a claim identity, NOT code that merely parses or validates the
format. Reader/adapter/regex sites that match `^clm1:` to VALIDATE (e.g. the
#941 `resolve_claim_id` regex) are explicitly permitted and MUST be allow-listed
in the gate (e.g. the gate greps for mint patterns like assignment to
`claim_id`/`Evidence(` fields, or uses an allow-list of validator files). Tests,
fixtures, and this contract doc are excluded. This makes the strongest invariant
enforceable rather than aspirational; future code (including the "later
confirmation UI" contemplated in §8) that needs a claim id MUST obtain it from
`build_report`, never mint a `clm1:` string.

### 3.1 Modules that MUST reference (never mint)

The following MUST consume the canonical claim_id from `build_report` and MUST
NOT generate a second claim-identity scheme:

- `agent/orchestrator._scored_to_evidence` (`orchestrator.py:138`) — gains an
  optional `claim_id: str` parameter populated by the caller (`build_report`);
  it MUST NOT derive its own.
- `agent/kernel_projection._kernel_scored_to_evidence` / `project`
  (`kernel_projection.py:47`, `:76`) — the kernel path. `project` returns
  `KernelJudgment` (`kernel_projection.py:22`) whose `evidence` tuple order
  equals `kernel_output.scored_claims` order (`:92-100`). `build_report`
  zips that order with the source fingerprints and stamps canonical claim_ids.
  `project` itself MUST NOT mint (it has no `run_scope_id`).
- `schema.Evidence` / `schema.BasisItem` / `schema.Report` (`schema.py:43,97,105`)
  — transport only.
- `web._public_evidence_dict` (`web.py:5349`) and all `/api/*` handlers —
  reference/serialize only.
- `historical_replay` (`historical_replay.py:60`) and all `asdict(report)` /
  `ev.to_dict()` serialization sites — transport only.
- Frontend components — reference only (`EvidenceTable.tsx`, `KeyBasisList.tsx`,
  `InsightExplainabilityPanel.tsx`, `CrossSourceSignalPanel.tsx`).

### 3.2 Sealed kernel boundary (MUST NOT change)

`trustforge_core.contracts` (`contracts.py`) is **sealed** (`__init_subclass__`
raises at `:132,149,166,186,…`). The kernel-internal `KernelClaim.id`
(`contracts.py:140`) and the positional `KernelClaimResolution.claim_id`
(`contracts.py:157`) / `KernelRunResolution.claim_resolutions` tuple
(`contracts.py:174`) MUST remain exactly as-is. The canonical claim_id is an
**app-layer** identity introduced at the projection/report boundary; it does not
enter the deterministic kernel and does not perturb `validate_claim_resolution_order`
(`contracts.py:190-206`). This preserves the kernel's immutability and test
stability while giving the report/export layer a joinable typed key.

## 4. Related-claim grouping and back-link invariants (supports #942)

### 4.1 Canonical fields added

- `Evidence.claim_id: str` — the single canonical identity of that evidence's
  claim. REQUIRED on all newly-written Evidence (non-empty, matches `^clm1:`).
- `BasisItem.claim_ids: list[str]` — the canonical identities of the claims this
  basis item rests on (parallel to, and consistent with, the existing positional
  `evidence_idx`). The two MUST agree: `set(claim_ids) == {evidence[i].claim_id
  for i in evidence_idx}`.
- `Insight.claim_ids` / `InsightContribution.claim_id` (`insights.py:74,51`) —
  rewired by `build_report` via its `source_fingerprint -> canonical_claim_id`
  map (currently populated from the raw `sc.claim.id` at `insights.py:463,489`,
  `:566-589`).
- `cross_source_signal.supporting_claim_ids` (`orchestrator.py:864,974`,
  read by `web.py:3259`) and `cross_source_signal.stance_pairs[].claim_id`
  (`orchestrator.py:657`) — likewise remapped to canonical ids by `build_report`.
- Narrative Step-3 citations `[claim_id]` (`agent/orchestrator.py:1229-1239`,
  prompt contract at `agent/narrative_locale.py:215,222`) — the claim_id table
  handed to the Bedrock prompt MUST use canonical ids, so citations in the
  rendered narrative resolve through the same join.

### 4.2 Grouping invariants

1. **Deterministic ordering for `claim_ids` / `supporting_claim_ids`**: sorted by
   `(admission_order)` then by lexicographic `claim_id`. Repeated runs of the
   same inputs within the same run produce identical ordering; across fresh
   reruns ordering is irrelevant because ids are disjoint (§1.1).
2. **No dangling references**: every canonical id appearing in `BasisItem`,
   `Insight`, `cross_source_signal`, or narrative citations MUST be present in
   the run's **canonical claim_id registry** — the complete set of ids minted by
   `build_report` for this run, which includes both Evidence-admitted claims AND
   truncated (detected-but-not-admitted) claims that `cross_source_signal`/
   insights reference. This resolves the tension between §4.2.5 (`cross_source`
   receives the untruncated `scored` list) and the no-dangling invariant:
   truncated claims are minted and registered, but not exposed as Evidence rows.
   The report builder MUST validate this **as the final statement immediately
   before the return** of `build_report` (fail-closed ValueError on any dangling
   id — mirrors the existing strictness in `contracts.py:205`); no code may run
   between this check and the return.
3. **Bidirectional reachability (the #942 requirement)**: from any **admitted**
   Evidence one can reach every `BasisItem` / `Insight` / `cross_source_signal`
   that cites it (via `claim_id` equality), and from any such consumer one can
   reach the Evidence (via the same key). **This invariant applies to admitted
   Evidence claims only.** Truncated claims' canonical ids are registered in the
   canonical registry (§4.2.2) and MAY be referenced by cross_source_signal /
   insights, but they are **NOT bidirectionally navigable** — they have no
   Evidence row. References to truncated claims are **informational** (the signal
   was detected involving a claim not included in the final report). `evidence_idx`
   remains as a legacy positional convenience but is NOT authoritative.
4. **Direction role preservation**: the existing supporting/contrarian split
   encoded in `related_claim` (`"{coin} 市場判斷"` vs `"反方／低信任訊號"`,
   `orchestrator.py:1062,1090`; mirrored in `evidence_grouper.py:29,95`) is
   preserved. `claim_id` carries no direction; direction stays on `Evidence`/
   `claim.direction` to avoid encoding semantics into the identity.
5. **Truncation consistency (truncated-but-referenced claims stay registered)**:
   `aggregate()`'s supporting/contrarian truncation determines the **admitted
   Evidence set** — only admitted claims are exposed as `Evidence[*]` rows.
   Consumers that receive a broader list — insights, `cross_source_signal` (which
   intentionally receives the untruncated `scored` list), and narrative citations
   — MUST reference **only registered canonical ids**: an admitted Evidence row,
   or a truncated claim minted into the registry below. Any reference to a claim
   that was truncated (present in `scored` but not admitted) MUST be **minted and
   registered** in the canonical claim_id registry (§4.2.2) by `build_report` —
   the truncated claim gets a canonical id even though it is not exposed as an
   Evidence row. Consumers (`cross_source_signal`, insights) MAY reference these
   registered-but-not-admitted ids. This preserves the #32 cross-source
   reliability fix (which depends on `cross_source` seeing the full untruncated
   list) while maintaining no-dangling integrity.

## 5. Backward compatibility (old snapshots)

**Old snapshots remain readable. The array index is never promoted to a permanent
identity.** Concretely:

- Pre-#960 snapshots (persisted in `analysis_results.payload_json`,
  `analysis_flow.py:649`) lack `Evidence.claim_id` and `BasisItem.claim_ids`,
  and rely on `evidence_idx` / `related_claim`. They MUST continue to deserialize
  and render without error.
- On **read** of such a snapshot, the reader MAY synthesize an **ephemeral,
   explicitly-non-canonical** join key for the session — format
   `legacy:{run_scope_id_or_snapshot_id}:{evidence_index}` — purely so the UI can
   use one code path. This key:
  - MUST carry a distinct prefix (`legacy:`) so it can never be confused with a
    `clm1:` canonical id;
  - MUST NOT be persisted, logged as canonical, exported, or cited in a
    narrative; and
  - MUST NOT be treated as stable — it is scoped to that single read session and
    is discarded when the session ends.
- The `evidence_idx` field on `BasisItem` is retained on the wire for
  backward-compatible readers, but new code MUST join via `claim_ids`; the index
  is a legacy fallback, not an identity (this is the literal realization of
  "array index 不得升格成永久 ID").

### 5.1 Migration / adapter proposal (#941 owns impl)

1. **Dual-write is unnecessary** — new runs simply write canonical ids; old runs
   are immutable artifacts and are NEVER rewritten/backfilled.
2. **Dual-read adapter** (`#941`): a single `resolve_claim_id(evidence_dict,
   index, run_scope_id)` helper used by all readers, with three disjoint outcomes:
   - `claim_id` **absent** (pre-#960 snapshot) → return the `legacy:` synthetic key.
   - `claim_id` **present and matches the full §10 regex** `^clm1:[^:]+:[0-9a-f]{16}(\.d[0-9]+)?$` → return it.
   - `claim_id` **present but does NOT match** (e.g. `clm2:...`, `clm1:invalid`,
     unprefixed garbage) → **reject fail-closed** (raise); it MUST NOT fall through
     to a `legacy:` key, because an explicitly-present unknown scheme is a
     contract violation per §2.1 (`unknown-scheme-rejected`), not a legacy
     snapshot. This prevents a corrupted/unknown id from masquerading as legacy
     and violating the join/no-dangling invariants.
3. **Lazy, read-path-only**: no background migration job, no schema rewrite, no
   replay of old runs. The cost is paid once per read of an old snapshot.
4. **Deprecation**: `evidence_idx`-as-identity is documented as deprecated at
   introduction; a follow-up (out of #959 scope) MAY remove legacy read support
   only after all retained snapshots are post-#960 and the retention SLA
   (`analysis_flow.py:4148` `DELETE … WHERE created_at < ?`) has aged them out.

## 6. Join contract (Report / Evidence / JSON / CSV / export)

The canonical claim_id is the **single typed join key** across all shapes:

| Shape | claim_id location | Serialization site |
|---|---|---|
| `Report` (in-memory) | `Evidence.claim_id`, `BasisItem.claim_ids`, `Insight.claim_ids`, `cross_source_signal.supporting_claim_ids`, `stance_pairs[].claim_id` | `schema.py:43,97,105`; `insights.py:51,74` |
| Report JSON (`/api/analyze`, `/analyze.json`) | same fields, serialized via `asdict(report)` + `Evidence.to_dict()` | `web.py:5635`, `web.py:5349` `_public_evidence_dict` |
| Evidence JSON (replay / snapshot export) | `Evidence.claim_id` per row | `historical_replay.py:60` |
| `analysis_results.payload_json` | the canonical-id-bearing Report+Evidence blob | `analysis_flow.py:649` |
| CSV export (#949) | one column `claim_id` on the evidence/claim grain; join column on basis/insight exports | new in #949 |
| Public API OpenAPI | `Evidence.claim_id` (required), `BasisItem.claim_ids` (required), `legacy_evidence_idx` (optional, deprecated) | `docs/api/openapi.yaml:3026,3124` |
| Rendered narrative | `[claim_id]` citations resolve through the same key | `orchestrator.py:1229-1239` |

Join rules:

- Joining Report↔Evidence↔Basis↔Insight↔cross_source_signal within one run uses
  **only** `claim_id` equality. Positional `evidence_idx` is for legacy readers.
- Cross-shape consistency: the canonical id minted once by `build_report` MUST
  appear identically in every shape above for that run. Any divergence is a
  contract violation (caught by the §4.2 invariant check).
- `claim_id` MUST NOT appear in any cross-run aggregation/comparison key. Cross-run
  comparison (#949-style) joins on content/source attributes, never on claim_id.

## 7. OpenAPI / types / migration / test matrix proposal

### 7.1 Type changes

- `schema.Evidence`: add `claim_id: str` (required, no default on new writes).
- `schema.BasisItem`: add `claim_ids: list[str] = field(default_factory=list)`
  (parallel to `evidence_idx`).
- `trust/insights.py`: `InsightContribution.claim_id` and `Insight.claim_ids`
  keep their types; their *values* are canonicalized by `build_report`.
- `docs/api/openapi.yaml`: add `claim_id` (string, `^clm1:`, required) to
  `Evidence` (`:3026`); add `claim_ids` (array of string, required) and
  `legacy_evidence_idx` (array of integer, optional, deprecated) to `BasisItem`
  (`:3124`). `EVIDENCE_SCHEMA_VERSION` / `REPORT_SCHEMA_VERSION`
  (`data_contracts.py:32-33`, both `"1.0.0"`) bump to `"1.1.0"` (additive; old
  readers tolerate the new optional fields, new readers require them).

### 7.2 Migration steps (owned by #960 impl)

1. Introduce `claim_id`/`claim_ids` fields dark (additive, default-populated),
   no public behavior change.
2. Wire `build_report` to mint via the single helper, populate the
   `source_fingerprint -> canonical_claim_id` map, and remap
   insights/cross_source_signal/narrative citations.
3. Add the §5.1 dual-read adapter; switch the initial reference read-paths (`web._public_evidence_dict`, `historical_replay`) to `resolve_claim_id` (#960 scope). Remaining read-paths (snapshot modal, comparison, other web handlers) migrated by #941.
4. Synchronize OpenAPI + frontend typed client; add the `^clm1:` validation.
5. Enforce: no canonical id minted outside `build_report` (grep/lint guard in
   #960).

### 7.3 Test matrix

| Case | Expected |
|---|---|
| `uniqueness-within-run` | all `Evidence.claim_id` distinct; identical (doc,sentence) within one run collapse to one id (dedup) |
| `determinism-within-run` | same `run_scope_id` + same inputs → byte-identical claim_ids across repeated `build_report` calls |
| `run-scope-disjoint-fresh-rerun` | fresh rerun (new `job_id`) on byte-identical docs → **zero** overlap of claim_id sets with the original run |
| `run-scope-same-job-replay` | replay of the same `run_scope_id` (e.g. #957 `reused`) → identical claim_ids (same run) |
| `empty-run-scope-id-rejected` | `build_report(run_scope_id="")` raises before any Evidence emitted |
| `back-link-parity` | `set(BasisItem.claim_ids) == {evidence[i].claim_id for i in evidence_idx}` for every basis item |
| `no-dangling-claim-ref` | every id in basis/insight/cross_source/narrative ∈ canonical claim_id registry (Evidence-admitted + truncated-but-registered), else ValueError |
| `kernel-boundary-unchanged` | `KernelClaim.id`/tuple-order contract tests pass unmodified; canonical ids do not enter the kernel |
| `legacy-snapshot-readable` | pre-#960 payload deserializes; reader returns `legacy:` synthetic keys; render succeeds |
| `legacy-key-not-persisted` | synthetic `legacy:` key from a read is never written to any store/export/log |
| `openapi-shape` | Evidence requires `claim_id` matching `^clm1:`; BasisItem requires `claim_ids` |
| `public-api-claim-id-present` | `/api/analyze` body Evidence rows carry non-empty `clm1:` ids |
| `csv-join-parity` | CSV evidence-grain `claim_id` joins basis/insight export on equality |
| `collision-disambiguation` | forced hash collision → deterministic `.dN` suffix; still unique; no bare index |
| `truncated-claim-ref-registered` | a claim present in `scored` but truncated from Evidence, referenced by cross-source/insight → claim is minted + registered in canonical registry (not Evidence); reference resolves; no-dangling check passes |

## Amendment A — Contract clarifications (post-PR1 review)

1. **Canonical registry (§4.2.2)**: the no-dangling validation target is the
   canonical claim_id registry (all ids minted by `build_report` for a run,
   including truncated-but-referenced claims), not just the `Evidence[*].claim_id`
   set. This preserves `cross_source_signal`'s #32 reliability behavior.

2. **provenance().claim_id (`scoring.py:304`)**: `TrustedBrief.provenance()`
   returns the raw source fingerprint (`sc.claim.id`), NOT a canonical `clm1:`
   id. It is a trust-layer identity, not an app-layer join key. It MUST NOT be
   used for Report↔Evidence↔Basis joining. If any `build_report` consumer
   serializes `provenance().claim_id` into a public field, `build_report` MUST
   remap it via the `source_fingerprint→canonical` map at the consumption point.

3. **JSON-schema required**: the data-contracts JSON schema MUST NOT add
   `claim_id`/`claim_ids` to `required` (preserves backward compat for old snapshot
   readers). "New-write-required" is enforced by the mint helper (`build_report`
   always stamps) + no-dangling check + tests, NOT by JSON-schema validation.
   OpenAPI MAY mark them required (describes new-write contract, not old-reader
   validation).

4. **Adapter ownership**: #960 ships the initial `resolve_claim_id` dual-read
   adapter + migrates `web._public_evidence_dict` and `historical_replay`. #941
   owns hardening + migrating all remaining read-paths (snapshot modal,
   comparison, remaining web handlers).
5. **Serialized resolvability of truncated references**: truncated claims'
   canonical ids are registered in-memory by build_report but have no Evidence
   row. #960 MUST ensure that serialized references to truncated claims (in
   stance_pairs/supporting_claim_ids/narrative) are either (a) resolvable via a
   serialized minimal registry/lookup exposed in the Report/export shape, or
   (b) explicitly marked as truncated so consumers (#942 UI / #949 export) do
   not attempt to join to a non-existent Evidence row. The approach is an #960
   implementation decision; the contract constrains the outcome (no silent
   unresolvable references in serialized output).

## 8. Owner boundaries

- **#960 (impl)** owns: the `claim_id`/`claim_ids` fields, the single mint helper
  in `build_report`, the `run_scope_id` injection, the source→canonical remap for
  insights/cross_source/narrative, the §4.2 invariants, the dual-read adapter,
  OpenAPI/types sync, schema-version bump, and the unit + contract tests above.
- **#941 (adapter)** owns: hardening the `resolve_claim_id` dual-read adapter and
  migrating the remaining read paths (snapshot modal, comparison, other web
  handlers) to it; the legacy-snapshot read tests.
- **#942 (back-link UI)** owns: frontend Evidence↔Claim bidirectional navigation
  using `claim_id`/`claim_ids`; deprecating `evidence_idx`-as-identity in
  `KeyBasisList.tsx` / `EvidenceTable.tsx` / `InsightExplainabilityPanel.tsx` /
  `CrossSourceSignalPanel.tsx`.
- **#949 (export)** owns: emitting `claim_id` as the join column in CSV/JSON
  exports and ensuring no cross-run aggregation keys on `claim_id`.
- **#957 (formal-run)** owns `run_scope_id`'s transport authority (the `job_id`
  in the receipt). #959 depends on #937 (DONE) and on #957's job identity being
  available; #959 does not alter #957's fingerprint/receipt semantics.

A later confirmation UI or multi-angle run that reuses this identity MUST reuse
the same `claim-identity/v1` scheme and the same single-mint discipline; it MUST
NOT invent a parallel claim-id namespace.

## 9. Approval gates

#959 is complete only when gray/CPO, CEO, harper/CISO, and `/codex-review`
approve this contract and `git diff --check` passes. Per `AGENTS.md`, this is a
single-developer repository; reviewer attestation is commit-bound rather than a
self-approval. Security review (harper) MUST specifically examine: (a) that
`claim_id` leaks no more than the already-public `Evidence.source`/`source_url`
(text is one-way hashed into the fingerprint), (b) that the `legacy:` synthetic
key cannot be abused to forge a canonical id, and (c) that `run_scope_id`
injection cannot be spoofed by a caller to collide with another run's ids.

#960 MUST additionally pass unit, contract, real HTTP, restart, and full
pre-push gates before merge to `develop`.

**Accepted residual risk (harper, documented):** because `claim_id` is a keyless
public join key (§1.1 non-goal), the mint point cannot self-verify that a caller
is *entitled* to a given `run_scope_id`; a caller passing a victim run's `job_id`
would deterministically mint ids that collide with that run. Collision-resistance
therefore rests entirely on #957's transport/receipt authority being unforgeable
(owner: #957), with no backstop at the mint. This is accepted because claim_id is
not a trust signal; #957 remains the authority for run identity.

## 10. Normative machine-readable acceptance matrix

The JSON below is normative test input for #960. `claim_id_set` denotes the set
of `Evidence.claim_id` values produced by one `build_report` invocation.

```json
{
  "contract": "claim-identity/v1",
  "id_shape": {
    "regex": "^clm1:[^:]+:[0-9a-f]{16}(\\.d[0-9]+)?$",
    "legacy_regex": "^legacy:[^:]+:[0-9]+$",
    "prefixes_mutually_exclusive": true
  },
  "cases": [
    {"id":"uniqueness-within-run","expect":{"distinct_claim_ids":true}},
    {"id":"determinism-within-run","expect":{"byte_identical_across_repeats":true}},
    {"id":"run-scope-fresh-rerun-disjoint","expect":{"claim_id_overlap":0,"shared_prefix_only":"clm1"}},
    {"id":"run-scope-same-job-replay","expect":{"claim_id_overlap":1.0}},
    {"id":"empty-run-scope-id","expect":{"raises_before_evidence":true,"evidence_emitted":0}},
    {"id":"nonstring-run-scope-id","expect":{"raises_before_evidence":true}},
    {"id":"dedup-survivor-single-id","expect":{"evidence_claim_ids_after_dedup":"exactly_one_per_admitted_row"}},
    {"id":"hash-collision-disambiguation","expect":{"disambiguator":[".d2"],"still_unique":true,"bare_index_used":false}},
    {"id":"basis-claim-ids-parity","expect":{"parity_with_evidence_idx":true}},
    {"id":"no-dangling-claim-ref","expect":{"all_refs_in_canonical_registry":true,"else":"ValueError"}},
    {"id":"insight-claim-ids-canonicalized","expect":{"insight_claim_ids_match_clm1":true,"raw_source_fingerprint_leaked":false}},
    {"id":"cross-source-claim-ids-canonicalized","expect":{"supporting_claim_ids_and_stance_pairs_match_clm1":true}},
    {"id":"narrative-citations-canonical","expect":{"prompt_claim_table_uses_clm1":true}},
    {"id":"kernel-internal-unchanged","expect":{"KernelClaim_id_unchanged":true,"tuple_order_contract_passes":true}},
    {"id":"single-mint-point","expect":{"mint_callers":["build_report"],"no_second_scheme":true}},
    {"id":"legacy-snapshot-readable","expect":{"deserializes":true,"synthetic_key_prefix":"legacy:","render_succeeds":true}},
    {"id":"legacy-key-not-persisted","expect":{"written_to_store":false,"written_to_export":false,"logged_as_canonical":false}},
    {"id":"openapi-evidence-required","expect":{"Evidence_claim_id_required":true,"matches_regex":true}},
    {"id":"openapi-basisitem-required","expect":{"BasisItem_claim_ids_required":true,"legacy_evidence_idx_optional_deprecated":true}},
    {"id":"schema-version-bump-additive","expect":{"new_version":"1.1.0","old_optional_fields_tolerated":true}},
    {"id":"public-api-carries-claim-id","expect":{"analyze_body_evidence_claim_id_nonempty":true}},
    {"id":"export-csv-join","expect":{"csv_claim_id_joins_basis_and_insight":true}},
    {"id":"no-cross-run-aggregation-key","expect":{"comparison_join_uses_content_not_claim_id":true}},
    {"id":"unknown-scheme-rejected","expect":{"clm2_or_unprefixed_rejected_fail_closed":true}},
    {"id":"text-normalization-trim-only","expect":{"nfc_applied":false,"casefold_applied":false,"internal_whitespace_altered":false}}
  ]
}
```

---

## Appendix A — Audit evidence (file:line, read-only)

All claims above are grounded in the current `develop` tree:

**Report builder / single mint point**
- `src/trustforge/agent/orchestrator.py:992` `def build_report(...)` → returns
  `(Report, list[Evidence])`; the Evidence dedup loop at `:1064` `_add_evidence`
  (keys on `(source, content_reference, related, direction)`, keeps highest
  trust `:1070`); supporting/contrarian admission at `:1080,1089`.

**Source claim fingerprints (pre-canonical; NOT canonical identity)**
- `src/trustforge/trust/scoring.py:325` `extract_claims` →
  `Claim(id=f"{d.id}#{i}")` (doc.id + sentence index; production-dominant).
- `src/trustforge/bedrock.py:585` → `id=f"{src_doc_id}#llm{i}"` (LLM extraction).
- `src/trustforge/ingestion/prices.py:164` → `id=fid` (e.g. `price-BTC-ret`).
- `src/trustforge/ingestion/hoyabit.py:141` → `id=f"hoyabit-{coin}-{now}"`.
- `src/trustforge/trust/source_accuracy.py:277` → `f"dummy-{coin}-{date}-{j}"`
  (training-only placeholder).

**Evidence assembler (must reference, not mint)**
- `src/trustforge/agent/orchestrator.py:138` `_scored_to_evidence(sc, related)`
  — currently does NOT carry `sc.claim.id`; `related_claim` is a free-text role
  tag (`"{coin} 市場判斷"` / `"反方／低信任訊號"`, set at `:1062,1090`).
- `src/trustforge/agent/kernel_projection.py:47` `_kernel_scored_to_evidence`;
  `:76` `project`; `:22` `KernelJudgment` (evidence tuple order == scored order,
  `:92-100`).

**Evidence / Report schema (no canonical claim_id today)**
- `src/trustforge/schema.py:43` `Evidence` — `related_claim: str` role tag only,
  no `claim_id`; `:92` `to_dict` via `asdict`.
- `src/trustforge/schema.py:97` `BasisItem` — `evidence_idx: list[int]`
  (positional; `:101`).
- `src/trustforge/schema.py:105` `Report`; serialization via `asdict`.
- `src/trustforge/data_contracts.py:32-33` `EVIDENCE_SCHEMA_VERSION` /
  `REPORT_SCHEMA_VERSION` = `"1.0.0"`.

**Sealed kernel contract (must stay unchanged)**
- `src/trustforge_core/contracts.py:140` `KernelClaim.id`; `:157`
  `KernelClaimResolution.claim_id`; `:174` `KernelRunResolution.claim_resolutions`
  tuple; `:190-206` `validate_claim_resolution_order` (positional coupling);
  sealed via `__init_subclass__` raises at `:132,149,166,186`.

**Existing claim_id carriers that need canonicalization**
- `src/trustforge/trust/insights.py:51` `InsightContribution.claim_id`;
  `:74` `Insight.claim_ids`; populated from `sc.claim.id` at `:185,217,246,254,
  281,289,322,330,463,489,566,571,575,589`.
- `src/trustforge/agent/orchestrator.py:657` stance_pairs `claim_id`;
  `:864,974` `supporting_claim_ids`; `:1229-1239` narrative Step-3 claim table.
- `src/trustforge/web.py:3259` reads `supporting_claim_ids`.

**Serialization / export sites (transport only)**
- `src/trustforge/historical_replay.py:60` `asdict(report)` + `ev.to_dict()`.
- `src/trustforge/web.py:5635` `asdict(report)`; `:5349` `_public_evidence_dict`.
- `src/trustforge/agent/agentcore_runtime.py:57` / `backfill.py:641`
  `asdict(report)`.
- `src/trustforge/analysis_flow.py:649` `analysis_results.payload_json`.

**Run / snapshot persistence (run_scope_id source)**
- `src/trustforge/analysis_flow.py:631` `analysis_snapshots` (snapshot_id PK);
  `:635` `analysis_jobs` (job_id PK, snapshot_id FK); `:649` `analysis_results`
  (result_id, job_id UNIQUE); retention GC at `:4148`.

**Cross-run / fresh-rerun boundary (§1.1)**
- `src/trustforge/formal_run_coordinator.py:131` `fresh: bool`;
  `:280` `disposition="fresh-created" if fresh else "created"`.
- `src/trustforge/historical_replay.py:33` `replay_snapshot` →
  `:58` `run_agent_pipeline` (fresh per snapshot).

**OpenAPI**
- `docs/api/openapi.yaml:3026` `Evidence` (required list lacks `claim_id`);
  `:3124` `BasisItem` (`evidence_idx`, no `claim_ids`).

**Frontend (reference only)**
- `frontend/src/components/KeyBasisList.tsx:14` reads `item.evidence_idx`.
- `frontend/src/components/EvidenceTable.tsx:34` renders `ev.related_claim`.
- `frontend/src/components/InsightExplainabilityPanel.tsx:33` reads `c.claim_id`.
- `frontend/src/components/CrossSourceSignalPanel.tsx:63` reads
  `signal.supporting_claim_ids`.
