# Issue #1051 — native-runtime hard-limit remediation split

- Owner: gray (CPO)
- Parent: #1032
- Baseline: `origin/develop@77b461f179539c9701c7e4ab224dc952ee3de599`
- Date: 2026-07-30
- Status: **CEO REVIEW REQUIRED — implementation forbidden**
- Current disposition:
  **#1051 OPEN / `BLOCKED_BY_1033_AND_B1_B3_B5_NATIVE_RUNTIME`**
- Deliverables: two bounded child issues, each no more than 12 hours

## 1. Gray decision

The #1051 implementation does not pass its gray, Harper and `/codex-review`
gates. A Python-level launcher, a manifest that begins verification after the
interpreter starts, or a signed description of process state cannot establish
the required native trust boundary. The present work therefore receives no
release-completion credit.

Stop #1051 implementation and split the remaining work at the authority
boundary:

| Work | Estimate | Dependencies | Deliverable |
|---|---:|---|---|
| NR1 — native immutable bootstrap/package | 10–12h | accepted #1048 key-identity contract; coordinate with #1050 | Native pre-runtime verifier/broker that pins the complete runtime and releases only fixed descriptors |
| NR2 — post-merge authority/provenance integration | 8–12h | accepted NR1, #1033, #1048, #1049, #1050 and #1052 integration contract | Exact merged-commit Linux transaction proving live six-role provenance through the authoritative publication path |

NR2 must not start until every dependency is merged into `develop`, its exact
merge commit is recorded, and the integration contract in #1052 is frozen.
Neither work item may silently expand. At hour 6 gray reviews remaining scope;
at hour 10 the implementer stops and creates a further bounded issue if the
acceptance evidence cannot finish before hour 12.

## 2. Non-negotiable evidence boundary

The following are never release evidence:

- Darwin execution, a container-only simulation or a checked-in fixture;
- caller claims about PID, UID, route, runtime, digest, descriptor or key ID;
- a signed envelope whose facts were not compared with live kernel state;
- Python/stdlib imports that occurred before immutable-runtime verification;
- path reopen after verification, mutable `PYTHONPATH`, user/site packages or
  environment-selected loaders;
- self-authored review, synthetic PASS or a green unit test standing in for
  external Linux execution.

Unavailable live Linux state returns `BLOCKED_EXTERNAL_LINUX`. A partial,
stale, simulated or unverifiable result returns `BLOCK`; neither may be
translated to PASS.

## 3. NR1 — native immutable bootstrap/package

### Scope

Build a small native bootstrap and signer-descriptor broker that runs before
CPython or any other application runtime. It opens and verifies the entire
runtime closure, pins it through execution, validates the live child and only
then supplies the minimum already-open descriptors needed by the authority.
Prefer a smaller statically linked runtime when it materially reduces the
closure; static linkage does not waive verification of the bootstrap bytes,
configuration or loaded kernel-visible image.

### Required package closure

The versioned manifest must enumerate and domain-separate at least:

- native bootstrap and broker;
- CPython executable when used;
- stdlib archive/tree and every imported application module;
- native extension modules;
- ELF interpreter/dynamic loader and every shared library in the effective
  dependency graph;
- fixed configuration, keyring public metadata and publisher entry point.

Every entry binds canonical package-relative identity, file type, owner, mode,
link count, size and cryptographic digest. No glob, ambient search path,
symlink, device, FIFO, writable directory or unlisted dependency is accepted.

### Descriptor and identity contract

- Open from a root-owned package-directory descriptor with no-follow,
  beneath-only semantics; validate each opened object before use.
- Hold verified file descriptors across the exec boundary. Do not verify a
  pathname and later reopen it.
- Pass a fixed numeric FD allowlist; close every other inherited descriptor.
- The signer broker never exposes a key pathname, private-key bytes, arbitrary
  signing oracle, inherited environment secret or payload-selected descriptor.
- Broker authorization binds the exact #1048 actor/key identity and raw public
  key bytes. Aliases, duplicate public-key bytes under different IDs, or a
  caller-supplied key ID fail closed.
- Bind parent/child using pidfd (or an equivalently race-free Linux primitive),
  not a reusable numeric PID. Confirm process ownership, executable identity
  and liveness immediately before descriptor release and after derivation.

### Runtime isolation

- If CPython remains, invoke isolated/no-site operation (`-I -S`) with an
  explicit fixed module root and sanitized allowlisted environment.
- Pin and verify CPython, stdlib, extensions, loader and shared libraries
  before CPython starts. Post-start inspection alone is insufficient.
- Validate the live `/proc/<pid>/exe`, loaded-object map and effective runtime
  closure against the pinned descriptors/manifest. Any extra or missing
  executable object blocks.
- Make the broker's one-shot descriptor release inseparable from the validated
  package, exact authority transaction ID and exact child identity.

### Acceptance

- [ ] Native code executes before the application runtime and has no mutable
      interpreted dependency.
- [ ] The complete effective runtime closure is represented by strict manifest
      entries and equals pinned bytes.
- [ ] Fixed FD allowlist, close-on-exec policy and broker protocol are tested
      and documented.
- [ ] pidfd/live identity checks prevent PID reuse, child substitution and
      post-verify exec swap.
- [ ] #1048 actor/key identity and distinct raw public-key-byte rules govern
      broker release.
- [ ] Signer material is never available by path, argv, environment, response
      payload or unrestricted inherited descriptor.
- [ ] Unsupported/unverifiable runtime closure blocks before signer access.
- [ ] A reproducible package build emits the manifest and exact artifact
      digests used by later provenance.

### Adversarial tests

Test fail-closed behavior for symlink/hardlink/path swap, mutable package
directory, manifest omission, stdlib/extension mutation, loader/library
injection, `LD_PRELOAD`, Python user/site path injection, inherited unexpected
FD, FD-number substitution, key-ID alias, duplicate public-key bytes, PID
reuse, child exec replacement, pidfd mismatch, broker replay, broker crash and
signer request before runtime validation. Test both before-exec and
between-check-and-release races on real Linux.

## 4. NR2 — post-merge authority transaction and live #1033 integration

### Scope

Integrate the accepted NR1 package only after #1033 and #1048–#1050 have merged.
Run the actual #1052 authority transaction from the exact merged `develop`
commit. The test must use the production-shaped ingress, authorization,
transaction store, native broker and provenance interfaces; a parallel test
publisher or fixture path is forbidden.

### Six-role live provenance

#1033 must independently observe and sign the live identities for:

1. evidence authority process;
2. ingress harness;
3. nginx/front proxy;
4. release router;
5. release A;
6. release B.

Each role binds a distinct expected identity, pidfd/live PID, UID/GID,
executable and complete artifact/runtime digest. Every claim is compared with
kernel-observed state and the exact NR1 package or deployment artifact. A
signed caller description does not satisfy this criterion.

### Authoritative transaction

- Consume accepted #1048 dual evidence-action authorizations and distinct key
  identities.
- Consume #1049 typed ingress records and #1033 live provenance without
  copying caller verdicts.
- Use #1050 as the sole evidence publication state machine.
- Bind native broker release to the exact transaction, bundle, control head,
  merged git commit, package digest and intended evidence key.
- Recheck child/runtime/provenance after derivation and before #1050 commit.
  Process exit, replacement or closure drift tombstones the transaction.
- #1052 recomputes final authority and eligibility; NR2 may not add a
  permissive compatibility path.

### Acceptance

- [ ] Exact #1033, #1048, #1049, #1050 and #1052 merge SHAs are recorded.
- [ ] The real Linux run begins from the exact merged `develop` commit and an
      NR1 reproducible package digest.
- [ ] All six roles are simultaneously live, independently observed and
      distinct where the contract requires distinct identities.
- [ ] Every executable/runtime/artifact claim equals live kernel state and
      pinned package/deployment bytes.
- [ ] The authority uses the actual post-merge HTTP/worker/publisher path and
      #1050 transaction store; no fixture-only side channel exists.
- [ ] Signer access occurs only after current authorization, ingress,
      transaction, broker and provenance validation.
- [ ] Post-derive checks complete before durable eligibility.
- [ ] Crash/restart, role exit and provenance drift leave no eligible PASS.
- [ ] The resulting evidence is traceable to the exact commit, package,
      authorizations, ledger heads, six roles and transaction ID.

### Adversarial tests

Exercise a stale pre-merge binary, wrong commit/package, caller-supplied
provenance, signed-but-false PID, PID reuse, swapped A/B role, duplicated role
identity, missing nginx/router, mutable Python dependency, wrong broker key,
old #1048 authorization, #1049 splice, #1050 crash at every durable boundary,
process replacement after derivation, provenance signer loss and replay of a
previously valid transaction. Each test must prove the public store contains
no eligible PASS.

## 5. Dependency graph

```text
#1048 accepted key identity ----+
#1050 store coordination -------+--> NR1 native package

#1033 live six-role provenance -+
#1048 authorization ------------+
#1049 typed ledger -------------+--> NR2 exact post-merge integration
#1050 transaction store --------+
#1052 authority contract -------+
NR1 native package -------------+
```

“Accepted” means merged normally into `develop` after the required reviews and
a green exact-commit pre-push gate. A local branch, draft PR, fixture or
review-blocked commit is not a satisfied dependency.

## 6. Required gates

Both child PRs require:

1. gray acceptance review against this plan;
2. Harper CISO review of signer isolation, runtime closure, process identity,
   transaction safety and fail-closed behavior;
3. independent `/codex-review` adversarial review;
4. exact-commit `.githooks/pre-push` PASS before push;
5. normal merge into `develop` with no unresolved finding, followed by a fresh
   merged-commit pre-push run.

Any P0/P1 finding returns the work to implementation. Review records must bind
the exact commit; a rebase invalidates them. Eye is N/A unless operator-visible
UI changes.

## 7. Closure and honest disposition

#1051 may close only after NR1 and NR2 merge, the exact post-merge Linux run
passes every acceptance/adversarial check, and #1052 consumes its evidence
without weakening the contract.

- `PASS`: complete native closure, current live six-role provenance and
  authoritative transaction all verify.
- `BLOCK`: any contract, identity, transaction or review criterion fails.
- `BLOCKED_EXTERNAL_LINUX`: the required real Linux environment is unavailable
  or unverifiable.

Until those conditions hold, #1051 and its parent remain OPEN/BLOCK. This plan
authorizes no implementation, issue creation, push, merge, promotion or
production deployment until CEO approval is recorded against its exact commit.
