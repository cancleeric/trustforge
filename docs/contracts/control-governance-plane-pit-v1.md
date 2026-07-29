# Control and governance plane PIT contract v1

Issue: #1035, remediating #998 C-1 and C-5.

## Boundary

This contract is an evidence-to-fact layer.  It produces only the existing
`control_dispersion` and `governance_capture_resistance` dimension names.  It
does not add a sixth dimension, score an asset, infer a ranking, modify a
promotion receipt or bypass a signed `BLOCK`.

No observation contains an asset symbol, name or issuer label.  Consensus kind
selects a common protocol rule:

- proof of work: miner/pool plus node/client;
- proof of stake: validator plus node/client;
- hybrid: validator, miner/pool and node/client.

Governance is always replayed independently.

## Typed planes and attribution

Every observation binds one plane, source ID, source family, canonical HTTPS
source URL, control-entity ID, positive source revision, evidence digest,
numeric value, observation/fetch timestamps and optional validity end.
Evidence kinds are plane-specific:

- validator and miner/pool: `entity_measurement`;
- node/client: `client_telemetry`;
- governance: `governance_record`.

Documentation prose is not an accepted evidence kind.  Multiple hosts assigned
to one source family remain one family and cannot satisfy independence.
Within `(plane, source_id)`, the greatest PIT-visible revision is selected
before validity, freshness or withdrawal is evaluated.  An expired, stale or
withdrawn latest revision makes that source unavailable; replay never falls
back to an older revision.

## PIT and withdrawal

An observation is knowable only when both its observation and fetch timestamps
are at or before the cutoff.  A withdrawal is knowable only when both its
effective and fetch timestamps are at or before the cutoff, and it must bind
the exact source ID and observation ID.

The latest visible revision is selected before validity, freshness and
withdrawal are applied.  If that revision is unavailable, replay does not fall
back to an older revision.
Therefore:

- immediately before a withdrawal cutoff, the observation remains visible;
- exactly at the cutoff, a known withdrawal removes it;
- after the cutoff, it remains removed;
- a withdrawal fetched after the cutoff cannot rewrite the earlier PIT view.

## Fail-closed aggregation

Expired or older-than-freshness inputs are not known.  Missing, withdrawn and
stale required planes produce `UNKNOWN` with no value.  A value spread greater
than `0.20` inside a plane produces `CONFLICT`; a conflicted required plane
propagates `CONFLICT` with no dimension value.

After latest-revision selection, observations are first collapsed to one
deterministic contribution per canonical source family.  Duplicate source IDs,
hosts or aliases in a family cannot add votes; duplicate values and evidence
digests are set-deduplicated.  Irreconcilable aliases inside one family fail
closed as `CONFLICT`.  Plane values then use an equal-weight mean across
families.  A dimension is known only when every required plane is known and
the union contains at least two typed source families.  Ordering, alias count,
source hosts and identity labels cannot alter the result.

The module is not connected to promotion.  The current signed promotion
receipt remains `BLOCK`; later promotion requires the existing signed gate and
is outside #1035.
