# Native hermetic package and provenance v1

Issue: #1087 (NF1)

This contract defines an authority-neutral build artifact. It proves only that
accepted inputs produce identified bytes. It does not launch a process, transfer
a descriptor, authorize an actor, access a key, commit a transaction or confer
evidence/release eligibility.

## Inputs

The builder accepts a clean Git worktree and the fixed
`native/hermetic-package` source closure. It refuses:

- dirty or non-Git source trees;
- symlinks and special source files;
- a Rust toolchain other than the pinned `1.96.0`;
- a missing `x86_64-unknown-linux-musl` target/sysroot/libc closure;
- third-party Cargo dependencies without a complete local vendor tree;
- network-dependent Cargo resolution;
- generated source that differs from the canonical builder recipe;
- authority-bearing manifest keys.

The effective closure records the builder, all crate/Cargo/config/generated
inputs, Cargo resolution, `cargo`, `rustc`, `rust-lld`, the complete target
library/sysroot input tree, deterministic environment values and Git
commit/tree.

## Build isolation

The source crate is copied to a fresh build-input directory. Cargo receives a
fresh empty `CARGO_HOME`, non-user `HOME`, explicit pinned executables,
`--locked --offline --frozen`, fixed locale/timezone/epoch, disabled
incremental compilation and a path-remapping flag. This prevents a user Cargo
configuration or clone path from changing the result.

The current package has no third-party dependency, so its vendor closure is
explicitly empty. Adding one without a content-bound vendor tree returns
`BLOCK`.

## Outputs

`scripts/build_native_hermetic_package.py` writes:

- `native-hermetic-provenance.json`: canonical JSON using schema
  `trustforge.native-hermetic-provenance/v1`;
- `native-hermetic-package.tar`: deterministic USTAR with sorted entries,
  fixed epoch, root ownership, empty owner names and fixed `0555`/`0444`
  modes;
- `native-hermetic-digests.json`: SHA-256 identities for the manifest, archive
  and runtime.

The runtime must be ELF and have neither `PT_INTERP` nor `DT_NEEDED`. If an
ELF inspection tool is unavailable, the builder performs a conservative loader
reference scan and records that method; any suspected dynamic dependency
blocks.

## Reproducibility and negative evidence

Acceptance requires two separately cloned clean worktrees and separately
created build/output/Cargo-home directories to produce byte-identical runtime,
manifest and archive digests. Tests additionally mutate source, lockfile,
toolchain configuration, flags, generated input and tool/linker bytes. A
mutation must change provenance or block.

## Forbidden authority metadata

The provenance schema recursively rejects actor/key/raw-key/signer/trust-root,
verdict/PASS, eligibility and publication-authority fields. These concepts
belong to downstream approved work and cannot be added as NF1 metadata.

Darwin cross-compilation can establish NF1 reproducible bytes, but it is not a
real-Linux execution PASS. Real non-container Linux adversarial execution is
owned by NF3.
