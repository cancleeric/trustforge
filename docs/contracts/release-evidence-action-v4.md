# Release evidence action v4 and transaction-intent contract

Issue: #1064 (B1A of #1048)

## Purpose and non-authority boundary

This contract gives CEO/operator producers, #1060 and #1052 one exact wire
format. It can describe a structurally well-formed pair and compute an inert
transaction-intent digest. It does **not**:

- verify a production trust anchor or raw public-key identity;
- derive current promotion PASS, control head or next sequence;
- decide whether a nonce is unused or consume one;
- call `EvidenceTransactionStore._publish`;
- produce eligible evidence or authorize publication.

Those operations remain B1B dependencies on the OS-backed #1060 trust service
and #1052 integrated authority. #1050 remains the sole evidence transaction
store.

## Canonical signed object

Both roles sign the exact v4 schema and
`action=derive-and-publish-release-ingress-evidence`. CEO and operator use
different domain prefixes. Each object binds:

- target/candidate and active/candidate release digests;
- release manifest and current promotion PASS event hash;
- git, dataset, promotion policy, routing ramp and PIT cutoff;
- evidence bundle, routing key and control ledger/head/next sequence;
- transcript v2, provenance and intended evidence key;
- actor, key ID, nonce, issue time and expiry.

The pair must use different actor strings, key IDs and nonces. B1A records but
does not claim the production raw-key distinction: #1060 loads the fixed trust
anchor and must reject equal raw Ed25519 bytes even when aliases differ.

## Transaction-intent digest

`describe_evidence_action_intent` checks syntax, exact pair scope, structural
identity separation and time windows, then hashes both complete signed
envelopes and scope under
`trustforge.release-evidence-transaction-intent.v1`.

The returned `EvidenceActionIntentDescription` is inert. A changed field or
signature changes the digest. It is not a receipt, verdict, authority token,
nonce commitment or publication capability.

## Migration and goldens

The checked-in golden file contains deterministic valid CEO/operator
signatures and invalid migration cases. Cases enforced by `B1A` are executable
schema/structure/crypto vectors. `B1B_OS_TRUST` cases explicitly identify
replay, stale/fork state and duplicate raw-key aliases that cannot be decided
inside this pure module.

Deployment authorization v3 remains unchanged and accepts only its existing
non-evidence actions. V1/v2/v3, `start` and `start-canary` never migrate into
the v4 evidence-action contract.
