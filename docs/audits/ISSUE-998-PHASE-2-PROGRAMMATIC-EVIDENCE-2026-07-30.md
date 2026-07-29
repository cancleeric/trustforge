# Issue #998 Phase 2 — programmatic acceptance replay

- Baseline: `develop@e1bbd6708f81437148b24b18c4035c1be8f0b8bc`
- Baseline tree: `32cc83184d855f54f41202b413942fbadc1ea76b`
- Date: 2026-07-30
- Scope: 54 `PROGRAMMATIC` rows only
- Machine ledger: `docs/audits/issue-998-criterion-ledger.json`
- Feature code changed: no
- External/elapsed-observation rows evaluated: no

## Disposition

| Result | Count |
|---|---:|
| PASS | 50 |
| FAIL | 4 |
| BLOCK | 0 |
| External rows retained for Phase 4 | 16 |

The green full pre-push gate does not override four criterion-specific proof
failures. Fixture, synthetic, hermetic and platform-skipped tests were accepted
only for the narrow programmatic contracts they exercise. They were not used
to prove real release topology, real ingress, real rollback, mature labels, or
elapsed observations.

## Exact execution evidence

### Focused backend

```text
PYTHONPATH=src /Users/apple/HurricaneSoft/trustforge/.venv/bin/python -m pytest -q \
  tests/test_asset_intrinsic.py \
  tests/test_asset_intrinsic_metamorphic.py \
  tests/test_asset_intrinsic_forbidden_inference.py \
  tests/test_asset_intrinsic_migration.py \
  tests/test_asset_intrinsic_shadow.py \
  tests/test_shadow_runtime.py \
  tests/test_shadow_dashboard.py \
  tests/test_analyze_intrinsic_shadow_api.py \
  tests/test_asset_intrinsic_benchmark.py \
  tests/test_asset_intrinsic_promotion.py \
  tests/test_asset_intrinsic_promotion_receipt.py \
  tests/test_asset_intrinsic_promotion_dataset.py \
  tests/test_asset_intrinsic_candidate.py \
  tests/test_rollback_drill.py \
  tests/test_canary_control.py
```

Result: exit 0, 355 passed.

The first attempt with unqualified system `pytest` exited 2 during collection
because that interpreter lacked `jsonschema` and `cryptography`. It proves
nothing and is retained in the machine ledger as `F0`, not hidden.

### Focused frontend

```text
npm --prefix frontend test -- --run \
  src/components/AssetIntrinsicShadowPanel.test.tsx \
  src/components/AnalysisReportView.intrinsic.test.tsx \
  --reporter=verbose
```

Result: exit 0, 2 files and 17 tests passed.

An earlier attempt that borrowed another worktree’s Vitest binary could not
resolve this worktree’s dependencies. The final evidence above ran only after
the locked local dependencies had been installed by the repository gate.

### Full repository gate

```text
TRUSTFORGE_NO_CD=1 TRUSTFORGE_PYTEST_WORKERS=4 .githooks/pre-push
```

Result: exit 0.

- Backend parallel: 6093 passed, 13 skipped.
- Backend serial: 14 passed, 1 skipped, 6105 deselected.
- Coverage: 84%, threshold passed.
- Data contracts: current.
- YAML duplicate keys: passed.
- Source stub scan: 0 unexpected, 0 stale allowlist.
- Competition QA: 24/24 passed.
- Frontend: 70 files, 588 tests passed.
- Frontend lint: passed with one existing unused catch-parameter warning.
- Frontend build: passed with one existing chunk-size warning.
- `git diff --check`: passed.

The 14 backend skips are not cited as proof for any real/external criterion.

## Row mapping

The machine ledger has a distinct Phase 2 result for all 54 programmatic IDs.
The human grouping below is only a readable index; it does not collapse the
machine rows.

| Track | Programmatic IDs | Result |
|---|---|---|
| A | A-1–A-6 | 6 PASS |
| B | B-1–B-6 | 6 PASS |
| C | C-1–C-6 | 4 PASS, 2 FAIL |
| D | D-1, D-2, D-4 | 3 PASS |
| E | E-1–E-6 | 6 PASS |
| F | F-1–F-6 | 6 PASS |
| G | G-2, G-3, G-4, G-6, G-7 | 5 PASS |
| H | H-1–H-8 | 7 PASS, 1 FAIL |
| I | I-1–I-6 | 6 PASS |
| J | J-2, J-3 | 1 PASS, 1 FAIL |
| K | none | Phase 4 external evidence only |

## Failed proof rows

### C-1 — independent control-plane separation

Current records collapse miner/pool/node/client inputs into one curated
`control_dispersion` value. Provenance prose and multiple hosts do not form a
typed, independently replayable validator/miner/node/governance plane
contract. Result: FAIL.

### C-5 — source-withdrawal replay

Canonical PIT, conflict, stale and expiry tests exist. Repository-wide search
found no source-withdrawal before/at/after-cutoff replay test. Result: FAIL.

### H-1 — canonical-core-only integration

`asset_intrinsic_candidate.py` lives in the application package and is invoked
from `agent/shadow_runtime.py`; `trustforge_core` has no candidate composition
entry point. The current import-boundary tests prove core isolation, not the
required canonical-core-only application. Result: FAIL.

### J-2 — reuse actual release-router path

The hermetic drill imports `RoutingPolicy`, but directly probes A and B loopback
services and uses an in-memory drill control setup. It does not send requests
through the actual release-router request path. Result: FAIL.

## Remediation issue drafts — not opened

### Draft R-CONTROL-PLANES — 10 hours

Title: `test(asset): typed control-plane separation and source-withdrawal PIT replay`

Addresses C-1 and C-5; depends on #870 and #998. Add a typed
validator/miner/node/governance observation contract without adding scoring
dimensions, common symbol-blind aggregation, and canonical withdrawal/expiry
PIT tests. Missing/withdrawn/conflicting inputs must fail closed.

### Draft R-CANONICAL-CANDIDATE — 12 hours

Title: `refactor(core): canonical intrinsic candidate composition boundary`

Addresses H-1; depends on #875, #876 and #998. Establish one canonical
composition boundary, forbid web/agent/orchestrator delta application, preserve
flag-off byte parity/direction, and test duplicate-application/import
boundaries. A BLOCK promotion receipt must remain non-promotable.

### Draft R-ROLLBACK-ROUTER — 10 hours

Title: `test(release): rollback drill through the actual release-router request path`

Addresses J-2; depends on #877 and #998. Exercise the production router
composition/request path in non-production without introducing another control
authority. Hermetic evidence must remain explicitly non-release evidence.

These are drafts only. CEO approval is required before opening issues or
starting remediation.

## Artifact digests

| Artifact | SHA-256 |
|---|---|
| `docs/methodology/ASSET-INTRINSIC-METHODOLOGY.md` | `e23f409bc64210b5a620b859548cce05a97ac50f94c82c6cca5548e3db1c3307` |
| `docs/reports/ISSUE-872-HOLDER-CONCENTRATION-FEASIBILITY-2026-07-29.md` | `51a6a9cbfe28ccf0637f3bd603f1bd8ed6d8338c20ab6a63742f3faa3b774f7a` |
| `data/asset_intrinsic_evidence/pep/asset:eth/manifest.json` | `1450239f10089870d051462b01e448acf1ca7b0fb9f49848a51594e89699a6c2` |
| `data/asset_intrinsic_benchmark/manifest.json` | `84fccd76c4a6839f6bfd425b925225c258e3c3c0a6913c202cc76b616b3762c9` |
| `data/asset_intrinsic_benchmark/profiles.json` | `cc76da3722102228eaee84dba87a76778808ab8987ecb21eed028180d1460986` |
| `data/contracts/intrinsic-promotion-policy.v1.json` | `ef7693b2484ac133b9eb7d92b747aef5e57e92df450a28881b46b6cac3b61294` |
| `data/intrinsic_promotion/receipt-current.json` | `70436e093f54ede40e278258b704469df1ddc4c802623e996c79f4a7421f8c21` |
| `out/pre-push/stub-scan.json` | `e99ac72fb2ece0a38ef854c16c01b301b74040205ad746863a5d998918e49942` |
| `out/pre-push/question-bank-results.json` | `575f51bd06945d104d934d2eff2ec4fa08e22014d3c1259e7d63cd7fc7c6187d` |

## Phase boundary

Phase 2 does not erase any `HISTORICAL_GAP`; fresh routed reviews remain Phase
3. The 16 external/elapsed rows remain unevaluated for Phase 4. #748 and #998
remain open, and no remediation issue has been opened.
