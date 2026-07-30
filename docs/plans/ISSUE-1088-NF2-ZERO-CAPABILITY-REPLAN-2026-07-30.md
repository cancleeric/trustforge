# Issue #1088 — NF2 zero-capability acceptance replan

- Parent plan: PR #1086
- Dependency: accepted immutable NF1 package from #1087
- Gray CPO: APPROVED
- CEO: APPROVED
- Date: 2026-07-30
- Limit: 10–12 hours total; mandatory scope review at hour 6

## Dependency correction

The accepted NF1 executable only emits its fixed diagnostic version line and
exits. It has no READY protocol, Unix peer socket, capability input, or response
phase. NF2 must consume those accepted bytes unchanged. Modifying or rebuilding
`native/hermetic-package` invalidates NF1 provenance and is forbidden.

Consequently, an NF1-bound `SO_PEERCRED` handshake cannot be claimed honestly.
The approved NF2 profile transfers no capability descriptor and creates no
child control socket. `SO_PEERCRED` is mandatory only if a future profile adds
such a Unix peer channel; this profile must instead prove that no such channel
exists.

## Approved zero-capability design

NF2 is a separate Linux x86_64 broker crate:

1. Resolve the sealed install root, manifest, and accepted NF1 executable with
   `openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS|RESOLVE_NO_MAGICLINKS)`.
   Retain all authoritative descriptors and strictly validate the canonical
   NF1 manifest and runtime digest.
2. Fork a child with bounded diagnostic stdout/stderr pipes only. In the
   async-signal-safe pre-exec path, set and read back parent-death behavior,
   `no_new_privs`, exact architecture-specific default-deny seccomp, and total
   FD closure, then `execveat` the retained NF1 executable descriptor.
3. Use a ptrace EXEC stop before the first NF1 user instruction as an external
   observation barrier. READY is ephemeral and non-authoritative. It exists
   only after pidfd liveness, retained proc/exe identity, start time,
   executable digest/device/inode, package/manifest identities, seccomp,
   no-new-privileges, exact FD set, and `map_files` closure all pass.
4. Permit execution only after that verification. Accept exactly one bounded
   NF1 diagnostic stdout record; it is never capability, authority, PASS,
   eligibility, or release evidence.
5. At the ptrace exit barrier, before exit completes, repeat every root,
   manifest, process, executable, FD, mapping, and isolation check. Only then
   permit exit and reap the exact pidfd-bound child.
6. Timeout, unexpected stop, output mismatch, process substitution, missing
   kernel visibility, or cleanup ambiguity kills via `pidfd_send_signal`,
   reaps via `waitid(P_PIDFD)`, closes every descriptor, and returns BLOCK.

Numeric PID text and `/proc/<pid>` paths are diagnostic locators only. Retained
pidfd/proc/executable descriptors, start time, and kernel stop identity form
the authority. Missing `map_files` or ptrace permission is
`BLOCKED_EXTERNAL_LINUX` or BLOCK, never PASS.

## Acceptance

- [ ] NF2 is separate; accepted NF1 source, package, manifest, archive, and
      binary bytes are unchanged.
- [ ] Unsupported architecture, kernel feature, policy, or readback fails
      closed; external READY cannot be forged.
- [ ] `openat2` retains and rechecks sealed root, manifest, and runtime
      descriptors without path reopen or string-search JSON parsing.
- [ ] Every inherited FD outside the exact diagnostic allowlist is closed and
      verified; no Unix control/capability socket or `SCM_RIGHTS` path exists.
- [ ] pidfd, ptrace stop identity, retained proc/exe descriptors, start time,
      executable digest/device/inode, and `map_files` resist PID/path/exec
      substitution.
- [ ] All boundaries are reverified at EXEC and exit stops.
- [ ] Output is exact, bounded, public diagnostic data only.
- [ ] Timeout/error/broker exit kills and reaps the child and closes all
      descriptors without success.
- [ ] Linux mechanism/adversarial tests and host fail-closed tests pass.
- [ ] Exact-commit Gray, Harper, and `/codex-review` PASS; full pre-push,
      normal merge to develop, and fresh post-merge gate pass.

## Non-goals

No NF1 v2, child handshake, capability FD, durable transaction, signer,
authority, publication, release PASS, main merge, release branch, or production
deployment is included.
