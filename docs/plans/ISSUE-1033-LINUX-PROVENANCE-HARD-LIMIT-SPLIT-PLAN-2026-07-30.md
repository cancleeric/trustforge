# Issue #1033 — Linux provenance hard-limit split plan

- Owner: gray (CPO)
- Parent plan: `ISSUE-1019-REAL-INGRESS-EVIDENCE-REMEDIATION-SPLIT-PLAN-2026-07-30.md`
- Parent issue: #1019
- Date: 2026-07-30
- Baseline: `origin/develop@1976946b0243309329a93f924f163c1f113478ac`
- Status: **CEO REVIEW REQUIRED — implementation is not authorized**
- Current disposition: **#1033 OPEN / BLOCK**

## 1. Hard-limit decision

#1033 combines three independently security-sensitive trust boundaries and
cannot honestly fit its original 6–8-hour estimate. It is split before any
partial implementation is treated as accepted evidence.

The local, unpushed process-provenance experiment
`b0b5ca3e76938b66f1be8fcb7a3651f90403ca89` is **void as completion and
release evidence**. It may be reconsidered only as untrusted implementation
input for LP1 after this plan and its child issue are approved.

| Item | Estimate | Dependencies | Outcome |
|---|---:|---|---|
| LP1 — six-role kernel process provenance core | 6–8h | shared role/digest domains frozen with #1051/#1058 | Live pidfd/starttime/credentials/executable/runtime/artifact verification and signed-claim equality |
| LP2 — nginx source/auth and AF_UNIX provenance | 10–12h | accepted LP1; coordinates with #1021 and #1058 | Descriptor-pinned nginx `-T`, exact effective auth/source boundaries, socket inode and peer credentials |
| LP3 — ledger cross-binding and external Linux gate | 10–12h | accepted LP1 + LP2 + #1048 + #1049 + #1050 + #1052 + #1058 + #1060 | Four-ledger authenticity/cross-binding plus the only real-host integrated verdict |

LP2 may prepare fixtures while LP1 is reviewed, but it must not claim
acceptance or merge against a private duplicate contract. LP3 starts only
after all dependencies are accepted and merged to `develop`.

Every item has a Gray hour-6 review and stops before 12 hours. Any remainder is
split again; no simulated result or caller assertion fills an acceptance gap.

## 2. Shared invariants

1. The six exact roles are `evidence`, `ingress`, `nginx`, `router`,
   `release_a`, and `release_b`. Each has a distinct live process identity.
2. Trusted executable, runtime, artifact, configuration and ledger inputs are
   opened by a privileged authority with no-follow descriptors. Caller paths,
   `PATH`, digests, PIDs, credentials and summaries are not authority.
3. Kernel-observed PID/starttime/UID/GID/executable/socket state is compared
   with signed claims; a valid signature over false live state still BLOCKS.
4. Every digest uses one versioned domain and equals bytes read from a pinned
   descriptor. Arbitrary digest-shaped strings do not satisfy provenance.
5. Race, process restart, descriptor/path swap, missing input, ambiguous nginx
   scope, wrong peer, broken ledger binding or partial result BLOCKS.
6. Canonical result bytes and their digest are deterministic. A blocked result
   contains no partial role set that downstream code could mistake for PASS.
7. Darwin, fixtures, Docker/container simulation and absent exact topology
   return `BLOCKED_EXTERNAL_LINUX`; no override parameter may promote them.
8. This split does not promote B, deploy, modify thresholds, close #1019, or
   authorize production evidence.

## 3. LP1 — six-role kernel process provenance core

### Scope

Implement the shared typed contract for the six live roles. It verifies
kernel identity and immutable bytes only; it does not claim nginx, socket or
ledger topology.

### Acceptance

- Require exactly the six roles in canonical order and six distinct live PIDs.
- Pin each PID with Linux `pidfd_open`; read `/proc/<pid>/stat` twice and
  require an unchanged starttime across verification.
- Observe real/effective UID and GID from `/proc`; compare them with the
  trusted role policy, not caller payload fields.
- Compare `/proc/<pid>/exe` device/inode with the already pinned executable
  descriptor.
- Hash executable, complete runtime package and release artifact bytes from
  pinned regular, single-link, non-writable descriptors; verify metadata did
  not race.
- Verify domain-separated Ed25519 claims with public keys only and require the
  complete signed claim to equal the independently observed live record.
- Reject missing/extra roles, duplicate PID, invalid key/signature, stale
  starttime, changed descriptor metadata, executable mismatch and malformed
  `/proc`.
- Emit canonical versioned bytes/digest and validate them before downstream
  consumption.
- Non-Linux/container/fixture coverage yields only
  `BLOCKED_EXTERNAL_LINUX`; there is no public bypass flag.
- Focused Linux integration tests must use real child processes and pinned
  descriptors. macOS tests cover only BLOCK and canonical/adversarial logic.

### Non-goals

LP1 does not parse nginx, verify a socket, authenticate ledgers or produce a
release PASS. Those assertions belong to LP2 and LP3.

## 4. LP2 — nginx source/auth and AF_UNIX provenance

### Scope

Prove that traffic reaches the intended authenticated canary location through
the exact nginx executable and AF_UNIX router socket.

### Acceptance

- Locate nginx from root-owned trusted configuration, never caller `PATH`.
- Open the root-owned, regular, single-link, non-group/world-writable
  executable by no-follow descriptor and invoke exact `/proc/self/fd/<fd> -T`.
- Parse nginx `-T` source markers and descriptor-open every root config,
  include, allowlist and source file used in the conclusion.
- Resolve effective directives at the exact canary location. Sibling/nested
  `auth_basic`, `auth_request`, header clearing or injection cannot satisfy it.
- Require effective authentication, rejection of empty identity, clearing of
  all client/legacy identity headers, trusted identity injection and the exact
  AF_UNIX upstream.
- Verify the router socket is the pinned socket inode, has expected type,
  owner/group/mode and cannot be substituted with a symlink or regular file.
- Verify Linux `SO_PEERCRED` PID/UID/GID at both nginx→router and controlled
  diagnostic boundaries. Wrong UID/direct clients remain A-only.
- Bind the observed nginx and router processes to LP1 records without accepting
  duplicate caller observations.
- Attack fake `PATH`, wrapper/alias nginx, changed executable/config/include,
  hardlink/symlink, source omission, sibling auth, `auth_* off`, wrong socket
  owner/mode/inode and wrong peer credentials.
- Any unavailable exact topology returns `BLOCKED_EXTERNAL_LINUX`.

## 5. LP3 — four-ledger cross-binding and external Linux gate

### Scope

Authenticate routing, control, budget and outcome ledgers; bind them to LP1,
LP2 and the accepted evidence authority; then execute the only eligible
external Linux integration gate.

### Acceptance

- Descriptor-open each ledger root/head/event stream and verification keyring;
  require root-owned safe metadata and stable bytes.
- Verify each ledger schema, identity, signature domain/key authorization,
  sequence, previous hash and canonical head.
- Require one run/release/ramp/policy/control identity across all four ledgers,
  the typed ingress transcript and #997/#1048 authorization.
- Verify reservation→result→reconciliation conservation, cross-ledger causal
  references, autostop ordering, rollback-to-A and zero post-barrier B.
- Bind ledger heads, LP1 digest, LP2 digest, transcript digest, artifact/git
  digests and intended evidence key into one canonical provenance result.
- Reject deletion, truncation, duplicate/reordered record, cross-run splice,
  stale head, wrong key/domain, changed control, missing reconciliation and
  partial ledger availability.
- Run on a non-container Linux release host with the immutable verifier and
  evidence services delivered by accepted #1058/#1060; no in-process fixture
  signer or caller-owned trust root is permitted.
- Reobserve all six processes, nginx sources, socket peers and ledger heads
  immediately before finalization; any race BLOCKS.
- Only the complete real-host run may return PASS. All skipped, synthetic,
  fixture, Darwin, container or unavailable runs return
  `BLOCKED_EXTERNAL_LINUX`.

## 6. Dependency graph

```text
shared domains (#1051/#1058)
              |
              v
          LP1 kernel core
              |
              v
       LP2 nginx + AF_UNIX
              |
              +-------------------------------+
                                              |
#1048 + #1049 + #1050 + #1052 + #1058 + #1060
                                              |
                                              v
                              LP3 ledger + real Linux gate
                                              |
                                              v
                                   #1033 / #1019 disposition
```

LP3 must consume merged public contracts. It may not copy an unmerged branch
or privately redefine a dependency to make the gate green.

## 7. Review and release evidence

Each LP issue requires:

- a scoped branch/worktree and linked issue with exact acceptance criteria;
- focused unit, Linux integration and adversarial tests as applicable;
- Gray acceptance/truthfulness review at hour 6 and before push;
- Harper CISO review;
- independent `/codex-review` adversarial review;
- exact-commit repository-local `.githooks/pre-push` PASS;
- reviewer attestation and `Eye N/A` rationale (no UI change);
- normal merge to `develop`, followed by a fresh post-merge full gate.

No author self-approval, admin override, synthetic release receipt, skipped
test recast as PASS, or backdated evidence is permitted.

## 8. Honest disposition

- `PASS`: LP1–LP3 and all dependencies are accepted, and LP3 passes on the
  exact external Linux release topology.
- `FAIL`: any acceptance, adversarial, signature, race or causal check fails.
- `BLOCKED_EXTERNAL_LINUX`: the exact non-container Linux topology, service
  identities, authority or durable ledgers are unavailable or unverifiable.

Until LP3 produces authentic PASS, #1033 remains OPEN/BLOCK and continues to
block #1019. Green macOS tests prove portable fail-closed behavior only.

## 9. CEO approval gate

CEO must explicitly approve:

1. the LP1/LP2/LP3 split and estimates;
2. the dependency order and prohibition on private duplicate contracts;
3. the void status of local experiment
   `b0b5ca3e76938b66f1be8fcb7a3651f90403ca89`;
4. external Linux as a non-overridable release condition;
5. all security review and exact-commit gates;
6. #1033 remaining OPEN/BLOCK until authentic LP3 PASS.

This document does not authorize implementation, push, merge, issue closure or
release activity.
