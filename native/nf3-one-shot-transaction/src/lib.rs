//! Linux retained-directory-FD primitives and durable one-shot burn ledger.
//!
//! This crate grants no execution, signing, capability, or release authority.
#[cfg(all(feature = "adversarial-test-hooks", not(debug_assertions)))]
compile_error!("adversarial test hooks are forbidden in release builds");

#[derive(Debug)]
pub enum Error {
    UnsupportedPlatform,
    InvalidName,
    TooManyEntries,
    UnsafeObject(&'static str),
    IdentityChanged,
    Os(i32),
    Io(std::io::Error),
}

impl From<std::io::Error> for Error {
    fn from(value: std::io::Error) -> Self {
        Self::Io(value)
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{self:?}")
    }
}

impl std::error::Error for Error {}

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod ledger;
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod linux;
mod sha256;
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
pub use ledger::{Binding, ClaimSession, LedgerStore, Request, State};
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
pub use linux::{Dir, Entry, Vfs};

#[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
pub struct Vfs;

#[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
impl Vfs {
    pub fn open(_root: &std::path::Path) -> Result<Self, Error> {
        Err(Error::UnsupportedPlatform)
    }
}

#[cfg(all(test, not(all(target_os = "linux", target_arch = "x86_64"))))]
mod tests {
    use super::*;

    #[test]
    fn unsupported_host_is_explicitly_blocked() {
        assert!(matches!(
            Vfs::open(std::path::Path::new("/")),
            Err(Error::UnsupportedPlatform)
        ));
    }
}
