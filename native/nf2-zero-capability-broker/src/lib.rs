//! NF2 zero-capability broker.
//!
//! This crate consumes an already accepted NF1 artifact. It must never be
//! linked into, or change, the NF1 hermetic-package crate.

#[cfg(all(feature = "adversarial-test-hooks", not(debug_assertions)))]
compile_error!("adversarial-test-hooks are structurally forbidden in release builds");

pub const BLOCKED_EXTERNAL_LINUX: i32 = 77;

pub mod canonical_json;
pub mod capability;
pub mod manifest;
pub mod sha256;

#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum Outcome {
    Completed,
    BlockedExternalLinux,
}

/// Sink notified at each capability-protocol reverify boundary.
///
/// The `on_capability_issued` hook receives the fully identity-bound
/// [`capability::CapabilityDescriptor`] (transaction id + foundation digest +
/// sealed runtime dev/inode + kind, with its domain-separated
/// `descriptor_sha256` already computed). The broker constructs this descriptor
/// in-process at the release boundary from the [`capability::CapabilityContext`]
/// it was handed (decoded by NF3 from the durable `Binding`) plus the sealed
/// runtime device/inode the child is bound to, then passes a reference to the
/// sink. This keeps the descriptor live-bound rather than reconstructed from
/// ambient state, and lets an NF3 `ClaimSession` durably record the descriptor
/// digest without owning the live runtime binding. Returning `Err` makes the
/// broker fail closed: the live child is killed/reaped via the existing `Child`
/// cleanup and the transaction is aborted. [`NoopSink`] never returns `Err`, so
/// the default [`run`] path is behaviorally identical to before this hook
/// existed.
pub trait CapabilitySink {
    /// Child is ptrace-stopped at the EXEC event, bound to the sealed runtime,
    /// and the post-exec sealed reverify passed.
    fn on_ready_bound(&self) -> Result<(), &'static str>;
    /// Authority reverify passed and the capability is about to be released to
    /// let the child produce its derived work. `desc` is the full
    /// identity-bound descriptor the broker built for this transaction.
    fn on_capability_issued(
        &self,
        desc: &capability::CapabilityDescriptor,
    ) -> Result<(), &'static str>;
    /// Child is ptrace-stopped at the EXIT event after producing derived work;
    /// the exit-stop sealed + authority reverify passed.
    fn on_derived_pending_recheck(&self) -> Result<(), &'static str>;
    /// Child reaped a clean exit and the final sealed + authority reverify
    /// passed: the transaction is committed.
    fn on_committed(&self) -> Result<(), &'static str>;
}

/// Default sink whose hooks are all no-ops. Restores the exact pre-hook
/// control flow when used via [`run`].
pub struct NoopSink;

impl CapabilitySink for NoopSink {
    fn on_ready_bound(&self) -> Result<(), &'static str> {
        Ok(())
    }
    fn on_capability_issued(
        &self,
        _desc: &capability::CapabilityDescriptor,
    ) -> Result<(), &'static str> {
        Ok(())
    }
    fn on_derived_pending_recheck(&self) -> Result<(), &'static str> {
        Ok(())
    }
    fn on_committed(&self) -> Result<(), &'static str> {
        Ok(())
    }
}

/// Default entry point. Behaviorally identical to the pre-hook `run()`: it
/// delegates to [`run_transactional`] with [`NoopSink`] and an all-zero
/// [`capability::CapabilityContext`], so no sink can ever abort the default
/// path and no descriptor is ever constructed.
pub fn run() -> Result<Outcome, &'static str> {
    run_transactional(&NoopSink, &capability::CapabilityContext::zero())
}

/// Transactional entry point that emits a stage notification at each capability
/// reverify boundary. `ctx` supplies the transaction id and foundation digest
/// the broker combines with the sealed runtime identity to build the
/// [`capability::CapabilityDescriptor`] at the `on_capability_issued` boundary.
/// Non-Linux-x86_64 hosts are still explicitly blocked.
pub fn run_transactional<S: CapabilitySink>(
    sink: &S,
    ctx: &capability::CapabilityContext,
) -> Result<Outcome, &'static str> {
    #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
    {
        linux::run_transactional(sink, ctx).map(|()| Outcome::Completed)
    }
    #[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
    {
        let _ = (sink, ctx);
        Ok(Outcome::BlockedExternalLinux)
    }
}

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod linux;

#[cfg(test)]
mod tests {
    #[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
    #[test]
    fn unsupported_host_is_explicitly_blocked() {
        assert_eq!(super::run(), Ok(super::Outcome::BlockedExternalLinux));
    }

    #[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
    #[test]
    fn noop_sink_path_matches_default_run() {
        assert_eq!(
            super::run_transactional(&super::NoopSink, &super::capability::CapabilityContext::zero()),
            Ok(super::Outcome::BlockedExternalLinux)
        );
    }
}
