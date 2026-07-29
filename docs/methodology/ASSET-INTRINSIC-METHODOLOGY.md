# Asset Intrinsic Methodology v1.0.0

## Five-Dimension Measurement

| Dimension | Definition | [0,1] Scale |
|---|---|---|
| **Issuance Predictability** | Deterministic issuance rule reproducible from pinned source | 1.0 = fully deterministic; 0.0 = discretionary/unbounded |
| **Control Dispersion** | Diversity of miner, pool, node-operator, and client control | 1.0 = highly dispersed; 0.0 = concentrated under single entity |
| **Supply Verifiability** | Independent full-node auditability of total supply and UTXO set | 1.0 = independently verifiable; 0.0 = opaque or trust-required |
| **Governance Capture Resistance** | Resistance of off-chain/on-chain governance to capture by a single actor | 1.0 = high resistance; 0.0 = captured by design |
| **Holder Concentration** | Distribution of token holdings across identifiable addresses/entities | 1.0 = broadly distributed; 0.0 = concentrated in identifiable wallets |

## Normalization

Every dimension value is clamped to the [0.0, 1.0] interval. Numeric values must be finite.

The shadow assessment maps `raw` → `(raw − 0.5) × weight` to produce a signed delta per dimension. The total delta is capped at ±0.08.

## Status Rules

| Status | Meaning | Delta Contribution |
|---|---|---|
| **known** | Value verified from upstream source | Full contribution if eligible |
| **unknown** | No qualifying dataset exists | Zero delta |
| **stale** | Assessment as_of exceeds STALE_WINDOW_DAYS threshold relative to dimension as_of | Zero delta; status displayed as "stale" |
| **conflicted** | Multiple sources disagree irreconcilably | Zero delta; excluded from PIT view |

## Stale Window

A dimension becomes stale when the assessment point-in-time (`assessment_as_of`) minus the dimension's fact date (`dimension.as_of`) exceeds `STALE_WINDOW_DAYS` (365 days). Stale dimensions contribute zero delta and carry status "stale" in shadow output.

## Source-Family Rule

Each source URL is mapped to its host-family (eTLD+1) via `normalized_source_family()`. The coverage gate requires at least 2 distinct source families among all eligible known dimensions. This prevents single-publisher dominance.

## PIT (Point-in-Time) Rule

The shadow assessment starts from one `AssetIntrinsicView` at a specific `as_of`. Only dimensions that are eligible at that `as_of` (valid_from ≤ as_of ∧ fetched_at ≤ as_of ∧ as_of ≤ as_of ∧ not expired) contribute to the assessment.

## Unknown Rule

Dimensions with status "unknown" are visible in the PIT view but contribute zero delta. Their coverage and provenance are reported.

## Forbidden Inference Catalog

The following inference patterns are **prohibited** in any `provenance.methodology` field. Each pattern represents an invalid claim-to-fact bridge that is not independently verifiable.

| # | Category | Description | Detection Pattern |
|---|---|---|---|
| 1 | Price-inferred | Deriving intrinsic facts from market price data | `(price|价格).*(infer|推|derive|導出)` |
| 2 | Lost-key estimates | Using estimated lost/irretrievable key counts | `lost\s+(coin|key|wallet|私鑰|錢包)` |
| 3 | Address=entity | Treating blockchain addresses as individual entities | `(address|地址)\s*(is|==|＝|equals|＝)\s*(entity|実体|entity)` |
| 4 | Popularity-inferred | Inferring intrinsic quality from popularity metrics | `(popularity|受欢迎|popularité).*(infer|推|implies|暗示)` |
| 5 | Wall Street ownership | Citing Wall Street or institutional ownership as intrinsic fact | `(Wall\s*Street|华尔街|ウォール街).*(ownership|所有權|保有)` |
| 6 | Issuer/symbol hardcode | Hardcoding facts based on issuer identity or ticker symbol | `(issuer|symbol|発行者|発行体)\s*(is|=|等于|＝).*(安全|secure|safe|deterministic|確定)` |

Each pattern is detected via case-insensitive regex matching against the full methodology text. If any violation is found, the result is rejected fail-closed.

## Versioning

Schema version: 1.0.0 (same as ASSET_INTRINSIC_SCHEMA_VERSION).

### Migration Contract

The `asset_intrinsic_migration_contract()` function returns:

- `schema_version`: "1.0.0"
- `supported_migrations`: empty (no prior schema versions exist)
- `description`: "Initial schema; five-dimension asset-intrinsic profiles with PIT-safe views."
- `breaking_changes`: empty
