# Native zero-capability broker v1

Issue: #1088

This Linux x86-64 broker consumes the accepted NF1 artifact without modifying
or rebuilding it. It grants no authority, transfers no descriptor to the
runtime after `execveat`, and creates no Unix control socket or `SCM_RIGHTS`
path.

## Sealed inputs

The broker pins `/`, then resolves the fixed
`opt/trustforge/native-foundation/current` chain, package directory, canonical
provenance manifest, and NF1 executable using retained descriptors and
`openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS|RESOLVE_NO_MAGICLINKS)`.
Directories must be root-owned and not group/world writable. Manifest and
runtime must be singly linked, root-owned regular files with their exact
contract modes.

The dependency-free parser requires the exact NF1 canonical JSON encoding,
schema, nested key sets, fixed enums, package paths and recursive
authority-metadata rejection. Runtime mode, size and SHA-256 are bound to the
manifest. Every boundary compares retained device, inode, owner, group, mode,
link count, size, modification time and change time and rereads bounded
manifest/runtime bytes by descriptor.

NF2 accepts exactly this externally reviewed NF1 receipt; these values are
compile-time constants and are never caller-selected:

- Git commit: `e28a675f03ee517dcd69fba0d7705ec8828d24cd`
- Git tree: `9a912277b3458c54462a8a6101db8e4766038a1f`
- Manifest SHA-256:
  `5e2db7cf733482a0c43bbfe2a27e96c3b255c1a69dde32054db3181a92fd241c`
- Archive SHA-256:
  `808487c590a183a8df2e69cfc5257969e18ae88b15c4378da95d97add6c03c1b`
- Runtime SHA-256:
  `cf8c2165cb93b7a8712d848b653d51a977f4ce12f1a9dad7bd41e189ee694f86`

The manifest VCS values, manifest bytes and runtime bytes must all match this
receipt. Self-consistent replacement manifest/runtime pairs therefore BLOCK.

## Child boundary

The only inherited runtime outputs are stdout FD 1 and stderr FD 2. FD 0 is
closed; retained exec FD 3 is `CLOEXEC`; `close_range(4, UINT_MAX)` removes all
other inherited descriptors. Before exec, the child sets and reads back
`PDEATHSIG=SIGKILL`, confirms its parent, sets and reads back
`no_new_privs=1`, installs the architecture-specific default-kill seccomp
filter and reads back filter mode.

The seccomp filter permits the measured static NF1 startup/exit syscall set.
It permits write only to FD 1 or 2, `prctl` only for the seccomp readback, and
the one pre-exec `execveat` only with FD 3 and `AT_EMPTY_PATH`. No open, dup,
socket, descriptor-transfer or read syscall is available after exec.

## External barriers

`PTRACE_TRACEME` creates an EXEC stop before the first NF1 instruction.
Ephemeral READY exists only while the exact pidfd-bound child is stopped and
all of these checks pass:

- retained proc directory identity and process start time;
- retained `/proc` executable descriptor equals the NF1 device/inode;
- `NoNewPrivs: 1`, `Seccomp: 2`, and exact live FDs `1,2`;
- every retained `map_files` descriptor belongs to the NF1 executable;
- sealed root, package, manifest and runtime identities and digests.

The same checks repeat at `PTRACE_EVENT_EXIT` while the child is stopped. The
broker accepts exactly one bounded public diagnostic stdout record and empty
stderr. It then permits exit and reaps through `waitid(P_PIDFD)`. Every wait
has a five-second deadline. Error or timeout uses `pidfd_send_signal(SIGKILL)`,
bounded pidfd polling and `waitid(P_PIDFD)`; cleanup ambiguity exits the broker
so parent-death behavior kills the child.

Numeric PID text is only an initial locator under a retained root. It is never
the identity authority.

## Platform evidence

Darwin and every non-Linux-x86-64 platform return
`BLOCKED_EXTERNAL_LINUX` (exit 77). Cross-compilation is not kernel evidence.
Acceptance requires the dedicated non-container Linux adversarial harness,
exact-commit Gray and Harper reviews, `/codex-review`, and the repository
pre-push gate. This contract provides no release, signer, publication or
production authority.

The harness requires the reviewed source tree, its exact full commit SHA, the
accepted install, and the accepted archive. It refuses a dirty or different
checkout, builds both the release broker and adversarial-hook broker itself,
and records both binary digests plus the commit, archive, manifest, runtime,
kernel, and boot ID in its evidence record. PID, executable, and map identity
hook cases are verifier-state fault injections; they are not represented as
real kernel process-substitution attacks.

Tool provenance is an explicit external prerequisite. A reviewed commit must
populate the harness's non-caller-selectable SHA-256 allowlist for root-owned,
non-writable `/usr/bin/git`, `/root/.cargo/bin/rustup`, `/usr/bin/unshare`,
`/usr/bin/mount`, `/bin/sh`, `/usr/bin/sleep`, `/usr/bin/mv`, `/usr/bin/ln`,
`/usr/bin/touch`, and the exact cargo/rustc binaries returned by rustup for
`1.96.0-x86_64-unknown-linux-gnu`. An unprovisioned allowlist returns
`BLOCKED_EXTERNAL_LINUX`; invocation arguments cannot supply trust anchors.
The harness exports the reviewed commit with verified git, builds with the
verified compiler in isolated HOME, CARGO_HOME, target, and source directories,
and executes verified absolute host-tool paths under a minimal environment
without loader variables. Evidence repeats tool digests and version identities
for independent comparison.
