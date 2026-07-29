# Issue #1048 — evidence authorization authority hard-limit split

- Owner: gray (CPO)
- Parent: #1032
- Baseline: `origin/develop@82c874c56ad12925015f9307ce286d6e77df6bb4`
- Date: 2026-07-30
- Status: **CEO REVIEW REQUIRED — implementation forbidden**
- Current disposition: **#1048 OPEN / BLOCK**
- Rejected revisions: every local #1048 implementation through
  `0ec67c0c5862db955659c0a91616bd245f2e0988`

## 1. Gray decision

#1048 cannot create an evidence-publication authority inside an untrusted
Python caller. A caller-selected loader, key map, ledger, lock, nonce store,
path, descriptor, scope or PASS boolean is not a trust source. Protocols,
private seals, object identity and same-process globals do not repair that
boundary.

The accepted #1050 store now supplies the sole durable evidence transaction
primitive, but intentionally keeps `_publish` private for B5. It binds payload
digest and transaction ID; it does not authenticate authorization keys, derive
current PASS/control state or consume authorization nonces. #1060 owns the
OS-backed verifier and fixed trust anchor. #1052 owns integration of B1–B4 and
the only capability allowed to reach #1050 publication.

Stop implementation and split the remaining work:

| Work | Estimate | Dependencies | Deliverable |
|---|---:|---|---|
| A — v4 authorization and transaction-intent contract | ≤8h | merged #1050 contract; coordinate with #1060 request schema | Strict shared schema, canonical signing bytes, structural/crypto golden vectors and deterministic transaction-intent binding; **no authority, nonce consumption or publication** |
| B — trusted authorization and publication integration | 10–12h | accepted A + #1060 + #1050 + #1052 frozen integration contract | OS-trusted key/current-state/replay verification and same-boundary publication through #1050 |

A may begin only after CEO approves this plan and creates a scoped child
issue. B begins only after all dependencies are accepted on `develop`. At hour
6 gray reviews scope; work stops and splits before exceeding 12 hours.

## 2. Prohibited shortcuts

Neither child may treat any of these as release authority:

- caller-supplied key maps, trust-anchor paths, descriptors, digests, actor or
  key identities;
- caller-created or self-signed receipt/control ledgers;
- caller PASS/current booleans, expected heads, sequences, scopes or nonce
  sets;
- arbitrary loader/store/lock Protocol implementations;
- fake/no-op locks, private constructors, seals, module globals or
  same-process monkeypatch resistance;
- direct public exposure of `EvidenceTransactionStore._publish`;
- in-memory replay sets or a second transaction store beside #1050;
- synthetic/Darwin/test-UID results presented as release evidence.

Missing OS trust or integration yields `BLOCK` or
`BLOCKED_EXTERNAL_LINUX`. It never falls back to v1–v3, `start`,
`start-canary`, fixture verification or local publication.

## 3. Child A — v4 authorization and transaction-intent contract

### Scope

Define the shared data and canonicalization contract used by CEO/operator
signers, #1060 and #1052. This is a pure contract deliverable. Its result must
be named and typed as structurally/cryptographically described input, never
`authorized`, `verified`, `eligible` or `publishable`.

Both role payloads bind:

- schema, version, role-separated signing domain and exact
  `action=derive-and-publish-release-ingress-evidence`;
- target, candidate, active/candidate release digests and release manifest;
- promotion PASS event hash, git, dataset, promotion policy/ramp and PIT
  cutoff;
- evidence bundle digest, routing key, control ledger ID/head/next sequence;
- transcript v2 digest, provenance digest and intended evidence key ID;
- issued-at, expiry, nonce and nonempty actor/key identity.

Provide:

1. exact dataclasses/JSON schema rejecting missing, extra and wrong-type
   fields;
2. canonical unsigned producer construction and distinct CEO/operator signing
   bytes;
3. exact parser and structural checks for action/version/full-scope equality,
   actor/key-ID/nonce distinction, timestamps and maximum lifetime;
4. a deterministic transaction-intent digest binding both complete signed
   payloads and scope for later #1052/#1050 use;
5. checked-in valid and invalid golden vectors consumable by #1060 and #1052;
6. migration regression proving existing #997 deployment authorization v3
   remains strict only for non-evidence operations.

### Raw public-key distinction

A records two key IDs and the requirement that the corresponding raw Ed25519
public-key bytes differ. Golden vectors include duplicate-key aliases as an
expected `BLOCK`. A must **not** claim it verified this production invariant:
only #1060 may load the fixed OS trust anchor and compare the authoritative
raw bytes. B consumes that result and rebinds it to the transaction.

### Transaction boundary

A may compute a transaction-intent digest but must not call #1050 `_publish`,
consume a nonce, expose a public publication wrapper or add fields privately
to #1050 state. The digest is inert until B verifies it through #1060 and
commits it with the evidence payload through #1052’s sole #1050 capability.

### Acceptance

- [ ] Exact v4 CEO/operator schemas bind every required field and reject
      unknown/missing/wrong-type data.
- [ ] CEO and operator canonical signing domains are distinct.
- [ ] Structural validation rejects wrong action/version/scope, equal
      actors/key IDs/nonces, malformed signature bytes, stale/expired/overlong
      timestamps and invalid digest/sequence formats.
- [ ] Deterministic intent digest changes for every signed field/signature.
- [ ] Goldens cover v1/v2/v3, `start`, `start-canary`, wrong role/domain,
      wrong scope/head/sequence, expiry/lifetime, replay, stale/fork current
      state and duplicate raw-key aliases with explicit expected layer.
- [ ] No production API returns an authority/PASS/publication claim.
- [ ] Existing non-evidence v3 behavior has no permissive fallback.

## 4. Child B — trusted authorization and publication integration

### Scope

Extend #1060’s fixed root-owned trust service and #1052’s integrated authority
to consume A. The application submits only the strict signed v4 objects and
immutable run/transaction coordinates. It cannot select keys, ledgers, paths,
descriptors, expected state or policy.

#1060 must:

- load a fixed provisioned key registry and compare CEO/operator raw public
  key bytes, actors and key IDs;
- reject duplicate bytes under aliases and verify the two role-separated
  signatures;
- independently open authenticated promotion/control ledgers, derive the
  latest relevant current PASS, control head and next sequence, and reject
  stale/fork/self-signed state;
- bind service generation, policy, exact ledger identities/heads and the A
  transaction-intent digest in its authenticated response;
- authenticate the requesting peer through the kernel and enforce nonce
  replay under fixed OS-managed state.

#1052 must hold the sole capability to #1050 `_publish`. Under one concrete
trusted operation it must revalidate the #1060 response/current generation,
B2/B4 evidence and exact A intent; then commit the evidence payload through
#1050. No nonce is considered consumed unless the durable committed evidence
binds that intent, and no eligible evidence may exist without both nonces
being committed.

### Atomicity and recovery

The integrated design must prove two-or-neither CEO/operator nonce
consumption. Partial authorization state, process crash, service restart,
scope/head change, #1050 BEGIN without COMMIT, timeout or mixed generation
must leave no eligible PASS. Recovery must derive disposition from the fixed
#1060 state plus #1050 state chain, never a caller replay list.

### Acceptance

- [ ] Only fixed #1060 trust anchors provide keys and raw-byte identity.
- [ ] Current PASS/control/head/sequence are independently derived, never
      request fields.
- [ ] Both signatures, raw-key distinction, freshness and unused nonces verify
      against the exact A intent and current service generation.
- [ ] Scope verification, two-nonce commitment and #1050 evidence commit form
      one fail-closed consistency boundary.
- [ ] Partial nonce, crash/restart, concurrency and scope-race tests prove
      two-or-neither behavior and no stale eligible PASS.
- [ ] Fake/no-op lock, arbitrary loader/store, self-signed ledger, caller stale
      scope, key alias and direct `_publish` attempts block.
- [ ] Only #1052 owns the publication capability; no public compatibility
      path exists.

## 5. Dependency graph

```text
merged #1050 contract ----+
approved #1060 schema ----+--> A: v4 + intent contract

A accepted ---------------+
#1060 OS trust service ---+
merged #1050 store -------+--> B: trusted authorization + publication
#1049/#1051 contracts ----+
#1052 integrated B5 ------+
```

“Accepted” means normally merged into `develop` after exact-commit gray,
Harper and `/codex-review` PASS plus full local pre-push. A local commit,
fixture, open PR or passing same-process test is not a dependency.

## 6. Required gates

Each child requires:

1. gray acceptance review against this approved scope;
2. Harper CISO review of domains, identities, key provenance, replay,
   consistency and fail-closed behavior;
3. independent `/codex-review` adversarial review;
4. focused migration, malformed-input and adversarial tests;
5. exact-commit `.githooks/pre-push` PASS before push;
6. normal PR/reviewer attestation and merge into `develop`;
7. fresh post-merge full pre-push gate.

Eye is N/A unless an operator-visible UI changes. Any rebase invalidates
commit-bound reviews. Any P0/P1 finding returns work to implementation.

## 7. Honest disposition

- Child A can be `PASS` only as a non-authoritative shared contract. It never
  closes #1048 or permits publication.
- Child B can be `PASS` only when #1060/#1052/#1050 jointly prove trusted
  verification, two-nonce atomicity and publication on real Linux.
- `BLOCK`: any schema, trust, identity, replay, transaction or review
  criterion fails.
- `BLOCKED_EXTERNAL_LINUX`: the required installed OS trust boundary cannot be
  verified.

#1048 and #1032 remain OPEN/BLOCK until A and B are accepted. This document
authorizes no implementation, issue creation, push, merge or deployment until
CEO approval is recorded against its exact commit.
