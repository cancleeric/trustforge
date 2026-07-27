# Legacy → Core → Product Parity Matrix

Contract version: 2.2.0
Generated: issue-727-parity-matrix

## Layer 1: Core Kernel Fields

| Field | KernelOutput | KernelScoredClaim | Description |
|-------|-------------|-------------------|-------------|
| trust_score | float [0,1] | — | Aggregated trust of supporting claims |
| confidence | float [0,1] | — | Calibrated aggregate confidence |
| abstain | bool | — | True when decision_state == "abstain" |
| direction | str | — | "bullish" / "bearish" / "neutral" / "偏多" / "偏空" / "中性" / "不明" |
| decision_state | str | — | "abstain" / "low_confidence" / "normal" |
| reason_codes | tuple[str] | — | "low_calibrated_confidence" / "insufficient_independent_sources" / "below_normal_confidence" |
| supporting_count | int | — | Length of supporting tuple |
| independent_sources | int | — | Count of canonical supporting sources |
| scored_claims | tuple[KernelScoredClaim] | — | All scored claims in input order |
| supporting | tuple[KernelScoredClaim] | — | Top supporting claims (limit 10) |
| contrarian | tuple[KernelScoredClaim] | — | Top contrarian claims (limit 5) |
| — | trust | float [0,1] | Per-claim trust score |
| — | components | tuple[(str,float)] | reputation, corroboration, recency, manipulation |
| — | reputation_trace | KernelReputationTrace|null | DS/entailment trace |
| — | manip_flags | tuple[str] | Manipulation keyword hits |
| — | info_flags | tuple[str] | Info-only coordination signals |

## Layer 2: Legacy Adapter Mapping

### ScoredClaim ← KernelScoredClaim

| Legacy Field | Core Source | Exact |
|-------------|-------------|-------|
| claim | claim_by_id[ksc.claim.id] | Yes |
| trust | ksc.trust | Yes |
| components | dict(ksc.components) | Yes |
| reputation_trace | _trace_to_dict(ksc.reputation_trace) | Yes |
| manip_flags | list(ksc.manip_flags) | Yes |
| info_flags | list(ksc.info_flags) | Yes |

### TrustedBrief ← KernelOutput

| Legacy Field | Core Source | Exact |
|-------------|-------------|-------|
| query | output.query | Yes |
| supporting | mapped ScoredClaim list from output.supporting | Yes |
| contrarian | mapped ScoredClaim list from output.contrarian | Yes |
| confidence | output.trust_score | Yes |
| calibrated_confidence | output.confidence | Yes |

## Layer 3: Product Projection (Orchestrator)

The orchestrator bridges TrustedBrief → Report + Evidence. This is the third layer of the parity chain and is not directly part of the core/legacy parity matrix.

## Known Differences

### Corroboration in no-resolution path

**Classification**: semantic
**Summary**: Core `run_kernel()` with `resolution=None` does not compute corroboration, while legacy `score()` always computes `_corroboration_detail()`. This causes confidence values to differ.

**Cases affected**: support, contradiction, sparse_evidence, calibration, direction, pit_boundary, manipulation
**Resolution**: Core resolution path requires outer adapters to pre-compute corroboration and pass via `KernelClaimResolution.independent_sources`. This is an intentional architectural separation.

### Identical cases

**Cases**: abstain, duplicate_source, failure_cases
**Classification**: compatibility
**Summary**: These cases produce byte-identical outputs between core and legacy.

## Validation Gates

1. `test_all_parity_cases_deterministic()` — Each case produces identical output when run twice
2. `test_all_parity_cases_match_golden()` — Output matches committed fixture JSON
3. `test_pythonhashseed_cross_validation()` — Output is identical across PYTHONHASHSEED values
4. `test_to_legacy_scoring_field_exactness()` — Field mappings are exact and validated
5. `test_all_differences_have_disposition()` — All differences have owner + disposition
