# Issue #1076 — NR1a-B generic foundation plan (CEO approved)

- Author: gray (CPO); Reviewer: CEO
- Baseline: `b99cd370` (NR1a-A merged: capability.rs/CapabilitySink/ledger 7-state/SO_PEERCRED/descriptor landed)
- Date: 2026-07-31
- Status: **CEO APPROVED — execution authorized, PR-B1 first**

## Scope (#1076 acceptance #2/#3/#4)
- #2 generic manifest contract (manifest.rs NF1-bound → generic + NF1 instance)
- #3 reproducible builder (nf2/nf3 crate-local .cargo/config + rust-toolchain + byte-identical)
- #4 workspace + dedup (sha256×2, openat2/OpenHow×3, getdents64×2)

## §5 corrections (gray, source-verified)
1. close_range NOT duplicate (1 occurrence only) — excluded from native-sys.
2. manifest.rs NF1-bound = 9 categories (not few): RUNTIME_PATH/SCHEMA/ACCEPTED_*/entries.len()==5/expected_paths/cargo_resolution/generated/runtime_closure/builder_runtime/environment. Generic scope larger, PR-B1 5-6h.
3. sha256 unify on nf3 defensive version (bounded `digest(&[u8],max)->Result`) — never regress to nf2 unbounded (OOM/length-overflow).
4. provenance repin mechanism: manifest.rs + sha256.rs ∈ foundation.rs SOURCES[12]; changing them drifts 8 golden constants (§6) — must repin same PR on .83.

## PR split (B1→B2→B3, hard order)
| PR | Scope | Repin | Reviewer |
|---|---|---|---|
| PR-B1 (W7+W8) | generic manifest: AcceptedPins const struct + NF1_PINS + validate_accepted(no pins param) + NF1 instance compat | yes (interim) | gray+harper+codex |
| PR-B2 (W9) | native/Cargo.toml workspace + trustforge-native-sys (sha256/openat2/getdents64) + NR1a-A compat | yes (final) | gray+harper+codex |
| PR-B3 (W10) | nf2/nf3 .cargo/config + rust-toolchain + byte-identical double build + verify_static | no (config ∉ SOURCES) | gray+harper+codex |

## CEO red lines (CTO must obey)
1. AcceptedPins = compile-time const; validate_accepted(bytes) internally fixed &NF1_PINS, NO pins param — authority cannot inject.
2. sha256 baseline = nf3 defensive (bounded); nf2's 5 call sites pass explicit max.
3. openat2 shared layer = struct+consts only; O_NOFOLLOW|RESOLVE_BENEATH flag combos stay at call sites (path-safety).
4. Every PR repin golden on .83 (not hand-filled); rlib bytes depend on real musl build.
5. NR1a-A full .83 adversarial regression (peer-mismatch/second-exec/capability replay/seccomp/map_files/FD) must stay green — dedup = zero behavior change.

## PR-B1 design (generic manifest)
```rust
pub struct AcceptedPins { schema, runtime_path, entry_paths, entry_cardinality,
    cargo_package, generated, runtime_closure_method,
    commit, tree, manifest_sha256, runtime_sha256, archive_sha256 }  // all &'static str
pub const NF1_PINS: AcceptedPins = AcceptedPins { /* current ACCEPTED_* values */ };
pub fn validate(bytes, pins: &AcceptedPins) -> Result<RuntimeBinding, &str>  // generic schema parse
pub fn validate_accepted(bytes) -> Result<RuntimeBinding, &str> { validate(bytes, &NF1_PINS)?; verify_pin_values(...) }
```
- authority-neutral: validate_accepted signature has NO pins param (compile-time lock).
- reject_authority_metadata (14 terms) unchanged.
- builder_runtime Darwin dyld: stays as fixed sub-check (builder-host binding, NOT parameterized).
- entries.len()==5 → entries.len()==pins.entry_cardinality.
- NF1 instance compat: existing accepted manifest still validates (golden-unchanged test + behavior-equivalence fuzz).

## PR-B2 design (workspace + dedup)
- native/Cargo.toml [workspace] resolver=2 members=[hermetic-package,nf2,nf3,trustforge-native-sys].
- evidence profile stays in nf3/Cargo.toml (workspace root can't define custom profile).
- trustforge-native-sys: sha256 (nf3 baseline) + OpenHow/RESOLVE_*/SYS_OPENAT2 (shared consts) + getdents64_raw skeleton. Upper-level semantics (open_beneath/scan_once) stay local.
- nf2/nf3 Cargo.toml: trustforge-native-sys = { path = "../trustforge-native-sys" }.
- NR1a-A compat: capability.rs/ledger.rs use statements change path; descriptor_sha256 bytes unchanged (same algo, +bound). foundation.rs SOURCES[12] removes src/sha256.rs (moved to native-sys).

## PR-B3 design (builder)
- mirror hermetic-package/.cargo/config.toml + rust-toolchain.toml + toolchain-lock.json to nf2/nf3 (same Darwin builder → same dyld values).
- byte-identical: two clean builds, sha256 rlib/bin match. Reuse NF1 RUSTFLAGS (remap-path-prefix/build-id=none/SOURCE_DATE_EPOCH/incremental=0).
- verify_static_x86_64_elf: ET_EXEC/static/no-PT_INTERP.

## provenance repin checklist (8 constants, per PR on .83)
linked_nf2_source_sha256 / evidence golden 63e13c41 / release golden cd3a0b28 / NF2_LINKED_EVIDENCE_RLIB bada9d9e / NF2_LINKED_RELEASE_RLIB ef9e4d79 / NF2_SOURCE_TREE_RECEIPT 574440bd / build_receipt SOURCE bca11fbc / SOURCE_TREE_RECEIPT 02fe8e1b.

## Non-goals (unchanged)
No production signer/actor-key/PASS/eligibility (NR1b). static-only (dynamic deferred).
