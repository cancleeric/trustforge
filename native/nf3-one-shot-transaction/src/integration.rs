use crate::{Binding, ClaimSession, Error, LedgerStore, Request, accepted_foundation_sha256};
use trustforge_nf2_zero_capability_broker::Outcome;

const FIXED_OPERATION: &str = "nf2-fixed-diagnostic";
const FIXED_PAYLOAD: &[u8] = b"";

#[derive(Debug)]
pub enum IntegratedError {
    Ledger(Error),
    Nf2(&'static str),
    UnsupportedPlatform,
}

impl std::fmt::Display for IntegratedError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{self:?}")
    }
}

impl std::error::Error for IntegratedError {}

impl From<Error> for IntegratedError {
    fn from(value: Error) -> Self {
        Self::Ledger(value)
    }
}

/// Authority-neutral orchestration of one durable claim and accepted NF2 run.
///
/// The caller supplies only a transaction ID and deadline. Operation, payload,
/// executor, and foundation identity are the single fixed accepted diagnostic.
/// A successful result records an audit fact; it grants no signer, capability,
/// authorization, or release authority.
pub struct IntegratedRunner {
    store: LedgerStore,
}

impl IntegratedRunner {
    pub fn provision(store_id: &str) -> Result<Self, IntegratedError> {
        Ok(Self {
            store: LedgerStore::provision(store_id)?,
        })
    }

    pub fn open() -> Result<Self, IntegratedError> {
        Ok(Self {
            store: LedgerStore::open()?,
        })
    }

    pub fn execute(
        &self,
        transaction_id: &str,
        deadline_boottime_ns: u64,
    ) -> Result<Binding, IntegratedError> {
        let foundation = accepted_foundation_sha256()?;
        self.execute_with_foundation(
            transaction_id,
            deadline_boottime_ns,
            foundation,
            |sink, ctx| trustforge_nf2_zero_capability_broker::run_transactional(sink, ctx),
        )
    }

    fn execute_with_foundation<F>(
        &self,
        transaction_id: &str,
        deadline_boottime_ns: u64,
        foundation_sha256: String,
        run_nf2: F,
    ) -> Result<Binding, IntegratedError>
    where
        F: FnOnce(
            &ClaimSession<'_>,
            &trustforge_nf2_zero_capability_broker::capability::CapabilityContext,
        ) -> Result<Outcome, &'static str>,
    {
        let request = Request {
            foundation_sha256,
            operation: FIXED_OPERATION.to_owned(),
            payload: FIXED_PAYLOAD.to_vec(),
            deadline_boottime_ns,
        };
        // claim() durably creates the burn before returning this live session.
        // The non-Send session retains the same lock throughout NF2 execution.
        let session = self.store.claim(transaction_id, request)?;
        let binding = session.binding().clone();
        // Build the transaction-scoped context the broker combines with the
        // sealed runtime identity to construct the live-bound capability
        // descriptor. Both fields are fixed-width public identities decoded
        // from the durable Binding; no authority material crosses this boundary.
        let ctx = trustforge_nf2_zero_capability_broker::capability::CapabilityContext {
            transaction_id:
                trustforge_nf2_zero_capability_broker::capability::decode_hex_32(
                    &binding.transaction_id,
                )
                .map_err(IntegratedError::Nf2)?,
            foundation_sha256:
                trustforge_nf2_zero_capability_broker::capability::decode_hex_32(
                    &binding.foundation_sha256,
                )
                .map_err(IntegratedError::Nf2)?,
        };
        // ATTEMPT is durable immediately before calling NF2. It is not proof
        // that NF2 reached its irreversible action.
        execution_witness("ATTEMPT", self.store.store_id(), &binding)?;
        match run_nf2(&session, &ctx) {
            Ok(Outcome::Completed) => {
                execution_witness("DEFINITE_SUCCESS", self.store.store_id(), &binding)?;
                integration_checkpoint("AFTER_NF2_SUCCESS")?;
                session.commit()?;
                Ok(binding)
            }
            Ok(Outcome::BlockedExternalLinux) => {
                session.tombstone()?;
                Err(IntegratedError::UnsupportedPlatform)
            }
            Err(error) => {
                session.tombstone()?;
                Err(IntegratedError::Nf2(error))
            }
        }
    }

    #[cfg(test)]
    fn execute_with<F>(
        &self,
        transaction_id: &str,
        deadline_boottime_ns: u64,
        run_nf2: F,
    ) -> Result<Binding, IntegratedError>
    where
        F: FnOnce(
            &ClaimSession<'_>,
            &trustforge_nf2_zero_capability_broker::capability::CapabilityContext,
        ) -> Result<Outcome, &'static str>,
    {
        self.execute_with_foundation(
            transaction_id,
            deadline_boottime_ns,
            "33".repeat(32),
            run_nf2,
        )
    }

    #[cfg(any(test, feature = "adversarial-test-hooks"))]
    pub fn provision_for_test(
        root: &std::path::Path,
        store_id: &str,
    ) -> Result<Self, IntegratedError> {
        Ok(Self {
            store: LedgerStore::provision_for_test(root, store_id)?,
        })
    }

    #[cfg(any(test, feature = "adversarial-test-hooks"))]
    pub fn open_for_test(root: &std::path::Path) -> Result<Self, IntegratedError> {
        Ok(Self {
            store: LedgerStore::open_for_test(root)?,
        })
    }
}

#[cfg(feature = "adversarial-test-hooks")]
fn execution_witness(
    stage: &str,
    store_id: &str,
    binding: &Binding,
) -> Result<(), IntegratedError> {
    use std::path::Path;

    let Ok(path_value) = std::env::var("TRUSTFORGE_NF3_EXECUTION_WITNESS") else {
        return Ok(());
    };
    let path = Path::new(&path_value);
    let parent = path
        .parent()
        .ok_or(IntegratedError::Ledger(Error::UnsafeObject(
            "witness parent",
        )))?;
    let name = path
        .file_name()
        .ok_or(IntegratedError::Ledger(Error::UnsafeObject("witness name")))?;
    if name.as_encoded_bytes().contains(&b'/') {
        return Err(IntegratedError::Ledger(Error::UnsafeObject("witness name")));
    }
    // Retain a verified root-owned mode-0700 parent dirfd and use openat2
    // relative to it for the full locked append.
    let parent = crate::Vfs::open(parent)?;
    let executor = crate::foundation::accepted_build_identity()?;
    let frame = format!(
        "v1 stage={stage} transaction={} request={} store={} foundation={} boot={} deadline={} executor_profile={} executor_source={} executor_rlib={} executor_profile_receipt={}\n",
        binding.transaction_id,
        binding.request_sha256,
        store_id,
        binding.foundation_sha256,
        binding.boot_id,
        binding.deadline_boottime_ns,
        executor.profile,
        executor.linked_source_sha256,
        executor.linked_rlib_sha256,
        executor.profile_receipt_sha256,
    );
    parent.root().append_witness(
        name.to_str()
            .ok_or(IntegratedError::Ledger(Error::InvalidName))?,
        frame.as_bytes(),
    )?;
    Ok(())
}

#[cfg(not(feature = "adversarial-test-hooks"))]
fn execution_witness(
    _stage: &str,
    _store_id: &str,
    _binding: &Binding,
) -> Result<(), IntegratedError> {
    Ok(())
}

#[cfg(feature = "adversarial-test-hooks")]
fn integration_checkpoint(stage: &str) -> Result<(), IntegratedError> {
    use std::io::Write;

    if std::env::var("TRUSTFORGE_NF3_INTEGRATION_HOOK").as_deref() != Ok(stage) {
        return Ok(());
    }
    println!("INTEGRATION_PAUSED stage={stage}");
    std::io::stdout()
        .flush()
        .map_err(Error::Io)
        .map_err(IntegratedError::Ledger)?;
    match std::env::var("TRUSTFORGE_NF3_INTEGRATION_ERROR").as_deref() {
        Ok("EIO") => Err(IntegratedError::Ledger(Error::Io(
            std::io::Error::from_raw_os_error(5),
        ))),
        Ok("ENOSPC") => Err(IntegratedError::Ledger(Error::Io(
            std::io::Error::from_raw_os_error(28),
        ))),
        Ok(_) => Err(IntegratedError::Ledger(Error::UnsafeObject(
            "invalid integration hook error",
        ))),
        Err(std::env::VarError::NotPresent) => loop {
            std::thread::park();
        },
        Err(_) => Err(IntegratedError::Ledger(Error::UnsafeObject(
            "invalid integration hook environment",
        ))),
    }
}

#[cfg(not(feature = "adversarial-test-hooks"))]
fn integration_checkpoint(_stage: &str) -> Result<(), IntegratedError> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    fn root(tag: &str) -> std::path::PathBuf {
        let path = std::path::Path::new("/root").join(format!(
            ".trustforge-b-{tag}-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&path).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
        path
    }

    fn deadline() -> u64 {
        crate::linux::kernel_boottime_ns().unwrap() + 60_000_000_000
    }

    fn completed_via_capability_chain(
        sink: &ClaimSession<'_>,
        ctx: &trustforge_nf2_zero_capability_broker::capability::CapabilityContext,
    ) -> Result<Outcome, &'static str> {
        use trustforge_nf2_zero_capability_broker::CapabilitySink;
        use trustforge_nf2_zero_capability_broker::capability::{
            CapabilityDescriptor, CapabilityKind,
        };
        // Mirror the broker: build the live-bound descriptor from the
        // transaction-scoped context plus a runtime identity, then hand it by
        // reference so the ledger durably records its digest.
        let descriptor = CapabilityDescriptor::new(
            ctx.transaction_id,
            ctx.foundation_sha256,
            0xfd00,
            0x1,
            CapabilityKind::ZeroFd,
        );
        sink.on_ready_bound()?;
        sink.on_capability_issued(&descriptor)?;
        sink.on_derived_pending_recheck()?;
        sink.on_committed()?;
        Ok(Outcome::Completed)
    }

    #[test]
    fn success_commits_and_replay_never_executes() {
        let path = root("success");
        let runner = IntegratedRunner::provision_for_test(&path, &"44".repeat(32)).unwrap();
        let calls = AtomicU64::new(0);
        let tx = "11".repeat(32);
        let result = runner.execute_with(&tx, deadline(), |sink, ctx| {
            calls.fetch_add(1, Ordering::SeqCst);
            completed_via_capability_chain(sink, ctx)
        });
        assert!(result.is_ok());
        let replay = runner.execute_with(&"22".repeat(32), deadline(), |sink, ctx| {
            calls.fetch_add(1, Ordering::SeqCst);
            completed_via_capability_chain(sink, ctx)
        });
        assert!(replay.is_err());
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        std::fs::remove_dir_all(path).unwrap();
    }

    #[test]
    fn nf2_error_tombstones_and_replay_never_executes() {
        let path = root("error");
        let runner = IntegratedRunner::provision_for_test(&path, &"44".repeat(32)).unwrap();
        let calls = AtomicU64::new(0);
        let tx = "11".repeat(32);
        assert!(matches!(
            runner.execute_with(&tx, deadline(), |_sink, _ctx| {
                calls.fetch_add(1, Ordering::SeqCst);
                Err("injected NF2 failure")
            }),
            Err(IntegratedError::Nf2("injected NF2 failure"))
        ));
        assert!(
            runner
                .execute_with(&tx, deadline(), |_sink, _ctx| {
                    calls.fetch_add(1, Ordering::SeqCst);
                    Ok(Outcome::Completed)
                })
                .is_err()
        );
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        std::fs::remove_dir_all(path).unwrap();
    }

    #[test]
    fn dropped_session_after_action_is_recovered_without_retry() {
        let path = root("crash-window");
        let runner = IntegratedRunner::provision_for_test(&path, &"44".repeat(32)).unwrap();
        let request = Request {
            foundation_sha256: "33".repeat(32),
            operation: FIXED_OPERATION.into(),
            payload: FIXED_PAYLOAD.to_vec(),
            deadline_boottime_ns: deadline(),
        };
        let tx = "11".repeat(32);
        let session = runner.store.claim(&tx, request).unwrap();
        let calls = AtomicU64::new(0);
        calls.fetch_add(1, Ordering::SeqCst);
        drop(session);
        drop(runner);

        let reopened = IntegratedRunner {
            store: LedgerStore::open_for_test(&path).unwrap(),
        };
        assert!(
            reopened
                .execute_with(&tx, deadline(), |_sink, _ctx| {
                    calls.fetch_add(1, Ordering::SeqCst);
                    Ok(Outcome::Completed)
                })
                .is_err()
        );
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        std::fs::remove_dir_all(path).unwrap();
    }

    #[test]
    fn public_execute_surface_has_no_foundation_operation_or_payload() {
        let _: fn(&IntegratedRunner, &str, u64) -> Result<Binding, IntegratedError> =
            IntegratedRunner::execute;
    }
}
