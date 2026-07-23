# T+1/T+7/T+14 Outcome Label Semantics

Status: CEO/product disposition required before labeler implementation.
Issue: #501.

This document defines the product and data rules for delayed outcome labels.
It is intentionally implementation-neutral: it does not create a labeler, alter
storage, or backfill data.

## Decisions

| Decision | Rule |
| --- | --- |
| Calendar | `T+n` uses the primary listing exchange trading calendar for the instrument. |
| Timezone | Calendar cutoffs use the primary exchange timezone. Persisted event times stay UTC. |
| Anchor | `T0` is the first eligible regular-session close strictly after the analysis event time. |
| T+N maturity | `T+n` is the close of the nth eligible trading session after `T0`. |
| Price source | Official adjusted OHLCV close from the configured market data vendor. |
| Adjustment policy | Use split/dividend-adjusted close for return fields and raw close only for audit metadata. |
| Late data | A label is unavailable until source `available_at` is later than the maturity close plus vendor SLA. |
| Revisions | Revised market data creates a new label revision; prior label events remain immutable. |
| Missing data | Missing source data yields `pending` before SLA and `unavailable` after SLA. |
| Suspended trading | If no eligible session occurs inside the horizon guard, label is `unavailable`. |
| Corporate actions | Corporate action effective dates must be reflected in adjusted close provenance. |
| Intraday analyses | Analyses before regular-session close anchor to that same session; after close anchor to next eligible session. |

## Fields

| Field | Formula / source | Event time | Available time | Null semantics |
| --- | --- | --- | --- | --- |
| `analysis_id` | Stable id of the source immutable analysis event. | Analysis event time. | Analysis event `available_at`. | Never null; missing id makes the sample ineligible. |
| `instrument_id` | Canonical instrument/security id from the analysis scope. | Analysis event time. | Analysis event `available_at`. | Never null. |
| `horizon` | One of `T+1`, `T+7`, `T+14`. | Analysis event time. | Analysis event `available_at`. | Never null. |
| `anchor_session` | First eligible close strictly after event time unless event is before regular close. | Exchange session close. | Calendar availability. | Null only when no eligible anchor can be resolved. |
| `maturity_session` | nth eligible session after `anchor_session`. | Exchange session close. | Calendar availability. | Null when suspended/delisted beyond horizon guard. |
| `anchor_adjusted_close` | Vendor adjusted close for `anchor_session`. | Anchor close. | Vendor `available_at`. | `pending` before SLA; `unavailable` after SLA/source failure. |
| `maturity_adjusted_close` | Vendor adjusted close for `maturity_session`. | Maturity close. | Vendor `available_at`. | `pending` before SLA; `unavailable` after SLA/source failure. |
| `return_pct` | `(maturity_adjusted_close / anchor_adjusted_close) - 1`. | Maturity close. | Max source `available_at`. | Null if either close is pending/unavailable. |
| `direction` | `up` if `return_pct > epsilon`, `down` if `< -epsilon`, else `flat`. | Maturity close. | Same as `return_pct`. | Null if `return_pct` is null. |
| `risk_band` | `large_gain`, `gain`, `flat`, `loss`, `large_loss` by product thresholds. | Maturity close. | Same as `return_pct`. | Null if `return_pct` is null. |
| `maturity_state` | `pending`, `matured`, or `unavailable`. | Evaluation time. | Evaluation time. | Never null. |
| `source_revision` | Vendor dataset/version/checksum for price and adjustment inputs. | Source publish time. | Source publish time. | Never null for `matured`; optional while pending. |
| `label_revision` | Monotonic revision id for immutable label event. | Label event time. | Label event `available_at`. | Never null. |

## Thresholds

`epsilon` and risk-band thresholds are product decisions, not engineering
defaults. Until CEO/product disposition is recorded, generated fixtures must
use explicit test-local thresholds and must not be promoted to product truth.

Recommended decision slots:

| Parameter | Required disposition |
| --- | --- |
| `epsilon` | Minimum absolute return treated as directional rather than flat. |
| `large_gain_threshold` | Return at or above this threshold is a large gain. |
| `gain_threshold` | Return at or above this threshold is a gain. |
| `loss_threshold` | Return at or below this threshold is a loss. |
| `large_loss_threshold` | Return at or below this threshold is a large loss. |
| Vendor SLA | Delay after close before missing source data becomes unavailable. |
| Horizon guard | Maximum real-world days before a suspended/delisted horizon is unavailable. |

## Boundary Decision Table

| Case | Input | Expected outcome |
| --- | --- | --- |
| Weekday before close | Analysis Monday 13:00 exchange time; regular close Monday. | `T0` is Monday close; T+1 is Tuesday close. |
| Weekday after close | Analysis Monday 17:00 exchange time; regular close Monday. | `T0` is Tuesday close; T+1 is Wednesday close. |
| Weekend | Analysis Saturday UTC mapped to closed exchange day. | `T0` is next eligible regular-session close. |
| Exchange holiday | Analysis on a holiday. | Skip holiday; `T0` is next eligible trading close. |
| Half day | Eligible exchange session with official close. | Counts as one eligible session; use official half-day close. |
| Trading halt resumes | Halted intraday but official close exists. | Use official close if source provides valid OHLCV. |
| Full suspension | No eligible close through horizon guard. | `maturity_state=unavailable`, return/direction/risk null. |
| Delisting before maturity | No maturity close can be resolved. | `unavailable`; provenance records delisting source. |
| Split between anchor and maturity | Adjusted close revision includes split factor. | Return uses adjusted closes; raw close stored for audit only. |
| Dividend adjustment revision | Vendor revises adjusted close after first label. | Append new label revision; do not mutate prior label. |
| Late OHLCV | Maturity close passed, vendor data absent before SLA. | `pending`. |
| Source failure after SLA | Vendor data still absent after SLA. | `unavailable`. |
| Data correction | Vendor republishes corrected close. | New immutable label revision with new source revision. |
| Zero or negative adjusted close | Source emits invalid adjusted close. | `unavailable`; invalid source noted in provenance. |
| Multi-listing ambiguity | Instrument has multiple trading calendars. | Use canonical primary listing; unresolved primary listing is unavailable. |

## Non-Goals

- No labeler implementation.
- No database migration.
- No production backfill.
- No hidden default for unresolved product thresholds.
- No conversion of delayed outcomes into Evidence.
