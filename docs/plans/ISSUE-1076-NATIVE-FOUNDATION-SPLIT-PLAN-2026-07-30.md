# Issue #1076 — native foundation hard-limit split plan

- Owner: gray (CPO)
- Parent: #1057
- Baseline: `origin/develop@ceab63e165102900fb40bef749c9ebf843ea2ebb`
- Date: 2026-07-30
- Status: **CEO APPROVED — child issue creation authorized**
- Current disposition: **#1076 OPEN / BLOCK**
- Priority: **P0 foundation; P1 durability and release evidence**
- Deliverables: three child issues, each no more than 12 hours

## 1. Gray review decision

The exact implementation candidate
`ec4188d6d021927aee7a683fdffbe783fc956cae` received three independent BLOCK
dispositions. It is **void** as completion, review, test, release or provenance
evidence and must not be pushed, merged or incrementally repaired. Its worktree
and artifacts are not an accepted dependency of this plan.

The failed attempt combined three independently security-critical concerns:

1. creating a hermetic, reproducible package whose provenance covers every
   effective build input;
2. enforcing native kernel isolation and a descriptor boundary;
3. making a one-shot transaction durable under replay, timeout and crash while
   proving it on real Linux.

That scope cannot receive adequate adversarial review inside one 10–12 hour
issue. #1076 therefore stops and splits at those boundaries.

| Child | Priority | Estimate | Dependencies | Deliverable |
|---|---|---:|---|---|
| NF1 — hermetic reproducible package and provenance manifest | P0 | 8–12h | accepted source contracts only | Independently reproducible package with complete source/toolchain/linker provenance and no authority metadata |
| NF2 — native kernel isolation broker | P0 | 10–12h | accepted NF1 package contract | Native broker with exact architecture-specific isolation, total FD closure and typed public-only capability boundary |
| NF3 — durable one-shot transaction and real-Linux adversarial E2E | P1 | 10–12h | accepted NF1 and NF2 | Crash/replay-safe transaction state and real non-container Linux evidence; no release PASS |

At hour 6 Gray must review remaining scope. Work stops and splits again before
hour 12 rather than weakening acceptance.

## 2. Dependency graph and ownership

```text
accepted source contracts
          |
          v
   NF1 hermetic package
          |
          v
   NF2 kernel broker
          |
          +------------------+
          |                  |
          v                  v
 NF3 durable state     later production
 + real-Linux E2E      trust/key binding
```

NF1 owns build inputs and bytes. NF2 consumes the immutable NF1 package and
owns only the live kernel boundary. NF3 consumes both accepted contracts and
owns transaction durability plus real-Linux adversarial evidence.

None of the children owns production actor identity, a production public or
private key, signer selection, authorization verdict, evidence eligibility,
publication or release promotion. Those remain downstream work under the
approved #1057 dependency plan.

An exact child merge SHA and a fresh post-merge gate are required before the
next child may be marked unblocked.

## 3. Shared fail-closed rules

The following never count as PASS:

- Darwin execution, container execution, fixtures or mocked kernel state;
- output from the void `ec4188d6...` prototype;
- an archive built from an uncommitted or dirty source tree;
- caller-provided actor, key, signer, trust root, path, FD, PID, UID, digest,
  verdict or authority metadata;
- a partial manifest, ambient loader/tool lookup or an unrecorded generated
  input;
- parser/unit success standing in for kernel enforcement or installed Linux;
- a broker-owned in-memory flag standing in for durable replay/crash state.

Unavailable real non-container Linux reports `BLOCKED_EXTERNAL_LINUX`. A
failed or unverifiable invariant reports `BLOCK`. Neither may be translated to
PASS, release eligibility or publication authority.

## 4. NF1 — hermetic reproducible package and provenance manifest

### Scope

NF1 produces a canonical package and provenance contract from a clean,
independent build environment. It contains no broker transaction logic and no
actor/key metadata.

### Required design

- Pin the complete source tree by canonical relative path, type, mode, size and
  digest, including Rust sources, `Cargo.toml`, `Cargo.lock`, build scripts,
  vendored crates, generated-source recipes, configuration and test runtime
  sources.
- Pin the Rust toolchain components and target, Cargo version/configuration,
  target specification, linker identity and digest, linker arguments, libc or
  musl inputs, archive/binutils tools and relevant deterministic environment
  variables.
- Resolve dependencies offline from a checked and digested vendor tree or an
  equivalently immutable content-addressed input. Network access during the
  reproducibility build is forbidden.
- Record the exact VCS tree/commit, dirty-state refusal, build command,
  `SOURCE_DATE_EPOCH`, locale/timezone and canonical environment allowlist.
- Canonicalize manifest serialization, path ordering, archive member ordering,
  timestamps, owner/group fields, permissions and compression parameters.
- Enumerate every packaged runtime/config/public-metadata-format object. Static
  ELF output must prove no `PT_INTERP` and no `DT_NEEDED`; a dynamic design
  must enumerate the loader and complete resolved shared-object closure.
- Run two clean, independent builds in separately created directories from the
  same accepted inputs. Binary, manifest and archive digests must match.
- Run controlled negative builds proving a source, lockfile, toolchain, linker,
  flag or generated-input change alters provenance or blocks the build.
- Manifest schema must reject any actor ID, key ID, raw key, signer,
  authorization verdict, PASS, eligibility or publication field.

### Acceptance

- [ ] Complete source/Cargo/toolchain/linker/runtime closure is explicit and
      content-bound.
- [ ] Build is offline, dirty-tree refusing and independent of ambient user
      configuration.
- [ ] Two clean independent builds emit byte-identical binary, manifest and
      archive digests.
- [ ] Static/dynamic closure assertions are machine checked.
- [ ] Negative provenance mutation cases fail closed.
- [ ] Schema tests prove that actor/key/signer/verdict metadata is impossible.
- [ ] Exact-commit Gray, Harper and `/codex-review` dispositions are PASS.

### Non-goals

No process launch, seccomp installation, capability FD, transaction state,
production trust binding, release verdict or real-Linux PASS is produced.

## 5. NF2 — native kernel isolation broker

### Scope

NF2 launches only the accepted NF1 runtime and proves a live native isolation
boundary. It transfers either no descriptor or a narrowly typed,
public-only/non-secret capability. A generic untyped FD is forbidden.

### Required design

- Define the exact supported audit architecture and syscall ABI. Seccomp rules
  are generated or enumerated for that architecture, default-deny, and tested
  against the compiled program's actual syscall needs.
- Install `no_new_privs` and the accepted seccomp policy before READY. A policy
  mismatch, unsupported architecture or inability to read back kernel state
  blocks.
- Use `close_range` (or a proven equivalent loop with bounded verified limits)
  to close every descriptor except a fixed explicit allowlist before exec.
  Enumerate `/proc/self/fd` before READY and block extras, aliases or
  substitutions.
- Resolve package objects with `openat2` beneath a pinned root using no-symlink
  and no-magic-link constraints. Retain verified descriptors; do not reopen
  verified paths.
- Bind child identity with pidfd, start time, executable device/inode and
  `SO_PEERCRED`. Numeric PID or diagnostic path text is never authoritative.
- Compare file-backed mappings through kernel `map_files` descriptors against
  NF1-pinned device/inode/digest identities. Missing access is BLOCK.
- Reverify package root, manifest, executable, peer, process identity, mapped
  closure and all allowed descriptors immediately before any capability
  boundary and again after the child response.
- Prefer no FD transfer. If an FD is necessary, its protocol type, rights,
  seekability, content schema, public/non-secret classification and lifetime
  are fixed and validated on both sides. It cannot represent a signer,
  private key or authority.
- Timeout, peer exit, protocol error or broker exit closes all descriptors and
  kills/reaps the child without a success outcome.

### Acceptance

- [ ] Exact architecture and seccomp policy are versioned and default-deny.
- [ ] `close_range` establishes total inherited-FD closure.
- [ ] pidfd, peer, executable and `map_files` checks are live and fail closed.
- [ ] Every boundary has before/after root, manifest, FD and process rechecks.
- [ ] Capability is absent or typed public-only/non-secret; substitution and
      rights escalation tests block.
- [ ] Path/link, FD, PID, exec, peer, mapping, timeout and cleanup adversarial
      tests pass on the supported Linux kernel test target.
- [ ] Exact-commit Gray, Harper and `/codex-review` dispositions are PASS.

### Non-goals

NF2 does not persist a success transaction, resist broker restart replay,
provide production signing access or emit release evidence. Kernel tests may
validate mechanisms, but they do not close NF3.

## 6. NF3 — durable one-shot transaction and real-Linux adversarial E2E

### Scope

NF3 makes the NF2 protocol one-shot across process death and restart, and
executes the complete NF1→NF2 flow on a real non-container Linux host. Its
result remains foundation evidence, not release PASS.

### Durable state model

- Define canonical states such as `CREATED`, `READY_BOUND`,
  `CAPABILITY_ISSUED`, `DERIVED_PENDING_RECHECK`, `COMMITTED`, `ABORTED` and
  `EXPIRED`, with legal transitions and terminal-state rules.
- Bind every record to a high-entropy transaction ID, NF1 package/manifest
  digest, runtime identity, peer identity, request digest and monotonic
  deadline/boot identity where required.
- Persist and fsync the state transition before an irreversible protocol
  action. Directory metadata and atomic replacement/append semantics must be
  covered; torn writes fail closed.
- On startup, reconcile all non-terminal transactions. Capability-issued or
  ambiguous states become non-reusable terminal failure unless the protocol
  can prove an exact safe continuation.
- Duplicate READY/DERIVED, wrong transaction, stale deadline, replay after
  COMMIT/ABORT, copied state, rollback and concurrent duplicate requests block.
- Cleanup must close descriptors, terminate/reap children and retain enough
  immutable audit state to prevent replay without retaining a capability.

### Real-Linux E2E

- Use a dedicated, non-container, supported-architecture Linux host. Record
  kernel, boot, filesystem, package and exact merge/build digests.
- Build NF1 twice from clean independent inputs, install the exact accepted
  package read-only, then execute NF2 through NF3 durable state.
- Positive flow proves one complete generic public-only transaction.
- Negative flow covers source/toolchain/linker mutation, package/path/hardlink
  swap, missing/extra closure, loader injection, FD substitution, PID reuse,
  exec replacement, peer mismatch, seccomp/architecture mismatch, replay,
  concurrent duplicate, timeout, broker kill at every transition, torn state,
  restart reconciliation and cleanup.
- The harness refuses Darwin and containers and returns
  `BLOCKED_EXTERNAL_LINUX` when required host/kernel evidence is unavailable.

### Acceptance

- [ ] State transitions are durable before irreversible actions.
- [ ] Crash/restart and torn-write reconciliation cannot reuse a capability or
      produce duplicate COMMIT.
- [ ] Replay/concurrency/deadline tests fail closed.
- [ ] Full positive and adversarial suite runs on real non-container Linux
      against exact accepted NF1/NF2 merge SHAs.
- [ ] Evidence records exact host/kernel/build/package/test identities without
      secrets or authority metadata.
- [ ] Result is explicitly labelled foundation verification only: no release
      PASS, eligibility, publication or production signer claim.
- [ ] Exact-commit Gray, Harper and `/codex-review` dispositions are PASS.

## 7. P0/P1 sequencing and stop conditions

1. **P0/NF1** closes the build/provenance boundary first.
2. **P0/NF2** starts only after NF1 merges and its merged-commit gate is green.
3. **P1/NF3** starts only after NF1 and NF2 merge and both merged-commit gates
   are green.
4. Any missing source/toolchain/linker input returns NF1 to BLOCK.
5. Any ambiguous kernel/FD/process boundary returns NF2 to BLOCK.
6. Any non-durable transition or unavailable real Linux returns NF3 to BLOCK
   or `BLOCKED_EXTERNAL_LINUX`.
7. #1076 and #1057 remain OPEN until their separately approved downstream
   acceptance is satisfied.

## 8. Review and full-gate protocol

For each child, in this order:

1. run focused unit, integration, negative, format, lint and
   `git diff --check` checks;
2. freeze one exact commit;
3. obtain Gray, Harper CISO and independent `/codex-review` adversarial PASS
   on that exact commit;
4. fix every finding in a new commit and restart all exact-commit reviews;
5. only after all three reviews PASS, run the repository-local
   `.githooks/pre-push` in full;
6. push and open a linked PR with a named reviewer and commit-bound evidence;
7. merge normally to `develop`, then rerun the full pre-push gate at the
   merged commit.

No full-gate result from the void prototype is reusable. NF3's real-Linux
suite is additional to, not a replacement for, the repository full gate.
Eye is N/A unless a child introduces operator-visible UI.

## 9. Honest disposition

- `PASS` for a child means only that child's exact acceptance is satisfied.
- `BLOCK` means an invariant failed or cannot be verified.
- `BLOCKED_EXTERNAL_LINUX` means the required real host/kernel state is
  unavailable.

Even NF1+NF2+NF3 PASS does **not** confer production trust binding, a signer,
evidence eligibility, release PASS or publication authority. Those require
later approved issues and their own evidence.

This plan authorizes no implementation, issue creation, push, merge,
production action or release claim before CEO approval.

## 10. CEO approval gate

**APPROVED on 2026-07-30 after exact-plan review.**

CEO explicitly approves:

1. the NF1/NF2/NF3 split and each 12-hour hard limit;
2. NF1 and NF2 as P0, NF3 as P1;
3. the exact dependency sequence and merged-commit gates;
4. the authority-neutral boundary and prohibition on actor/key metadata;
5. typed public-only/non-secret capability or no FD transfer;
6. durable crash/replay state and real non-container Linux as NF3 acceptance;
7. no release PASS from any child;
8. `ec4188d6...` and all related prototype evidence remaining void and
   unpushed.

Approval authorizes creation of the three scoped child issues and execution in
the dependency order above. It does not approve implementation code, waive any
child review/gate, authorize a release PASS, or close #1076/#1057.
