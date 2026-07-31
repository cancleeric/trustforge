# Issue #1076 — NR1a capability protocol and generic foundation plan

- Owner: gray (CPO) — plan author; CEO — reviewer/approver
- Parent issue: #1076 (OPEN/BLOCK); grandparent #1057
- Baseline: `origin/develop@a2aff752` (worktree trustforge-nr1a)
- Date: 2026-07-31
- Status: **CEO APPROVED — execution authorized for NR1a-A first**
- Prerequisite done: NF1(#1087)/NF2(#1088)/NF3(#1089) CLOSED; their union does NOT fully satisfy #1076 acceptance (gaps below)

## 0. CEO approval and red lines

CEO approves: static-only v1; split into NR1a-A (10h) + NR1a-B (8h); CapabilitySink + ledger 7-state + forced-tombstone reconcile; SO_PEERCRED 4-way cross-check; non-secret descriptor; triple review on security PRs.

**Red lines CTO must obey (CEO-added, gray under-emphasized):**
1. Generic manifest `AcceptedPins` MUST be a compile-time `const` (`NF1_PINS`). Never a runtime/caller parameter — otherwise authority injection returns.
2. SO_PEERCRED socketpair (peer-identity channel) and `CapabilitySink` (in-process lifecycle hook) are TWO independent mechanisms. Do not merge them.
3. W9 workspace refactor MUST keep `--target x86_64-unknown-linux-musl` cross-compile green (verified loop: `cargo check --target x86_64-unknown-linux-musl` exits 0 on Darwin).
4. `CapabilitySink` calls must not cross threads (`ClaimSession` is `!Send`, holds the ledger store lock).

**Non-goal declaration (must be appended to #1076 issue body):** NR1a v1 is static-musl-only. Dynamic closure pinning (loader / shared-object enumeration) is deferred to a separate downstream issue (suggest NR1a-dyn) and does not block #1076 closure judgment.

## 1. GAP confirmation (source-verified)

| GAP | Source evidence | #1076 acceptance |
|---|---|---|
| GAP-1 capability protocol absent | `nf3/.../ledger.rs:17-22` State only Prepared/Claimed/Committed/Tombstoned; `nf2/.../process.rs:155-160` linear TraceStage; `nf3/.../integration.rs:65-104` capability degraded to executor() closure | #6 (core) |
| GAP-2 SO_PEERCRED zero hits | grep across native/ = 0; live.rs uses proc stat + starttime + exe dev/inode + map_files only | #5 (core) |
| GAP-3 manifest.rs NF1-bound | `manifest.rs:6-13` compile-time ACCEPTED_* consts; `:49-58` entries.len()==5 fixed; `:246-289` builder_runtime Darwin fields hardcoded generic | #2 |
| GAP-4 dup code, no workspace | sha256.rs x2; openat2/OpenHow x3; getdents64 x2; no root workspace | hygiene + #3 |
| GAP-5 NF2/NF3 no reproducible builder | only hermetic-package/ has .cargo/config + rust-toolchain.toml | #3 |

## 2. Scope decisions

- **static-only**: agreed (#2 is "static OR dynamic"; NF1/NF2 static musl). Generic manifest `runtime_closure` covers static branch only; dynamic branch enum-reserved, unvalidated.
- **capability protocol minimalized**: model capability stages as durable ledger states (extend existing 4-state to 7); descriptor is a non-secret fixed struct passed via in-process `CapabilitySink` trait (no socketpair transport). Reuse NF3 fsync/marker/poison. Keep NF2 ptrace barrier as child-ready sync; insert lifecycle hooks at existing reverify checkpoints.
- **reconcile forced-tombstone (non-negotiable)**: on restart, any tx stopped at ReadyBound/CapabilityIssued/DerivedPendingRecheck → append Tombstoned. NR1a v1 does NOT implement "exact safe continuation" proof, so capability may already be out → never reusable.
- **12h hard limit**: pre-split into NR1a-A + NR1a-B rather than stuffing 17h into one issue.

## 3. Work breakdown + child-issue split

| Block | GAP | Content | Type | Est |
|---|---|---|---|---:|
| W1 | GAP-1 | ledger State 4→7 + reconcile (mid-state forced tombstone) + legal transition matrix | generalize | 2h |
| W2 | GAP-1 | nf2 CapabilitySink trait + run_transactional; insert 4 lifecycle hooks at existing reverify points in process.rs | generalize+new | 2.5h |
| W3 | GAP-1 | capability descriptor struct + non-secret validation + nf2/src/capability.rs | new | 1.5h |
| W4 | GAP-2 | live.rs PeerCredential capture/reverify (socketpair + getsockopt SO_PEERCRED) | new | 2h |
| W5 | GAP-2 | SO_PEERCRED cross-check with pidfd/starttime/exe + peer-mismatch adversarial hook | new | 1h |
| W6 | GAP-1 | capability replay adversarial (crash @ CapabilityIssued → reconcile tombstone) | new | 1h |
| W7 | GAP-3 | manifest.rs generic schema (de-hardcode pins, entries cardinality, builder_runtime optional) | generalize | 3h |
| W8 | GAP-3 | NF1 instance binding compat (generic schema + NF1_PINS const still validates accepted manifest) | generalize+verify | 1h |
| W9 | GAP-4 | root Cargo workspace + extract trustforge-native-sys shared crate | refactor | 2h |
| W10 | GAP-5 | nf2/nf3 crate-local .cargo/config.toml + rust-toolchain.toml + byte-identical double build | new | 2h |
| | | **total** | | **18h** |

| Child issue | Blocks | Est | Deps | Satisfies #1076 |
|---|---|---:|---|---|
| **NR1a-A** capability protocol + SO_PEERCRED | W1+W2+W3+W4+W5+W6 | 10h | accepted NF1/NF2/NF3 | #5,#6,#8(peer/replay) |
| **NR1a-B** generic foundation: manifest + dedup + builder | W7+W8+W9+W10 | 8h | accepted NR1a-A | #2,#3,#4 hygiene |

A first (critical path: #1076 closure), B after (B touches nf2/nf3 shared code, must land after A to avoid rebase conflicts).

## 4. NR1a-A design

### 4.1 ledger State extension (W1, `native/nf3-one-shot-transaction/src/ledger.rs`)
```rust
pub enum State {
    Prepared,               // CREATED
    ReadyBound,             // READY_BOUND
    CapabilityIssued,       // CAPABILITY_ISSUED
    DerivedPendingRecheck,  // DERIVED_PENDING_RECHECK
    Committed,              // COMMITTED
    Tombstoned,             // ABORTED/EXPIRED
}
```
Legal transitions:
```
Prepared → ReadyBound | Tombstoned
ReadyBound → CapabilityIssued | Tombstoned
CapabilityIssued → DerivedPendingRecheck | Tombstoned
DerivedPendingRecheck → Committed | Tombstoned
```
reconcile (`recover_locked`): any tx at ReadyBound/CapabilityIssued/DerivedPendingRecheck on restart → append Tombstoned.

### 4.2 nf2 CapabilitySink + run_transactional (W2, `native/nf2-zero-capability-broker/src/lib.rs` + `src/linux/process.rs`)
```rust
pub trait CapabilitySink {
    fn on_ready_bound(&self) -> Result<(), &'static str>;
    fn on_capability_issued(&self, desc: &CapabilityDescriptor) -> Result<(), &'static str>;
    fn on_derived_pending_recheck(&self) -> Result<(), &'static str>;
    fn on_committed(&self) -> Result<(), &'static str>;
}
pub fn run() -> Result<Outcome, &'static str> { run_transactional(&NoopSink) }
pub fn run_transactional<S: CapabilitySink>(sink: &S) -> Result<Outcome, &'static str> { ... }
```
Hook insertion points in `process.rs::run()` (at existing reverify checkpoints): after sealed.reverify (ReadyBound), after authority.reverify pre-release (CapabilityIssued), after derived produced pre-exit (DerivedPendingRecheck), after clean reap (Committed).

### 4.3 capability descriptor (W3, new `native/nf2-zero-capability-broker/src/capability.rs`)
```rust
pub enum CapabilityKind { ZeroFd }
pub struct CapabilityDescriptor {
    pub transaction_id: [u8; 32],
    pub foundation_sha256: [u8; 32],
    pub runtime_device: u64, pub runtime_inode: u64,
    pub capability_kind: CapabilityKind,
    pub descriptor_sha256: [u8; 32],
}
```
non-secret: all fields are public kernel/package identity. No key/signer/actor/raw_key. Validated by reject_authority_metadata-equivalent rules.

### 4.4 nf3 ClaimSession implements CapabilitySink (`integration.rs`)
`append_state(State::X)` via store.append under lock. `IntegratedRunner::execute` switches executor() to call nf2 `run_transactional(&session)`. Public entry signature stays authority-neutral.

### 4.5 SO_PEERCRED (W4+W5, `live.rs` + `process.rs`)
```rust
pub struct PeerCredential { pid: i32, uid: u32, gid: u32 }
// getsockopt(fd, SOL_SOCKET, SO_PEERCRED) ; socketpair(AF_UNIX, SOCK_STREAM) before fork
// cross-check: ucred.pid == child.pid AND pidfd same task AND ucred.uid==geteuid AND ucred.gid==getegid
```
adversarial: test_mode "peer-mismatch".

### 4.6 capability replay adversarial (W6)
claim → on_ready_bound + on_capability_issued → drop session (crash sim) → reopen → recover_locked tombstones → same tx_id replay rejected.

## 5. NR1a-B design (summary)
- W7+W8: manifest.rs generic schema; ACCEPTED_* → `AcceptedPins` const param (compile-time); entries cardinality free but strict per-entry schema; builder_runtime → NF1-instance optional; NF1_PINS const keeps accepted manifest valid.
- W9: `native/Cargo.toml` workspace + `native/trustforge-native-sys` (merge sha256/openat2/getdents64/close_range).
- W10: mirror hermetic-package .cargo/config + rust-toolchain.toml to nf2/nf3; double-clean-build byte-identical; verify_static_x86_64_elf.

## 6. capability protocol state graph (durable)
```
Prepared →(sealed.reverify ok) ReadyBound →(authority.reverify ok, release ZeroFd desc) CapabilityIssued
→(derived produced, reverify ok) DerivedPendingRecheck →(exit clean, final reverify) Committed
any stage: sink Err / peer exit / timeout / crash → Tombstoned
restart reconcile: mid-state → forced Tombstoned
```
recheck at each boundary: sealed.reverify + authority.reverify + SO_PEERCRED + pidfd ensure_live + FD allowlist.

## 7. PR strategy
| PR | Child | Scope | Reviewers |
|---|---|---|---|
| PR-A1 | NR1a-A | ledger 7-state + reconcile + CapabilitySink + run_transactional + descriptor + nf3 integration + replay test | gray + harper(CISO) + /codex-review |
| PR-A2 | NR1a-A | SO_PEERCRED capture/verify + cross-check + peer-mismatch adversarial | gray + harper(CISO) + /codex-review |
| PR-B1 | NR1a-B | generic manifest contract + NF1 instance compat | gray + harper + /codex-review |
| PR-B2 | NR1a-B | workspace + trustforge-native-sys dedup | gray + /codex-review |
| PR-B3 | NR1a-B | nf2/nf3 builder + byte-identical | gray + harper + /codex-review |

## 8. test strategy
- Darwin (local): manifest/capability parse, ledger State transition matrix, authority-alias reject, generic vs NF1-instance compat, host `cargo test --locked` (non-linux tests only).
- Darwin cross-compile: `cargo check/clippy --target x86_64-unknown-linux-musl` verifies linux.rs compiles (verified exit 0).
- Real Linux .83 (non-container): ptrace+ledger timing, SO_PEERCRED capture/verify, capability replay, peer-mismatch, map_files/seccomp/FD allowlist, full adversarial harness.
- BLOCKED_EXTERNAL_LINUX when .83 unreachable.

## 9. hour-6 review gates
- NR1a-A h6: if W1/W2 not landed green, or W4 not started → split SO_PEERCRED (W4+W5) into NR1a-A2; NR1a-A shrinks to W1+W2+W3+W6 (~7h).
- NR1a-B h6: if W7 not done or W10 not started → split builder (W10) into NR1a-B2.

## 10. honest disposition
- PASS: NR1a-A + NR1a-B all acceptance pass on real non-container Linux + triple exact-commit review + merged-commit gate green.
- BLOCK: mid-state capability reused (not tombstoned); SO_PEERCRED mismatch not rejected; manifest schema drift; builder not byte-identical; reconcile/burn/replay regression.
- BLOCKED_EXTERNAL_LINUX: .83 unreachable.
- NR1a-A+B PASS does NOT confer production signer/actor-key/PASS/eligibility/publication (NR1b scope). static-only is explicit non-goal (dynamic deferred).
