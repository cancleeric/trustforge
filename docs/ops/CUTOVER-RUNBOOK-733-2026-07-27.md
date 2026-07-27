# #733 release-level A/B deployment-readiness runbook

Status: deployment-ready control and data-plane package only. It does not
authorize or perform a production deployment, traffic cutover or promotion.

## Architecture and single truth

A and B are complete, separately running immutable application releases on
distinct loopback HTTP endpoints. Neither endpoint hot-swaps
`trustforge_core`; the release router sits outside the application and never
imports the Kernel.

The authenticated append-only deployment ledger at
`/var/lib/trustforge/security-ledger` is the control-plane single source of
truth. Every record is canonical and sequence/hash chained. Every record and
its independently authenticated head is Ed25519 signed under an event-kind
capability domain; the head detects complete-tail truncation. The directory
and files are owner-only `0700`/`0600`, opened with `O_NOFOLLOW`,
locked across verification and append, bounded, and file/directory fsynced.
Unknown keys, corruption, truncation, stale heads and unsafe ownership fail
closed.

The data-plane router reauthenticates this ledger before every possible B
route. Missing/corrupt/disabled/stopped/prepared state, missing stable identity,
missing routing key, exceeded request cap or invalid policy always routes the
separately pinned A endpoint. It never logs a subject or subject-derived value.

The router hot path is read-only. It never advances a clock file. After each
successful control-plane terminal transition, the control process atomically
writes and file/directory-fsyncs a coarse authorization checkpoint. That
checkpoint is only a projection of the latest signed terminal head/sequence and
its ledger-authenticated monotonic `checkpoint_floor_at`. Completion advances
the floor from the independently signed completion `verified_at`; operator stop
uses the ledger-signed terminal `at`; every terminal stores the maximum prior
floor while always advancing the head. Missing, stale or corrupt checkpoint
projection blocks a read-only router and is atomically rebuilt only by a
control signer after full semantic replay. It never blocks status or emergency
control. An unresolved
authorization is always checked against the real current clock, never an
attacker-controlled event timestamp. A clock rollback
below the checkpoint blocks B, start and promotion, while status, stop,
rollback-to-A and failed-transition reconciliation remain available.

## Protected inputs

Production consumes fixed owner-only files:

- `/etc/trustforge/deployment-control.json`
- `/etc/trustforge/deployment-keys/{ledger,authorization,completion,gates,routing,endpoint-manifests}.json`
- `/etc/trustforge/release-router-runtime.json`
- `/etc/trustforge/release-router-runtime-keys.json`
- `/var/lib/trustforge/security-ledger/`

Operator keyrings are physically separated by purpose and carry key IDs: ledger, authorization,
activation completion, executable gates and privacy routing. Rotation retains
old verification keys while selecting a new active ledger/routing key.
Environment variables and command-line secret values are not accepted.
`status` opens only the ledger key file; emergency `stop` opens only ledger and
authorization keys and never touches artifact, gate, completion, routing or
endpoint-manifest inputs. The long-running router accepts exactly ledger,
routing and Ed25519 endpoint-manifest public verification roles.

The protected config binds:

- exact A/B artifact digests and fd-verified artifact paths;
- exact separate loopback A/B endpoints;
- production target and the literal confirmation
  `PRODUCTION:<target>:<A digest>:<B digest>`;
- signed limited-ratio policy, request cap, timeout, ramp ID and routing key ID;
- the canonical #732 `trustforge.shadow-health/v1` export;
- all signed executable gate receipts.

## Preflight evidence

Both A and B artifacts are opened relative to pinned directory descriptors with
`O_NOFOLLOW`, hashed through stable file descriptors, and checked for
device/inode/size/mtime changes.

The #732 export must be fresh and `eligible_for_operator_review`, with no
blockers. Exact active/candidate release identities, policy digest, contract
version, observation root, aggregate/decision IDs, ordered observation IDs,
completion checks, provider calls and cost are validated. The verifier opens
the actual #732 read-only SQLite store and independently reruns its deterministic
evaluation at the exported timestamp; owner-only JSON is not accepted as
provenance by itself.

Nine executable receipts are required: health, kernel golden, API contract,
Report, Evidence, snapshot, replay, real user workflow and rollback drill.
Each receipt is signed, key-ID/versioned, fresh, exact-A/B-bound and contains
command/output digests, unique nonce, result and zero provider cost. The A/B
snapshots, canonical shadow evidence and gate receipts form one evidence bundle
digest.

## Authorization and activation transaction

First initialize the disabled ledger and read its generated ledger ID:

```bash
python scripts/deployment_readiness.py initialize
python scripts/deployment_readiness.py status
```

`start`, `promote`, `stop` and `rollback-a` each require a separately signed,
15-minute authorization receipt:

```bash
python scripts/deployment_readiness.py start \
  --authorization /secure/start-authorization.json
```

The receipt binds action, target confirmation, ledger ID, exact A/B digests,
evidence bundle, routing policy digest, routing key ID, actor, expiry and
globally single-use nonce.

For `start`, `promote` and `rollback-a`, the command acquires the existing
activation lock and appends `activation_prepared`. Prepared is not active:

- prepared start remains A-only;
- prepared promotion enters an A-only safety hold until reconciliation;
- prepared rollback immediately makes desired state non-canary, so B stops.

The explicit release workflow performs the existing immutable pointer
transaction outside this issue. It then supplies a signed completion receipt:

```bash
python scripts/deployment_readiness.py complete \
  --receipt /secure/activation-completion.json
```

Completion binds the prepared event hash/transaction, exact A/B, observed
active pointer, actor, timestamp, status and nonce. Start and rollback require
the observed pointer to be exact A; promotion requires exact B. Lost lock,
forged/stale receipt or pointer mismatch cannot reconcile. Failed activation is
recorded distinctly. Thus desired and active phases cannot silently collapse
after a crash.

## Limited routing and automatic stop

The canary policy must remain between 1 and 9999 basis points. Stable identity
is first HMAC-pseudonymized, then assigned with a domain-separated HMAC bound
to candidate digest, ledger ID, ramp ID and routing key ID.

Each B response is atomically appended against the exact ledger head. Timeout,
transport error or HTTP 5xx fails over to A. At the configured consecutive
error threshold the same result event carries the authenticated automatic-stop
decision; the next request rereads `STOPPED` and cannot enter B. No health
result can promote.

Operator stop first creates an independent, Ed25519-signed, one-way latch for
the current canary epoch using `O_EXCL`, mode `0600`, and file/directory fsync.
The router checks that latch immediately before connecting to B. It does not
hold the global coordination lock across HTTP request/response. Under that lock
it performs the last latch check and establishes the B TCP socket with a
maximum 250 ms connect timeout, then releases the lock before sending. A stop
therefore waits at most for the bounded connect classification, never for a
hanging B response. The signed manifest and request use that same socket;
silent reconnect is forbidden.

The systemd sandbox runs `trustforge-router`, distinct from
`trustforge-operator`, and mounts the control ledger (including checkpoint and
epoch latches) read-only. Deployment keys and the control config are
inaccessible. Only `router-outcomes` and the pre-provisioned
`/run/trustforge-release-control/coordination.lock` inode are writable. Its
parent is root-owned mode `0750`, so the shared group can lock the mode-`0660`
inode but cannot unlink or replace it. Sysusers/tmpfiles artifacts provision
the identities and pinned inode before service start; the security-ledger root
is never shared read-write.

The executable data plane is `scripts/release_router_service.py`, packaged by
`deploy/trustforge-release-router.service`. It accepts idempotent GET only;
POST/PUT/PATCH/DELETE are rejected with 405, so failover never retries a
side-effecting request. Before serving either path it probes the endpoint's
signed `/.well-known/trustforge-release-manifest` and requires the served
artifact digest, origin and manifest key ID to match the ledger identity.
Install the reviewed unit and reverse-proxy snippet with
`deploy/install_release_router.sh --dry-run`, inspect the commands, then run
the same script as root in the authorized release workflow. The service listens
only on a mode-`0660` Unix socket. The nginx snippet rejects unauthenticated
requests, strips any client identity headers and injects `$remote_user` from
the site's authentication layer.

## Promotion, verification and rollback

Promotion is manual only and requires a new exact-bound authorization and
activation transaction. After promotion rerun and archive signed receipts for:
health, manifest/digest, golden, API, Report/Evidence, snapshot/replay, real
workflow, latency/error and zero unapproved provider cost.

On regression, stop first, then authorize `rollback-a`. Activate the retained
exact A artifact; never rebuild an old commit and never edit Kernel code during
the incident. Reconcile only after the signed completion receipt observes the
active pointer at exact A, then repeat the full verification matrix.

## Current release boundary

All repository tests use two real, separately running local HTTP servers.
Production endpoints are intentionally not provisioned or started by #733.
An independently authorized release workflow must provision immutable A/B
services and configure the external router before any production traffic
operation.

## Compatibility audit

Repository-wide reference search found no consumer of the removed
`trustforge.agent.kernel_canary` module or its former exports. The only
remaining `KERNEL_CANARY_RATIO` references are regression tests proving that
the obsolete environment switch cannot enter the formal candidate path. The
old imports are intentionally breaking: retaining a facade that reports or
enables `ratio=1` would recreate a second promotion authority. External
consumers, if discovered, must migrate to the authenticated release router;
there is no compatibility shim with activation semantics.
