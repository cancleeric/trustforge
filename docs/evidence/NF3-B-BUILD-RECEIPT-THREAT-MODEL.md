# NF3-B build receipt threat boundary

The fixed-path NF3-B build receipt is authority-neutral evidence. Before any
durable burn or NF2 action, the integrated runner verifies the actual retained
`/proc/self/exe` and a root-provisioned, read-only receipt containing the
reviewed NF1/NF2 build components.

The NF2 source-tree receipt is platform-independent: it length-frames the exact
Git subtree OID and the canonical linked-source digest under a fixed domain.
It never hashes a `git archive` tar stream.

The accepted NF2 rlib and NF3 evidence-helper digests must come from identical
builds in two different root-owned checkout paths on the accepted non-container
Linux x86_64 root/systemd host. Each copy is installed sequentially at the same
systemd-managed, root-owned mode-0700 build view,
`/run/trustforge-nf3-build-input/source`, before building. This prevents Cargo's
pre-rustc package-source disambiguator from observing different checkout paths.
Every verified Cargo/Rust invocation then maps that fixed view to
`/workspace/trustforge`; the canonical profile and toolchain receipts bind this
remap. The outer evidence service must declare both
`RuntimeDirectory=trustforge-nf3-build-input trustforge-nf3-handoff` and
`RuntimeDirectoryMode=0700`; the orchestrator refuses to create or use an
unverified parent. Before starting a nested unit it copies each retained,
verified artifact generation into the empty root-owned handoff with
`O_EXCL|O_NOFOLLOW`, rechecks owner, mode, link count, size, digest and
generation, and fsyncs both file and directory. A retained Cargo output may
already have multiple hard links; its complete metadata generation binds that
source while every newly staged destination must have exactly one link. Nested
units bind only these host-visible staged paths. Successful cleanup removes
only the exact staged generations and rejects unknown entries. The accepted
exact-commit probe produced identical A/B release rlib, release probe, evidence
rlib, and evidence helper objects from the two inputs through this canonical
view. Those reviewed
native receipts are now the sole accepted pins. A locally cross-compiled
object, or an object built on another host, cannot replace them even when its
source tree and arguments appear identical. Such a change requires a new
native cross-view double-build receipt and exact-commit review.

Because systemd validates `ExecStart` before applying a unit's bind mounts, a
nested unit starts through a fixed existing executable. `/usr/bin/env` receives
the bound native probe as a separate argument and replaces itself with that
probe, preserving the probe's `/proc/self/exe` identity. `/bin/bash` receives
the bound integration script as a separate argument. Neither path uses `-c`,
shell interpolation, or an untrusted command string.

The reviewed canonical-view receipts are:

- release NF2 rlib: `1f3c09df97298013ae1d67b8618de6b66492267d0fd59b3053d9f71fa48872a4`;
- release profile probe: `1db21394225521a2fb22ee81e73a35697a14d2e8275bc6008097684a026ecb93`;
- evidence NF2 rlib: `84eeca2087f46a12d71efb472ad31d27c1322ac769b2a9793d8e6c96a2bdc8f1`;
- evidence helper: `db9f6e1f95d1aea350fe43d4a0c2392fd9f67c284a8c6207bc5d56b341798830`.

Covered failures include a missing receipt, malformed or extra fields, an
executable or component digest mismatch, symlink/hardlink substitution,
non-root receipt mutation or forgery, and path-to-file replacement during
validation. Receipt and executable metadata generations include device, inode,
mode, UID, GID, link count, size, mtime, and ctime with nanoseconds.

The receipt does not establish signer, capability, authorization, verifier, or
release authority. Root provisioning is a trusted input to native evidence.
A malicious root authoring a false receipt, a compromised kernel, and
whole-volume rollback are explicit nonclaims.
