# #748-B Issuance/Supply Universal Data Ingestion — Scoped Development Plan

- **Branch**: `feat/873-issuance-supply`
- **Parent**: #748
- **Depends on**: A (#869, 五維方法論)
- **Est. Effort**: 10h
- **CPO**: gray

---

## 1. Scope

Issue B establishes a **protocol-family-agnostic ingestion pipeline** for `issuance_predictability` and `supply_verifiability` facts. The existing BTC fixture (Bitcoin Core v30.0 source inspection) proves the PoW case. This issue must:

1. Generalise the evidence-to-record builder so it works across protocol types without hardcoding PoW assumptions.
2. Produce one **second protocol type** (non-PoW, e.g. PoS/DPoS/consensus-spec or formally specified deflationary token) with its own pinned, hash-verified evidence pack.
3. Guarantee that the entire rebuild process is byte-stable, offline, and emits the full provenance envelope required by #869.
4. Verify all six acceptance criteria with regression tests.

**Out of scope**: governance, control dispersion, holder concentration, production scorer wiring, or UI changes. Those belong to C, D, E, H, I.

---

## 2. Deliverables

### D1. Protocol Evidence Pack (PEP) Convention

Introduce a minimal directory convention under `data/asset_intrinsic_evidence/pep/`:

```
pep/
  {asset_id}/
    manifest.json          # pinned revision, source coordinates, content hashes
    evidence/
      {evidence_file}.txt  # exact upstream bytes (excerpt or full blob)
```

The `manifest.json` is **not** a replacement for the provenance inside `asset_intrinsic_records.json`; it is the **build-time source of truth** from which the record is generated. It must contain:

- `protocol_family`: enum (`pow_source_code`, `pos_consensus_spec`, `evm_bytecode`, `formal_policy_doc`, …)
- `source_revision`: full `repo:commit` or `spec:version` string
- `source_urls`: array of `https://` raw URLs
- `source_coordinates`: exact file/line or section reference
- `evidence_files`: map of `filename → sha256`
- `methodology`: one-sentence rubric for this protocol family
- `coverage`: explicit boundary of what the evidence proves
- `valid_from`, `valid_until`: ISO-8601 UTC timestamps

### D2. Record Builder Script (`scripts/build_issuance_supply_records.py`)

A deterministic, **offline-only** builder that:

1. Reads a PEP directory.
2. Verifies every evidence file SHA-256 against the manifest.
3. Verifies evidence files are under `MAX_EVIDENCE_FILE_BYTES`.
4. Emits a single `AssetIntrinsicProfile` JSON object (ready for `data/asset_intrinsic_records.json` append) containing only the `issuance_predictability` and `supply_verifiability` dimensions.
5. Prints a stable, sorted JSON to stdout; no network I/O.
6. Exits non-zero on any hash mismatch, missing file, or schema violation.
7. Supports a `--dry-run` flag that validates without emitting.

The builder must be **protocol-family-agnostic**: it does not inspect the evidence semantics; it only validates hashes, sizes, and provenance completeness. The rubric for translating evidence into a `[0,1]` score remains a human review step documented in the manifest `methodology`.

### D3. Second Protocol Fixture

Choose and implement **one** second protocol family. Candidate priority:

1. **Ethereum PoS consensus spec** (preferred): pin a specific `ethereum/consensus-specs` release (e.g. `v1.5.0-alpha.5`) and excerpt the `get_base_reward` / `get_proposer_reward` functions. Supply side uses the `EIP-1559` burn mechanism defined in the execution spec.
2. **BNB Chain BEP-95 burn contract** (fallback): pin the `github.com/bnb-chain/BEPs` commit that defines the real-time burn, plus the system contract address and burn event signature. This is an `evm_bytecode` family.

**Regardless of choice**, the fixture must:
- Be a separate PEP under `data/asset_intrinsic_evidence/pep/asset:eth/` (or `asset:bnb/`).
- Contain at least two evidence files (issuance excerpt, supply excerpt).
- Pass the builder script dry-run.
- Result in a checked-in record inside `data/asset_intrinsic_records.json`.
- Be validated by the existing `validate_asset_intrinsic_records.py` CLI.

### D4. Expanded Test Coverage

New tests in `tests/test_asset_intrinsic.py`:

1. `test_pep_manifest_schema_is_versioned_and_reject_unknown_protocol_family`
2. `test_builder_rejects_tampered_evidence_and_wrong_hash`
3. `test_builder_emits_exact_record_keys_and_sorted_json`
4. `test_second_protocol_fixture_is_offline_and_hash_verified`
5. `test_second_protocol_is_different_family_from_btc`

New tests in `tests/test_asset_intrinsic_shadow.py`:

6. `test_known_issuance_and_supply_for_second_protocol_are_eligible`
7. `test_identical_pep_under_different_asset_id_produces_identical_results`
8. `test_stale_future_and_conflicted_issuance_supply_contribute_zero`

### D5. Documentation

Update `docs/plans/PLAN-ISSUE-748-ASSET-STRUCTURE-SCORE-PROMOTION-2026-07-29.md` section B status to "In Progress → Complete" with a link to the builder script and the two protocol families.

---

## 3. Test Plan

### 3.1 Unit Tests (pytest)

| Test | Description | Gate |
|---|---|---|
| PEP schema validation | manifest.json must validate against a PEP schema; unknown protocol_family rejected | pre-push |
| Builder hash verification | modify one byte in an evidence file → builder exits non-zero with "fingerprint mismatch" | pre-push |
| Builder output determinism | run builder twice on same PEP → byte-identical stdout | pre-push |
| Builder offline assertion | builder must not call `urllib`, `requests`, `httpx`, or open any non-file descriptor | pre-push (grep) |
| Second protocol record load | `load_asset_intrinsic_records` succeeds for the new record and passes evidence hash check | pre-push |
| Shadow assessment for second protocol | with 3 known dimensions (issuance + supply + one other) and 2 source families, gate passes | pre-push |
| Asset identity blind | same PEP data, different `asset_id` → same `assess_intrinsic_shadow` delta and total | pre-push |

### 3.2 CLI / End-to-End Tests

| Command | Expected Result |
|---|---|
| `python scripts/build_issuance_supply_records.py data/asset_intrinsic_evidence/pep/asset:eth --dry-run` | Exit 0, no stdout JSON |
| `python scripts/build_issuance_supply_records.py data/asset_intrinsic_evidence/pep/asset:eth` | Exit 0, valid JSON on stdout, `network_used=false` implied by no network |
| `python scripts/validate_asset_intrinsic_records.py data/asset_intrinsic_records.json --as-of 2026-08-01T00:00:00Z` | Exit 0, `pit_visible_assets >= 3` |

### 3.3 Regression Tests

- All existing `tests/test_asset_intrinsic.py` and `tests/test_asset_intrinsic_shadow.py` must pass unchanged.
- BTC fixture must not change content hash, source revision, or value. If the builder is retrofitted to BTC, it must reproduce the existing record byte-for-byte (or document why not, with CEO sign-off).

---

## 4. Risks

### R1. Second Protocol Evidence Availability
**Risk**: Ethereum consensus spec or BEP-95 source is not stable enough for a pinned, byte-exact excerpt.  
**Mitigation**: If the raw upstream file exceeds `MAX_EVIDENCE_FILE_BYTES`, truncate with a clear `source_coordinates` offset and document the truncation in the manifest. If no suitable source exists, downgrade to `decision_record` with `unknown` status and document the blocker; do not invent data.

### R2. Builder Couples to Protocol Semantics
**Risk**: The builder starts parsing issuance curves or supply formulas, becoming a protocol analyser rather than a provenance packager.  
**Mitigation**: Builder scope is **packaging only**. Score assignment stays in human-reviewed methodology text. Any automation of score derivation is a separate issue (out of scope for B).

### R3. BTC Fixture Retrofit Breaks Hash
**Risk**: Applying the new PEP convention to BTC changes the checked-in evidence paths or content, breaking existing hashes.  
**Mitigation**: BTC evidence files remain in `data/asset_intrinsic_evidence/` (flat). The PEP convention is additive. If we move BTC evidence into `pep/`, we must regenerate hashes and do a controlled migration with explicit CEO review.

### R4. Acceptance Criterion 2 Ambiguity
**Risk**: "At least two different protocol types" is interpreted as "two assets" rather than "two protocol families".  
**Mitigation**: The plan explicitly defines protocol families (PoW, PoS, EVM, formal). BTC = `pow_source_code`. The second fixture must be a different enum value. A test asserts `btc_pep.protocol_family != second_pep.protocol_family`.

### R5. Offline Rebuild is Not Truly Offline
**Risk**: Builder or test imports a module that lazily fetches network resources (e.g. `urllib` in stdlib, `certifi`).  
**Mitigation**: Add a pre-push grep gate: `grep -R "urllib\|requests\|httpx\|socket\." scripts/build_issuance_supply_records.py` must be empty (except safe stdlib uses for URL parsing, which must be documented). CI runs builder in an isolated environment with no DNS.

---

## 5. Review Gates

1. **gray (CPO)**: plan review (this document) — already in progress.
2. **CEO**: approve plan before implementation.
3. **CTO/subagent**: implement D1–D5.
4. **pre-push**: all tests, lint, build, data checks, `git diff --check`.
5. **Eye scan**: if any UI change (not expected, but if builder output is surfaced).
6. **`/codex-review`**: adversarial review on builder logic, hash verification, and fail-closed paths.
7. **harper (CISO)**: review if builder touches file-system traversal or path sanitisation beyond existing `MAX_PATH_LENGTH` checks.

---

*Plan status: Draft → Pending CEO Approval*
