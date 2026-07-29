# Release evidence transaction store v1

Issue: #1050 (B3 of #1032)

## Eligibility contract

`release-ingress-evidence.json` is the only canonical evidence name.  It is
eligible only when all of the following are true:

1. the directory and file are trusted, nonsymlinked objects owned by the
   configured root UID;
2. the file has mode `0600`, link count one, the exact v1 commit-marker fields
   and canonical JSON encoding;
3. its decoded payload matches its `sha256:` digest;
4. there is no `release-ingress-evidence.tombstone`;
5. there is no `.evidence-*.stage` transaction guard.
6. the pinned `.release-evidence-transaction-state` hash chain ends in a
   durable `COMMIT` binding the same transaction ID and digest.

Staging uses the distinct
`trustforge.release-evidence-staging/v1` schema, has state `INELIGIBLE`, and
stores evidence only as opaque base64.  A consumer must never parse staging as
an evidence verdict.

A tombstone uses
`trustforge.release-evidence-tombstone/v1`, has disposition `NON_PASS`, and
always overrides a canonical marker.

## Durable ordering

Publication performs these steps in order:

1. lock the pinned root-owned directory inode, then the root-owned state
   descriptor, with exclusive advisory `flock` locks;
2. append and `fsync` a hash-chained `BEGIN` terminal-state record;
3. exclusive staging write; file `fsync`; directory `fsync`;
4. exclusive prepared-marker write; file `fsync`; directory `fsync`;
5. no-replace hard link to the canonical name; directory `fsync`;
6. prepared-name unlink; directory `fsync`;
7. staging unlink; directory `fsync`;
8. append and `fsync` the digest-bound `COMMIT`;
9. reload and verify the state chain, exact payload, digest and metadata.

No step is reported successful until step 9 passes.  Before both hidden names
are removed, staging presence and/or link count greater than one makes the
canonical entry ineligible.  After hidden names are removed, the durable
`BEGIN` remains an independent permanent veto until `COMMIT`.  On failure, the
store writes and fsyncs a tombstone and records `ABORT`; if both cleanup paths
fail, the earlier `BEGIN` still prevents eligibility.  The state file is a
permanent control object, not evidence residue.

## Retry, immutability and recovery

An exact-byte retry of eligible evidence returns the existing commit.  A
different payload never overwrites or revokes eligible prior evidence.
All publication, retry and recovery decisions first lock the pinned directory
inode and then the state descriptor.  At every publish boundary, the directory
pathname and state pathname must still resolve to the locked device/inode with
the required owner, mode and link count.  A rename or replacement fails closed.
The descriptor and evidence directory must be on the same filesystem
(`st_dev` equality is enforced).  Cooperative writers therefore cannot race
the read/decision/publish interval.  A failed transaction may tombstone or
unlink the canonical name only after revalidating coordination identity and
proving the canonical marker contains its own transaction ID and digest; it
never globally revokes an unowned winner.
Recovery treats every stale staging transaction as non-PASS, durably writes a
tombstone, and only then attempts staging cleanup.  Cleanup failure leaves the
transaction ineligible.

The fault matrix covers zero/short writes, file and directory fsyncs, creation,
link, unlink, tombstone creation, every post-staging-unlink tombstone/rollback
combination, crash points, restart recovery, unsafe metadata, multi-link
artifacts and real two-process barrier races.  It proves observable fail-closed
state; it does not claim that an unavailable filesystem can be repaired.

## Authority boundary

`EvidenceTransactionStore._publish` is intentionally private.  It is the B3
storage primitive, not an authorization or verdict authority, and this change
does not wire it into a production caller.  The integrated B5 authority is a
hard dependency: it must be the sole owner of a capability that reaches this
method, and may call it only after the accepted B1 authorization, B2 event
proof and B4 provenance contracts all pass in the same trusted operation.

#1050 neither accepts caller-supplied key maps nor defines a verifier protocol,
key registry, promotion decision or public eligible-publication API.  Those
belong to #1048 and B5 after this concrete store lands on `develop`.
