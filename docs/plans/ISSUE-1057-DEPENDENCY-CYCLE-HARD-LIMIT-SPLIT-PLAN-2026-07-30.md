# Issue #1057 — dependency-cycle and hard-limit split plan

- Owner: gray (CPO)
- Parent: #1051
- Baseline: `origin/develop@2ca910ae9d0244790bb844d0fd49f57f24a2acdb`
- Date: 2026-07-30
- Status: **CEO REVIEW REQUIRED — implementation forbidden**
- Current disposition: **#1057 OPEN / BLOCK**
- Deliverables: two child issues, each no more than 12 hours

## 1. Gray stop decision

#1057 cannot truthfully satisfy its current production-key acceptance from the
accepted code on `develop`.

Merged #1064 is intentionally an inert, pure v4 structure contract.
`evidence_action_intent.py` explicitly states that it does not verify
production trust anchors, consume nonces, publish evidence or confer release
authority. Its envelopes bind actor and key IDs, but raw public-key bytes come
from a later OS trust anchor. Treating a package-builder argument, caller
payload or locally generated manifest as that trust anchor would recreate the
authority injection that #1048 and #1051 are meant to eliminate.

The current issue graph also contains two cycles:

1. #1060 says it depends on accepted #1048, while #1065 (the remaining #1048
   integration) depends on #1060.
2. #1065 says it depends on frozen #1052 and that only #1052 may publish,
   while #1052 depends on completed #1048/#1065.

Implementation stops before either cycle is hidden with a fixture or fake
authority. Split #1057 at the authority boundary:

| Work | Estimate | Dependencies | Deliverable |
|---|---:|---|---|
| NR1a — native immutable runtime/package and generic broker primitive | 10–12h | accepted #1064 schema; coordinate merged #1050 | Native pre-runtime closure and kernel broker state machine with no production-key authority or PASS claim |
| NR1b — production trust binding and installed Linux validation | 10–12h | accepted NR1a and #1060 | Bind fixed OS trust anchors, raw key identities and the real signer capability; emit an authenticated one-shot broker receipt for #1065/#1052 |

The uncommitted `/private/tmp/trustforge-1057` prototype and the earlier
review-blocked #1051 Python helper are **void** as completion, review or release
evidence. Either may be reconsidered only as untrusted implementation input
after its child plan is approved.

## 2. Corrected acyclic dependency graph

The child issues and affected downstream issues must use this graph:

```text
#1064 pure v4 schema --------------------+
                                          +--> #1060 OS trust service
NR1a native generic package --------------+

#1064 + #1060 + accepted NR1a ------------> NR1b production native binding

#1064 + #1060 + NR1b + #1050 ------------> #1065 authorization verdict

#1065 + #1050 + accepted verifier/
provenance/ledger dependencies -----------> #1052 sole publication integration
```

Required issue dependency corrections:

1. #1060 depends on accepted #1064 schema, not completed parent #1048.
   #1060 establishes the fixed OS trust anchor needed to complete #1048.
2. #1065 depends on accepted #1064, #1060, NR1b and #1050. It produces the
   current dual-authorization/nonce decision and authenticated broker intent;
   it does not publish eligible evidence.
3. #1052 depends on accepted #1065 and remains the sole integration owner that
   may call #1050 to make evidence eligible.
4. A frozen data contract may be reviewed before implementation, but “frozen
   #1052” must not mean that #1065 imports an unfinished runtime or that #1052
   waits on itself.

No issue may be marked unblocked until the exact dependency merge SHA and fresh
post-merge gate are recorded.

## 3. Shared non-negotiable boundary

Neither child may use any of the following as release evidence:

- Darwin execution, a container-only simulation or a checked-in fixture;
- Python or another interpreted runtime starting before the native verifier;
- caller-selected actor, key ID, raw public key, signer key, path, FD, digest,
  PID, UID, verdict or trust anchor;
- a package manifest generated from untrusted request fields;
- a green parser/unit test standing in for installed real-Linux execution;
- a generic broker test descriptor standing in for a production signing key;
- a signed description that is not compared with current kernel and pinned
  byte state.

Unavailable real Linux returns `BLOCKED_EXTERNAL_LINUX`. Missing authority,
closure, identity or transaction inputs return `BLOCK`. Neither state may be
translated to PASS.

## 4. NR1a — native immutable runtime/package and generic broker primitive

### Scope

Build the authority-neutral native foundation. It must run before CPython or
any application runtime, pin a complete static or explicitly enumerated
runtime closure and prove the generic one-shot descriptor protocol with a
non-secret test capability. It does not decide who is authorized, load a
production signer or release eligible evidence.

### Package contract

- A versioned canonical manifest enumerates the native bootstrap, application
  runtime, embedded stdlib/application/extensions when used, ELF
  loader/shared libraries when present, fixed config, public-key metadata
  format and build provenance.
- Every entry binds role, canonical package-relative path, file type, root
  owner, mode, link count, size, fixed FD and SHA-256.
- A statically linked runtime must prove absence of `PT_INTERP` and
  `DT_NEEDED`; static PIE relocation metadata is allowed only when it names no
  shared dependency.
- A dynamic runtime must enumerate and pin the loader and every effective
  shared object. Ambient lookup, `LD_PRELOAD`, `LD_LIBRARY_PATH`,
  `PYTHONPATH`, user/site packages, symlinks, devices and writable directories
  block.
- The reproducible builder uses a locked toolchain, fixed epoch, deterministic
  ordering and normalized archive metadata. Two clean builds from identical
  source emit byte-identical bootstrap, manifest and archive digests.

### Kernel and broker primitive

- Native code uses `openat2` with no-follow/beneath-only resolution and retains
  the verified descriptors through `execveat`; no verified pathname is
  reopened.
- Initial inherited FDs are a fixed allowlist. Every child FD is assigned to a
  fixed number; unexpected or substituted descriptors block.
- Parent/child identity uses pidfd, `/proc` start time, executable
  device/inode and `SO_PEERCRED`, not a reusable numeric PID.
- The approved static runtime installs `no_new_privs` and an exec-denying
  seccomp policy before sending READY. The broker checks kernel state before
  releasing the generic capability.
- Loaded file-backed mappings are compared through kernel `map_files`
  descriptors to pinned runtime objects. Mapping path strings are diagnostic
  only.
- Root, manifest and every pinned FD are reverified immediately before the
  one-shot capability transfer and after derivation.
- The protocol is transaction-bound:
  READY/isolation → live recheck → one-shot generic FD → DERIVED → live and
  closure recheck → COMMIT permit.
- Replay, duplicate READY/DERIVED, wrong transaction, peer exit, broker crash
  or timeout closes the transaction without a success disposition.

### Acceptance

- [ ] Native code executes before the application runtime and has no mutable
      interpreted runtime dependency.
- [ ] Complete static/dynamic closure equals strict pinned manifest bytes.
- [ ] Fixed FD, openat2/execveat, pidfd, SO_PEERCRED, seccomp and map-files
      boundaries are implemented fail closed.
- [ ] Generic broker protocol is one-shot and transaction-bound.
- [ ] No production signer, production actor/key decision, PASS verdict or
      evidence eligibility exists in NR1a.
- [ ] Reproducible build emits byte-identical binary, manifest and archive
      digests.
- [ ] Real Linux adversarial tests cover path/hardlink swap, missing/extra
      closure, loader injection, FD substitution, PID reuse, exec replacement,
      peer mismatch, replay, crash and pre-READY access.
- [ ] Darwin/unavailable Linux reports `BLOCKED_EXTERNAL_LINUX`.

### Explicit non-goals

NR1a does not verify #1064 signatures, choose trust anchors, derive raw public
keys, open a production private key, publish evidence or satisfy #1057/#1051
closure by itself.

## 5. NR1b — production actor/key trust binding and installed Linux validation

### Scope

After NR1a and #1060 merge, bind the generic native broker to the fixed
production trust service and real signer capability. NR1b owns native signer
isolation and emits a narrowly scoped broker receipt; it does not make evidence
eligible.

### Trust and signer contract

- Only #1060's root-installed generation, trust-anchor descriptors and
  authenticated Unix-socket response provide actor/key identity and raw
  public-key bytes.
- The CEO and operator actor IDs, key IDs, nonces and raw public-key bytes must
  all differ as required by #1048. Alias IDs over identical raw keys block.
- The verified #1064 action/intent digest, complete evidence scope, service
  generation, transaction ID, package digest and intended evidence key ID bind
  one broker transaction.
- The signer capability is installed/provisioned outside the request path. Its
  fixed descriptor is checked against root-owned key metadata and the intended
  key identity. Key pathname and private bytes never appear in argv,
  environment, request, response, log or archive.
- Ingress/router/request payloads cannot select a trust root, actor, public
  key, signer key ID, signer FD or policy.

### Installed real-Linux validation

- Install the exact NR1a package and #1060 generation on a non-container real
  Linux host.
- Verify systemd UID/GID, socket ACL, read-only package/trust paths, descriptor
  allowlist, `NoNewPrivileges`, seccomp and restart behavior.
- Run the approved runtime through the native broker with real #1060 trust
  responses and an isolated test signing generation.
- Reobserve pidfd, start time, executable, map closure, root/manifest/pinned
  FDs, peer credentials and service generation before signer release and after
  derivation.
- Emit a versioned authenticated broker receipt binding transaction, #1064
  intent, CEO/operator actor/key IDs and raw-key digests, #1060 generation,
  package/manifest/runtime digests, signer key ID, child identity and both
  boundary observations.
- The receipt confers no publication authority. #1065 consumes it while
  deriving authorization/nonce state; #1052 later owns #1050 eligibility.

### Acceptance

- [ ] Exact accepted NR1a and #1060 merge SHAs and installed digests are
      recorded.
- [ ] Production identities and raw keys come only from #1060.
- [ ] Wrong/duplicate raw keys, aliases, generation drift, stale intent,
      caller-selected authority and signer substitution block.
- [ ] No signer path/bytes leak and no pre-validation signer access occurs.
- [ ] Real Linux positive transaction completes READY → key → DERIVED →
      recheck → COMMIT and emits the authenticated broker receipt.
- [ ] Path/FD/PID/exec/service-generation races, replay, crash and restart fail
      closed without a reusable signer capability.
- [ ] #1065 and #1052 can consume the receipt without importing a private
      alternate broker or weakening #1050.

## 6. Adversarial matrix

| Class | NR1a | NR1b |
|---|---|---|
| symlink/hardlink/path swap | BLOCK | BLOCK |
| omitted/extra stdlib, app, extension, loader or library | BLOCK | BLOCK |
| `LD_PRELOAD`/`PYTHONPATH`/user-site injection | BLOCK | BLOCK |
| unexpected/substituted inherited FD | BLOCK | BLOCK |
| PID reuse/child substitution/post-verify exec | BLOCK | BLOCK |
| wrong peer UID/GID or socket | BLOCK | BLOCK |
| pre-READY capability request | BLOCK | BLOCK |
| replay/duplicate/wrong transaction | BLOCK | BLOCK |
| CEO/operator same actor, key ID or raw public key | not authority-bearing | BLOCK |
| caller-selected trust anchor/key/signer | not supported | BLOCK |
| #1060 generation drift/loss | not applicable | BLOCK |
| broker/runtime crash before post-derive recheck | no COMMIT | no receipt |
| Darwin/container/fixture-only run | `BLOCKED_EXTERNAL_LINUX` | `BLOCKED_EXTERNAL_LINUX` |

## 7. Time and review gates

For each child:

1. gray reviews remaining scope at hour 6;
2. implementation stops and splits again before hour 12;
3. focused native/build/adversarial tests, lint/format and `git diff --check`
   run before review;
4. gray, Harper CISO and independent `/codex-review` must all PASS on the exact
   commit;
5. only then may the exact repository `.githooks/pre-push` run;
6. push and PR require commit-bound evidence and a named reviewer;
7. merge normally into `develop`, then run a fresh merged-commit pre-push gate.

Eye is N/A because neither child changes operator-visible UI.

## 8. Closure and honest disposition

#1057 remains OPEN/BLOCK after NR1a. It may close only after NR1b merges and its
real installed-Linux positive/adversarial evidence passes every gate.

- `PASS`: NR1a closure plus NR1b OS-trusted raw-key/signer binding pass on real
  installed Linux.
- `BLOCK`: any manifest, authority, identity, signer or transaction check
  fails.
- `BLOCKED_EXTERNAL_LINUX`: required kernel/service state is unavailable or
  unverifiable.

#1051, #1048 and #1052 remain open until their own downstream acceptance is
satisfied. This plan authorizes no issue creation, implementation, push,
merge, evidence publication, promotion or deployment before CEO approval.

## 9. CEO approval gate

**PENDING.**

CEO must explicitly approve:

1. the NR1a/NR1b split and both 10–12-hour hard limits;
2. the corrected acyclic dependency graph and issue dependency edits;
3. NR1a being authority-neutral and unable to access a production signer;
4. NR1b producing a broker receipt rather than eligible evidence;
5. #1065 consuming NR1b without depending on #1052 runtime;
6. #1052 remaining the sole #1050 publication/eligibility integration owner;
7. all prior prototype code remaining void and unpushed;
8. real Linux absence remaining a non-overridable block.
