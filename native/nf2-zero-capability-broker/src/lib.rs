//! NF2 zero-capability broker.
//!
//! This crate consumes an already accepted NF1 artifact. It must never be
//! linked into, or change, the NF1 hermetic-package crate.

#[cfg(all(feature = "adversarial-test-hooks", not(debug_assertions)))]
compile_error!("adversarial-test-hooks are structurally forbidden in release builds");

pub const BLOCKED_EXTERNAL_LINUX: i32 = 77;

pub mod canonical_json;
pub mod manifest;
pub mod sha256;

#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum Outcome {
    Completed,
    BlockedExternalLinux,
}

pub fn run() -> Result<Outcome, &'static str> {
    #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
    {
        linux::run().map(|()| Outcome::Completed)
    }
    #[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
    {
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
}
