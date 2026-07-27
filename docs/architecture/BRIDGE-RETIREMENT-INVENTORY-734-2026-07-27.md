# Legacy Bridge Retirement Inventory — #734

**Generated**: 2026-07-27
**Method**: Eye CLI scan + grep import analysis
**Repo**: trustforge@`346b89e2` (main, before #732/#733 merge)

---

## 1. Bridge Summary

Two scoring systems coexist in production:

| Layer | System | Version | Status |
|-------|--------|---------|--------|
| Kernel | `trustforge_core` | contract 2.2.0 | **Deterministic, sealed** |
| Legacy | `trustforge.trust.scoring` | — | **Default source of truth** |

## 2. Production Bridge Callers

### 2.1 `src/trustforge/agent/orchestrator.py` — PRIMARY
- **Legacy imports**: `score`, `aggregate`, `TrustedBrief`, `ScoredClaim`, `Claim`
- **Kernel imports**: `run_kernel` (from `trustforge_core`), `to_kernel_input`
- **Bridge call site** (line 1501-1516): Runs both legacy `score()`/`aggregate()` AND kernel `run_kernel()` in parallel
- **Action**: Must be updated to use kernel-only path when promoted

### 2.2 `src/trustforge/agent/kernel_mapper.py` — ADAPTER
- **Bridge functions**: `to_kernel_input()`, `to_legacy_scoring()`
- **Action**: `to_legacy_scoring()` can be removed once legacy bridge is retired; `to_kernel_input()` stays as application adapter

### 2.3 `src/trustforge/analysis_flow.py` — BACKGROUND WORKER
- **Legacy imports**: `score`, `aggregate`, `build_stance_fn`
- **Bridge call site** (line 1045): Calls `aggregate()` for async analysis flows
- **Action**: Must be updated to use kernel when promoted

### 2.4 `src/trustforge/pipeline.py` — ENTRY POINT
- **Legacy imports**: `extract_claims` (lazy)
- **Action**: No direct kernel awareness; passes through to orchestrator

## 3. Legacy-Only Consumers (no kernel awareness)

These files import `trustforge.trust.scoring` but have NO kernel awareness. They are downstream consumers of the scoring output:

| File | Imports | Consumer Status |
|------|---------|----------------|
| `web.py` | `TrustedBrief`, `Confidence` | **API consumer** |
| `three_track_wiring.py` | `Claim`, `ScoredClaim`, `extract_claims` | Learning event emission |
| `delayed_outcome_labeler.py` | `Claim`, `ScoredClaim`, `TrustedBrief` | Labeler engine |
| `bedrock.py` | `Claim` (lazy) | LLM client |
| `budget_guard.py` | `DEFAULT_STANCE_PAIR_BUDGET` (lazy) | Cost guard |
| `analysis_anomaly_baseline.py` | `score`, `aggregate` | Anomaly detection |
| `schema.py` | `TrustedBrief` type reference | Schema |
| `release_manifest.py` | `KERNEL_CONTRACT_VERSION` from legacy facade | Release tracking |
| `calibration_dataset.py` | kernel_schema reference | Calibration |
| `trust/dawid_skene.py` | Re-exports from `trustforge_core.dawid_skene` | Compatibility re-export |

## 4. Legacy Kernel Facade (`trust/trust/kernel.py`)

**Phase-1 facade (contract 1.0.0)** — defines its own `KernelInput`/`KernelOutput`/`run_kernel` — DIFFERENT from `trustforge_core` (contract 2.2.0).

| Consumer | Symbol Used |
|----------|-------------|
| `release_manifest.py` | `KERNEL_CONTRACT_VERSION` |
| `tests/test_golden_baselines.py` | `KernelInput`, `run_kernel` |
| `tests/test_trust_kernel.py` | Full facade boundary test |
| `tests/test_trust_kernel_v2.py` | Full facade boundary test |

**Action**: Migrate `release_manifest.py` to `trustforge_core.KERNEL_CONTRACT_VERSION`. Migrate test files to `trustforge_core`.

## 5. Test Files Requiring Migration (25+ files)

### 5.1 Bridge Tests (import BOTH legacy AND kernel)
- `test_kernel_adapter_parity.py` — Remove after bridge retirement
- `test_parity_matrix.py` — Remove after bridge retirement
- `test_core_aggregate_decision.py` — Remove legacy comparison path
- `test_multistep.py` — Update Step2 assertions
- `test_kernel_contracts.py` — Remove legacy Claim import
- `test_kernel_result_contracts.py` — Remove legacy imports
- `test_pre_score_direction_resolution.py` — Already skipped, needs removal
- `test_golden_baselines.py` — Switch from legacy facade to core

### 5.2 Kernel-Only Tests (14 files — already using `trustforge_core`)
No migration needed. These tests are ready.

### 5.3 Legacy-Only Tests (~25 files)
- Primary: `test_trust_scoring.py`, `test_report.py`, `test_prices.py`
- Cross-source: `test_tier2_divergence.py`, `test_w4_calibration.py`
- Insights: `test_insights_d11.py`, `d12.py`, `d14.py`, `d15.py`
- These test the legacy scoring system — should remain as legacy regression tests until full retirement is authorized.

## 6. Scripts with Legacy Dependencies

| Script | Legacy Import |
|--------|-------------|
| `scripts/gen_stance_cache.py` | `from trustforge.trust.scoring import Claim, _corroboration_detail` |
| `scripts/backtest_conformal.py` | `from trustforge.trust.scoring import Claim, ScoredClaim, _evidence_strength` |

**Action**: Migrate to kernel or document as compatibility scripts.

## 7. Retirement Roadmap

### Phase A: Consolidate Facade (now)
- [ ] `release_manifest.py`: switch `trustforge.trust.kernel` → `trustforge_core`
- [ ] `test_golden_baselines.py`, `test_trust_kernel.py`, `test_trust_kernel_v2.py`: migrate to `trustforge_core`
- [ ] `trustforge/trust/dawid_skene.py`: remove compatibility re-export

### Phase B: Dual-Track Promotion (#732 blocking)
- [ ] `orchestrator.py`: when `is_kernel_promoted()`, use kernel-only path (stop calling legacy `score()`/`aggregate()`)
- [ ] `analysis_flow.py`: respect kernel promotion
- [ ] `kernel_mapper.py`: `to_legacy_scoring()` can remain as promotion bridge until Phase C

### Phase C: Full Retirement (requires production cutover authorization)
- [ ] Remove `to_legacy_scoring()` from `kernel_mapper.py`
- [ ] Remove legacy `score()`/`aggregate()` call sites
- [ ] Remove `trustforge/trust/kernel.py` Phase-1 facade
- [ ] Remove bridge test files
- [ ] Legacy test files become historical regression suite

## 8. Pre-Retirement Verification

Before removing any bridge:

1. **Eye scan**: `eye blast-radius src/trustforge/agent/kernel_mapper.py:to_legacy_scoring` — confirm zero callers
2. **Eye scan**: `eye blast-radius src/trustforge/trust/scoring.py:aggregate` — confirm only test/comment callers
3. **Consumer inventory** (this document) — confirm no undocumented consumers
4. **Full test suite pass** — `pytest --cov --cov-fail-under=75`

## 9. Architecture Evidence

**Current state**: Legacy scoring is the primary source of truth. Kernel runs in shadow.
**Target state**: Kernel is the primary source of truth. Legacy exists only as compatibility facade for downstream consumers that accept `TrustedBrief`/`ScoredClaim`.

**Consumer contract**:
- `TrustedBrief` and `ScoredClaim` dataclasses are the stable interface boundary.
- A kernel-promotion-friendly `build_report()` can accept both legacy and kernel-derived briefs.
- Internal implementation of `score()`/`aggregate()` can change without breaking consumers.
