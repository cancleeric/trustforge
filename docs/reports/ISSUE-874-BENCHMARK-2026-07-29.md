# ISSUE-874 — Multi-Asset PIT Asset-Intrinsic Benchmark

**Date:** 2026-07-29
**Branch:** `feat/874-pit-benchmark`
**Disposition:** `remain-shadow`
**Status:** Benchmark delivered; no ranking produced.

## 1. Purpose and framing

This benchmark is a **measurement instrument, not a ranker**. It replays a
symbol-blind profile corpus through the *real* shadow assessor
(`assess_intrinsic_shadow`) and observation builder
(`build_intrinsic_shadow_observation`) at a single fixed point-in-time cutoff,
and records four purely observational measurements. It never asserts that one
asset must score above another, and it never re-implements scoring.

Where factual coverage is insufficient to support a conclusion, the recorded
disposition is **`remain-shadow`**: the observation stays observational and
affects no official score.

## 2. Deliverables

| Deliverable | Path | Status |
|---|---|---|
| D1 symbol-blind corpus | `data/asset_intrinsic_benchmark/profiles.json` + 14 evidence bytes under `data/asset_intrinsic_evidence/bench-*.txt` | delivered |
| D2 PIT replay engine | `src/trustforge/asset_intrinsic_benchmark.py` | delivered |
| D3 four measurements | emitted into the manifest | delivered |
| D4 reproducibility manifest | `data/asset_intrinsic_benchmark/manifest.json` (golden) | delivered |
| D5 replay-layer metamorphic tests | `tests/test_asset_intrinsic_benchmark.py`, `tests/test_asset_intrinsic_metamorphic.py` (M5–M7 + coverage + no-leak + real replay) | delivered |

### Corpus (7 anonymous profiles, ≥6 required)

Names are measurement descriptors (value magnitude or coverage shape), never
trust judgments. The forbidden tokens `good / bad / safe / risky` are rejected
structurally by the corpus builder and verified absent by test.

| Profile | Coverage shape | Gate | `total_delta` |
|---|---|---|---|
| `anon-5known-high` | 5 known, 2 families, value-magnitude high | pass | +0.0704 |
| `anon-5known-low` | 5 known, 2 families, value-magnitude low | pass | −0.0704 |
| `anon-3known-boundary` | 3 known (gate boundary) + 2 unknown | pass | +0.0192 |
| `anon-conflicted` | 3 known + 1 conflicted (recorded) + 1 unknown | pass | +0.024 |
| `anon-stale` | 5 known, all `as_of` > 365 days before cutoff | pass | 0.0 |
| `anon-single-family` | 5 known, 1 source family | **fail** | 0.0 |
| `anon-future-gap` | 4 fresh known + 1 future-known (omitted by PIT view) | pass | +0.0304 |

## 3. The four measurements

### (a) Factual-distance vs score spread

The signed contribution of each eligible known dimension is
`(value − 0.5) × weight`, summed and capped at `±0.08`. The corpus confirms the
mapping is linear and cap-bounded, and records the spread only — **no ranking**.

| Profile | signed factual distance | `total_delta` |
|---|---|---|
| `anon-5known-low` | −2.20 | −0.0704 |
| `anon-3known-boundary` | +0.60 | +0.0192 |
| `anon-conflicted` | +0.75 | +0.024 |
| `anon-future-gap` | +0.95 | +0.0304 |
| `anon-5known-high` | +2.20 | +0.0704 |
| `anon-stale` | +2.20 | **0.0** |

Score spread over gate-passing members: min **−0.0704**, max **+0.0704**,
range **0.1408**.

### (b) Coverage bias (gate-pass distribution)

Distribution only; **no fairness conclusion is drawn**.

- gate passed: 6 / 7 (0.857)
- gate failed: 1 / 7
- `gate.reason_code` distribution: `{eligible: 6, insufficient_coverage: 1}`
- `known_count` distribution: `{3: 2, 4: 1, 5: 4}`
- `source_family_count` distribution: `{1: 1, 2: 6}`

### (c) Extreme-value sensitivity (single-dimension sweep)

A corpus-independent canonical anon view (5 known, 2 families) has one
dimension swept across `{0, 0.25, 0.5, 0.75, 1}` while the other four are held
at the 0.5 neutral. The response is strictly monotonic and identical in shape
for every dimension (measurement only; no cross-dimension ranking):

| swept value | `total_delta` |
|---|---|
| 0.00 | −0.016 |
| 0.25 | −0.008 |
| 0.50 | 0.0 |
| 0.75 | +0.008 |
| 1.00 | +0.016 |

### (d) Single-source manipulation

Collapsing the canonical view's source families from two to one flips the gate
and zeroes every contribution — the contribution is contingent on independent
corroboration, not on the factual values alone.

| | before | after |
|---|---|---|
| `gate.passed` | true | **false** |
| `source_family_count` | 2 | 1 |
| `total_delta` | +0.048 | **0.0** |

`gate_flipped = true`.

## 4. Notable measurement findings

- **Stale facts neutralize contribution even when the coverage gate passes.**
  `anon-stale` has 5 known, multi-family facts (gate passes) yet `total_delta`
  is 0.0 because every fact is older than the 365-day staleness window.
- **`pit_view` does not surface `conflicted` facts at this schema version.** A
  conflicted fact is neither `eligible_at` nor `visible_unknown_at`, so the
  real PIT replay path (`AssetIntrinsicRepository.pit_view`) drops it and the
  assessor renders it `fact_unavailable` (and `conflict_detected` is false).
  The assessor's `fact_conflicted` branch is therefore unreachable through PIT
  replay; the benchmark covers it with an explicitly-labeled direct-view probe
  (`coverage_probe`) for coverage completeness. Surfacing conflicted facts
  through `pit_view` is out of scope for #874 and is recorded here as an
  observation only.

## 5. Reproducibility

The manifest records: `benchmark_version`, `assessment_schema_version`,
`intrinsic_shadow_observation_version`, `asset_intrinsic_schema_version`,
`data_version` (SHA-256 of `profiles.json`), `evidence_version` (per-file
SHA-256), `pit_cutoff = 2026-07-29T00:00:00Z`, `seed = 874`, a neutral
baseline/candidate anchor of 0.5, per-profile `facts_hash` / `gate` /
`total_delta`, the four measurement tables, and `disposition = remain-shadow`.
There is **no ranking field** (`ranking: null`).

The checked-in `manifest.json` is byte-identical to a fresh run
(`test_golden_manifest_matches_fresh_run_byte_for_byte`).

## 6. Test evidence

```
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/test_asset_intrinsic_benchmark.py \
  tests/test_asset_intrinsic_metamorphic.py \
  tests/test_asset_intrinsic.py \
  tests/test_asset_intrinsic_shadow.py -q
=> 110 passed
```

Metamorphic / coverage guarantees:

- **M5** identity rename: renaming every corpus `asset_id` leaves the manifest
  byte-identical except the `label` fields.
- **M6** input permutation: 10 shuffle seeds leave every statistic unchanged.
- **M7** same-facts cross-symbol: BTC facts under a carrier symbol produce an
  identical `total_delta`.
- **No-leak**: the synthetic sections name no real symbol
  (`BTC / BNB / ETH / bitcoin`).
- **Coverage completeness**: all 4 dimension statuses, all 6 dimension
  reason codes, and both gate reason codes are exercised.
- **Real-asset replay** at the cutoff: `BTC gate.passed = true`;
  `ETH / BNB gate.passed = false` (empirical coverage facts).
