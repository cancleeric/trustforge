# Kernel contract 2.2 migration

Kernel contract 2.2 adds an optional, immutable outer-resolution graph without
moving provider, I/O, or runtime behavior into `trustforge_core`.

## Caller migration

- Existing positional construction remains valid: the first five
  `KernelInput` fields are unchanged. Omitting `contract_version` and
  `resolution` selects contract `2.2.0` with `resolution=None`.
- Explicit contract `2.1.0` input is rejected. Upgrade serialized callers to
  emit `2.2.0`; there is no implicit old-version coercion.
- Resolved callers append a `KernelRunResolution` after `contract_version`.
  Its `claim_resolutions` must match input claim IDs exactly and in order.
- Outer adapters must canonicalize and deduplicate source identities before
  crossing the boundary. Core contracts reject aliases, domains, whitespace,
  case variants, and duplicates rather than silently rewriting them.
- Empty policy tuples mean “use canonical scoring defaults at composition
  time.” Explicit nonempty policy tuples are validated by the contract.

`KERNEL_RESOLUTION_VERSION` starts at `1.0.0`. Resolution DTOs are frozen,
slotted, sealed, strict immutable-JSON values. Contract 2.2 only introduces
these values; resolved execution is wired separately in the composition change.
