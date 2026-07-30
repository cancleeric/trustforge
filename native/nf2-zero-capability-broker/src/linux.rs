//! Linux x86_64 implementation.
//!
//! Boundary implementation is deliberately unavailable until every retained
//! descriptor, pidfd, ptrace, deadline, and cleanup invariant is installed.

mod live;
mod process;
mod sealed;

pub fn run() -> Result<(), &'static str> {
    let sealed = sealed::SealedNf1::open()?;
    sealed.reverify()?;
    process::run(&sealed)
}
