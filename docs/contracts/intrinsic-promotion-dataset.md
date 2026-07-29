# Canonical intrinsic promotion evidence dataset

The promotion evidence producer reads only the persisted canonical shadow
observation store. Checked-in intrinsic fixtures are test inputs and are never a
production evidence source.

`scripts/build_intrinsic_promotion_dataset.py` requires an explicit release
identity, PIT cutoff, staleness window, read-only shadow database, and new output
path. The output is canonical JSON written once with mode `0600`.

The producer:

- validates the exact release tuple, policy digest, store schema, observation
  payload, and terminal completion chain;
- includes only records durable by the PIT cutoff and rejects future,
  stale, malformed, conflicted, or semantically inconsistent retry evidence;
- deduplicates identical retries by canonical input digest;
- preserves observed asset and source-family coverage for each event day;
- reports observation, asset, day, and source-family counts; and
- binds all observations, provenance, PIT policy, and coverage to an immutable
  domain-separated SHA-256 dataset digest.

Consumers must verify `dataset_digest` before using `observations`. A valid
dataset is evidence for the existing research promotion gate; it is not itself
authorization to promote or deploy.
