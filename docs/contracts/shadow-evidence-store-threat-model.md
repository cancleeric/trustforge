# Shadow evidence store threat model

The v1 shadow evidence store is a POSIX-only, local SQLite ledger. It must live
in a dedicated directory owned by the service uid with mode `0700`; the
database and SQLite sidecars must be private regular files owned by that uid.

## Enforced boundary

The store fails closed for:

- symlinks, non-regular database files, unsafe owners or permissions;
- replacement of the configured parent directory or primary database inode;
- corrupt, unknown, altered, or non-canonical schemas and evidence;
- locked, full, oversized, or unbounded storage;
- concurrent trusted worker writes, which serialize on a persistent
  `O_NOFOLLOW` directory descriptor bound to the verified parent inode.

Each store also uses an in-process reentrant mutex because POSIX `flock` does
not serialize threads sharing one open-file-description. After `fork`, the
child discards the inherited descriptor and opens a separately validated
directory descriptor, so its lock conflicts with the parent's lock as intended.
Closing a store waits for its current operation, is idempotent, and makes all
later operations fail deterministically.

SQLite may legitimately create, remove, or replace WAL and SHM lifecycle files.
Each observed sidecar is independently opened without following symlinks and
validated for type, owner, and permissions. The combined database, WAL, and SHM
size is bounded.

## Explicit non-goal

A malicious process running as the same service uid is outside this boundary.
Such a process can bypass advisory locks, mutate owner-only files, inspect or
alter this process, and race stdlib SQLite pathname/VFS operations. Python's
stdlib SQLite API does not expose a supported descriptor-bound VFS or the
connection's database descriptor, so pathname snapshots or `/dev/fd` scans
would provide a misleading security claim.

Deployments that require isolation from hostile local code must run the store
under a dedicated uid/container boundary or use a reviewed descriptor-bound
SQLite VFS. The store must not be enabled in a shared, same-uid plugin runtime.
