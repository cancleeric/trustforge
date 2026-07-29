# Release HTTP canary provisioning contract

K2b provisions `/etc/trustforge/release-router-allowlist.json` only through the
root-only provisioner. The input request contains identities, Analyze/Compare
endpoint names, and asset scopes. Release digests, ramp/policy identity,
authenticated control-ledger identity/head, and the nginx worker UID are
derived on the target host; callers cannot supply them.

The signed install receipt uses
`trustforge.release-install-evidence/v2` and binds the SHA-256 digest of the
fully derived allowlist. A v1 receipt cannot authorize this installation.

The provisioner requires canonical root-owned `0600` input, authenticates the
complete control ledger while holding the shared coordination lock, checks the
runtime projection, and rechecks the expected control head before publication.
The allowlist binds `control_event_head`; outcome reservation/result CAS uses
the separate `outcome_head`. Outcome appends therefore do not invalidate a
valid allowlist, while any control transition immediately makes it A-only.

The provisioner validates the root-owned installed snippet against its expected
digest and the exact `nginx -T` source-file marker. It also proves that an
authenticated server block includes that exact path. It parses the
comment-stripped nginx blocks rather than accepting directive substrings or
comments, validates exactly one router location, and requires it to reject an empty
`$remote_user`, clear both legacy/client identity headers, and inject
`X-TrustForge-Trusted-Identity` from `$remote_user`. Publication uses a pinned
directory, an inode-bound prior backup, and atomic replacement, followed by
descriptor-based content and `root:root 0600` verification. Failed
post-publication validation restores and fsyncs the prior content, or removes
and fsyncs a newly created destination.

Production has no synthetic nginx-config CLI option. The provisioner pins a
root-owned, non-writable executable nginx descriptor and invokes that exact
descriptor with `-T`; the authenticated server source and router snippet are
then descriptor-read and matched to the emitted source sections. Authentication
must be effective at the canary location: sibling/nested auth directives and
`auth_basic off`/`auth_request off` cannot authorize it.

Rollback-failure evidence is v3 and binds both the target allowlist digest and
the prior restore digest (or the explicit `absent` state). A subsequent install
blocks before mutation when any unresolved rollback receipt exists; legacy v2
evidence cannot authorize cleanup or another attempt.

Linux production additionally verifies the AF_UNIX peer UID with
`SO_PEERCRED`. A direct socket client can obtain A responses but cannot present
an authenticated canary identity. Unsupported paths are always A-only.

The installer runs with the exact content-addressed release Python and blocks
unless it is Linux with `SO_PEERCRED`. Its live gates require unauthenticated
nginx traffic to return 401, authenticated traffic carrying malicious client
identity headers to be overwritten and reach B, and the same spoof sent
directly over the Unix socket to remain on A.

The runtime key schema includes a nonempty, exact 32-byte public-only
`canary_cost_budget_public` role. A private cost-budget key in the runtime file
is forbidden. `load_runtime_key_material()` exposes the exact key IDs/bytes
through immutable typed `RouterRuntimeKeyMaterial` for the cost composition
layer; validation never loads a cost-budget private key. Fresh ledger
provisioning emits the v2 permission receipt with
`candidate_cost_reconciliation`; older v1 ledgers are rejected by runtime until
the audited ledger migration upgrades that receipt.

This installer is upgrade-only: the expected router snippet must already be
loaded by the authenticated nginx server before staging can be authorized. A
fresh host must establish that prerequisite through a separately reviewed
bootstrap procedure. If `nginx -T`, the exact authentication topology, the
worker account, the Linux release runtime, peer credentials, or live smoke
behavior are unavailable, release evidence is `BLOCK`. Temporary handlers,
monkeypatched transports, and synthetic nginx output are test fixtures and
never production evidence.
