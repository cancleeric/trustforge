# Issue #1049 — trust-verifier service hard-limit remediation split

- Owner: gray (CPO)
- Parent: #1032
- Baseline: `origin/develop@1976946b0243309329a93f924f163c1f113478ac`
- Date: 2026-07-30
- Status: **CEO REVIEW REQUIRED — implementation forbidden**
- Current disposition: **#1049 OPEN / BLOCK**
- Cause: three review rounds found no independent trust boundary

## 1. Gray decision

#1049 cannot treat a verifier that executes inside the evidence-producing
Python process as a trust source. That process can replace module globals,
monkeypatch closure state, alter private seals and choose the paths, digests or
keys it later “verifies.” Likewise, an `/etc` pathname or expected digest is
not an authority without an atomic root-owned installer, version selection,
service permissions and a deployment proof showing the non-root consumer can
use the interface without reading the trust-anchor secret/material directly.

Stop the current implementation. No rejected revision or passing in-process
test receives release-completion credit. Split the remainder into these
bounded issues:

| Work | Estimate | Dependencies | Deliverable |
|---|---:|---|---|
| TV1 — OS-backed trust-verifier service | 10–12h | #1048 identity/domain contract; coordinate with #1050 and #1051 native boundary | Independently provisioned, least-privilege verifier with a fixed root-owned versioned trust anchor and authenticated Unix-socket protocol |
| TV2 — typed ledger semantics and public run verdict | 10–12h | accepted TV1; #1031 production ingress contract; #1048; #1050; frozen #1052 authority contract | Strict S01–S12/exact-set recomputation exposed as one authoritative run-ID verdict and consumed by production ingress/B5 |
| TV3 — Linux deployment and adversarial E2E | 8–12h | accepted TV1 + TV2 + #1031 + #1033 + #1048 + #1050 + #1051 + #1052 | Exact merged-commit systemd deployment proof, production-shaped traffic and fail-closed release evidence |

TV2 may prepare schema-only work while TV1 is in review, but it cannot claim
an authoritative verdict or merge its integration until TV1 is accepted. TV3
starts only after all dependencies are normally merged into `develop`.
At hour 6 gray reviews scope; at hour 10 work stops and splits again if its
remaining acceptance evidence cannot finish within 12 hours.

## 2. Prohibited evidence and authority shortcuts

The following cannot satisfy any release acceptance:

- a verifier in the same Python process as the producer or publisher;
- monkeypatch resistance, a private module global, closure, object identity,
  hash seal or import trick used as a security boundary;
- a caller-supplied trust-anchor path, digest, key, key ID, policy, socket,
  UID, PID, executable identity or verdict;
- test-only UIDs, temporary-directory fixtures or a root test process standing
  in for installed service ownership and permissions;
- Darwin, simulated Linux, container-only fixtures or signed descriptions not
  compared with live OS state;
- a valid signature over incomplete/generic records, or a caller-provided list
  of records presented as the exact referenced set.

Unavailable external Linux returns `BLOCKED_EXTERNAL_LINUX`. Missing,
unreadable, stale, ambiguous or unverifiable trust state returns `BLOCK`.
Neither result may degrade to a compatibility path or synthetic PASS.

## 3. TV1 — OS-backed trust-verifier service

### Scope

Create a native verifier or minimal separately confined process whose binary,
configuration and trust anchor are installed by a root-owned provisioning
flow. It reads authenticated ledger bytes itself, verifies the fixed policy
and returns a signed or cryptographically bound typed result over a local Unix
socket. The producer and router are untrusted clients.

### Provisioning and versioned trust anchor

- Provide an idempotent root-only installer/uninstaller or package script and
  systemd units; application startup must not create or rewrite trust state.
- Provision the verifier binary, public trust-anchor/keyring, policy and
  manifest as root-owned, non-group/world-writable regular files under fixed
  compiled/package paths.
- Install a versioned directory, verify complete bytes, fsync files and parent
  directories, then atomically switch a root-owned current-version reference.
- The service opens the selected version with no-follow/beneath-only semantics
  and pins descriptors. It never reopens a caller-selected path.
- Rollback is an explicit privileged version transaction with an auditable
  generation, not an application-controlled symlink swap.
- Trust anchors bind #1048 domains, actor IDs, key IDs and raw public-key bytes;
  duplicate key bytes under aliases fail closed.

### Service and socket boundary

- Run under a dedicated non-login UID/GID, distinct from ingress/router,
  publisher and release processes.
- Use a root-owned Unix socket directory and explicit group/ACL granting only
  the minimum connect permission. Router/ingress need not and must not read
  anchor/config files.
- Authenticate every peer with kernel credentials (`SO_PEERCRED` or an
  equivalently race-free Linux mechanism) and an allowlisted service identity;
  payload UID/PID claims are ignored.
- Bind each request to a strict schema/domain, unique run ID, ledger root/head,
  exact record references, authority transaction ID, deadline and nonce.
- Return a typed response binding request digest, service generation, policy
  digest, exact referenced-set digest, recomputed verdict/reasons and response
  nonce. No generic sign-or-verify endpoint exists.
- Use fixed inherited descriptors and close all unlisted descriptors. The
  application cannot supply config, anchor, key or arbitrary files by FD.

### systemd confinement

The checked-in deployment must specify and test, at minimum:

- `User`/`Group` dedicated to the verifier and no login shell;
- `NoNewPrivileges=yes`, restricted capabilities and an empty ambient set;
- `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`;
- explicit `ReadOnlyPaths` for the selected verifier package/trust data and no
  writable application/code path;
- only the required runtime/state/socket paths writable;
- address-family, namespace, device, syscall and executable restrictions
  appropriate to the chosen implementation;
- explicit file-descriptor/socket activation policy with no unexpected
  inherited FDs;
- restart behavior that fails closed during generation or anchor ambiguity.

If the platform cannot enforce an item, the plan must document an equivalent
stronger control and Harper must approve it.

### Acceptance

- [ ] Verifier execution is a separate OS process with a dedicated live
      kernel-authenticated identity.
- [ ] Fixed root-owned provisioning atomically installs and selects a complete
      versioned trust anchor/config/binary package.
- [ ] Non-root ingress/router can call only the narrow Unix-socket protocol and
      cannot read or modify trust files.
- [ ] Peer identity derives from the socket/kernel, never request fields.
- [ ] Requests and responses bind run, ledger, exact referenced set,
      transaction, policy generation, nonce and digest.
- [ ] All files and FDs are fixed, pinned, type/owner/mode/digest checked and
      caller path/FD injection is rejected.
- [ ] Service unavailable, stale, wrong-generation, ambiguous or malformed
      responses fail closed before signer/publication access.
- [ ] Provision, upgrade, rollback and restart are durable, atomic and
      auditable on real Linux.

### Adversarial tests

Test same-process monkeypatch/global/closure replacement, socket impersonation,
payload UID spoofing, unauthorized UID/group, FD smuggling, caller anchor path,
caller digest/key/key-ID, anchor alias with duplicate public bytes, symlink and
hardlink swap, partial install, power/crash at each fsync/switch boundary,
version rollback race, stale service response, nonce replay, mixed generations,
socket replacement, unexpected inherited FD, writable `/etc` substitute and
service restart during verification. Every case must block and yield no
eligible PASS.

## 4. TV2 — typed ledger semantics and public run-ID verdict

### Scope

Preserve the strict #1049 typed ledger contract, but move authoritative
verification behind TV1. The production ingress submits a run ID and immutable
ledger coordinates, not a caller-assembled verdict. TV1 loads authenticated
records, derives the exact referenced sets and recomputes S01–S12. #1052/B5
consumes the bound TV1 response through the production path.

### Exact referenced-set rules

- Derive membership from signed causal graph roots, run/scenario IDs, ledger
  heads and terminal boundaries. Caller-provided record arrays are hints only.
- Bind a canonical sorted set of every consumed record hash plus typed
  missing, duplicate, extra and conflicting-record findings.
- Reject cross-run, cross-scenario, stale-head, reordered, omitted, duplicate,
  unreachable and post-terminal records.
- Bind all Analyze and Compare attempts, configured identities/cohorts/buckets,
  reservations, outcomes, reconciliations, barriers, stop/rollback events and
  terminal observations. There is no “representative sample.”

### Required semantics

TV1 must recompute from authenticated bytes:

- canonical request, identity/cohort, deterministic bucket, ramp, epoch,
  releases and control head;
- selected route, terminal HTTP/body/header digests and typed error;
- exact request/model/microusd reservation → result → reconciliation
  conservation and zero post-barrier B;
- signed causal failure → stop barrier → zero subsequent B;
- S12 dedicated activation rollback-to-A, never a second operator stop;
- terminal 100% A for every configured cohort and routing bucket;
- Analyze/Compare ordered asset semantics and actual successful B records.

### Public verdict contract

- Expose one typed public verdict keyed by unguessable run ID, exact ledger
  heads and TV1 generation.
- Bind per-scenario S01–S12 disposition, reasons, exact-set digest, totals and
  one aggregate `PASS`/`BLOCK`/`BLOCKED_EXTERNAL_LINUX`.
- A verdict is current only for the exact #1048 authorization scope, #1031
  ingress run, #1050 transaction and #1052 authority request.
- #1052/B5 reloads and verifies the TV1 response; ingress cannot translate or
  override it.
- Legacy/generic/v1/synthetic ledgers and private fixture interfaces block.

### Acceptance

- [ ] Strict schemas reject unknown, missing, wrong-type and legacy fields.
- [ ] TV1 independently loads the authenticated ledger and derives the exact
      referenced sets for the public run ID.
- [ ] S01–S12 and aggregate verdict are recomputed with no caller route,
      summary, set or PASS authority.
- [ ] Conservation, causal ordering, rollback and all-bucket terminal A are
      exhaustive rather than sample based.
- [ ] Production ingress and #1052/B5 use the TV1 interface; no alternate
      in-process verifier can publish.
- [ ] Service generation, response nonce, policy, ledger heads, exact-set
      digest and authority transaction remain bound through #1050 commit.
- [ ] TV1 loss or response ambiguity before commit tombstones/blocks the
      transaction.

### Adversarial tests

Cover record omission/addition/duplication/reordering, valid signed cross-run
splice, unreachable record, stale head, generic signed record, false route,
2xx/5xx mismatch, ordered-asset mutation, cap undercount, missing/double
reconciliation, stop-before-failure, later B after barrier, operator-stop used
as rollback, incomplete bucket coverage, caller-selected exact set, verdict
substitution, TV1 response replay, generation change and service crash before
commit. Assert both the public verdict and #1050 store remain fail closed.

## 5. TV3 — Linux deployment and production-shaped E2E

### Scope

Install the accepted TV1 service from the exact merged `develop` commit in the
real Linux evidence environment. Drive TV2 using #1031 production ingress,
#1033 live process provenance, #1048 authorization, #1050 transaction store,
#1051 native boundary and #1052/B5. This is the release-evidence gate; fixtures
and Darwin results remain development diagnostics only.

### Acceptance

- [ ] Record exact merge SHAs, package/anchor generation and deployment
      manifest for all dependencies.
- [ ] Verify live systemd unit, UID/GID, socket ownership/ACL, file ownership,
      modes, mount/read-only controls, FDs and confinement settings.
- [ ] Prove router/ingress can connect while being unable to read or modify
      trust anchor/config/binary files.
- [ ] Drive real Analyze and Compare S01–S12 traffic through #1031 and derive
      one TV2 public run-ID verdict from exact authenticated records.
- [ ] #1033 binds the live verifier plus required evidence/harness/nginx/router
      and A/B process identities to exact deployed bytes.
- [ ] #1052/B5 consumes that verdict and #1050 is the sole durable eligibility
      path.
- [ ] Restart, crash, upgrade/rollback and trust-generation changes cannot
      preserve or create an eligible stale PASS.
- [ ] Fresh post-merge pre-push and production-shaped E2E are both green on
      the recorded commit.

### Adversarial tests

Repeat peer spoofing, socket/file permission mutation, service replacement,
anchor rollback, mixed generation, stale response, run replay, ledger splice,
service crash/restart, dependency process replacement and #1050 crash/recovery
against the installed units. Also prove Darwin, temp fixture, test UID and
caller-selected trust inputs cannot be submitted as release evidence.

## 6. Dependency graph

```text
#1048 identity/domain ----+
#1050 store coordination -+--> TV1 OS trust service
#1051 native boundary ----+

TV1 accepted -------------+
#1031 production ingress -+
#1048 authorization ------+--> TV2 ledger semantics/public verdict
#1050 transaction store --+
#1052 frozen B5 contract -+

TV1 + TV2 accepted -------+
#1031 + #1033 ------------+
#1048 + #1050 ------------+--> TV3 Linux deployment/E2E
#1051 + #1052 ------------+
```

“Accepted” means normally merged to `develop` with every required review and a
green exact-commit gate. A local commit, open/draft PR, fixture or blocked
review does not satisfy a dependency.

## 7. Required reviews and gates

Every child PR requires:

1. gray acceptance review against this exact plan;
2. Harper CISO review of provisioning, trust/key identity, peer
   authentication, confinement, transaction safety and fail-closed behavior;
3. independent `/codex-review` adversarial review;
4. exact-commit `.githooks/pre-push` PASS before push;
5. no unresolved finding and normal merge into `develop`;
6. a fresh full pre-push run from the merged `develop` commit.

Any rebase invalidates commit-bound reviews. Any P0/P1 finding returns the work
to implementation. Eye is N/A unless operator-visible UI changes.

## 8. Closure and honest disposition

#1049 may close only after TV1–TV3 merge and the exact installed Linux E2E
proves the public run-ID verdict through production ingress and #1052/B5.

- `PASS`: independent fixed trust service and exact S01–S12 public verdict
  verify end to end on real Linux.
- `BLOCK`: any trust, identity, ledger, transaction, deployment or review
  criterion fails.
- `BLOCKED_EXTERNAL_LINUX`: the required external Linux environment is absent
  or unverifiable.

Until then, #1049 and parent #1032 remain OPEN/BLOCK. This document authorizes
no implementation, child issue creation, push, merge, promotion, release or
deployment until CEO approval is recorded against its exact commit.
