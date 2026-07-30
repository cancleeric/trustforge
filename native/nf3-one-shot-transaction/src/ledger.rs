use crate::Error;
use crate::linux::{Dir, StoreLockGuard, Vfs, kernel_boottime_ns, kernel_pid};
use crate::sha256::{digest, hex};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::{marker::PhantomData, rc::Rc};

const RECORD_MAX: usize = 4096;
const STORE_MAX_ENTRIES: usize = 1024;
// Reserve burn, PREPARED, CLAIMED, terminal, and poison before accepting work.
const RESERVED_RECOVERY_SLOTS: usize = 5;
const MAX_FUTURE_NS: u64 = 300_000_000_000;
const PRODUCTION_ROOT: &str = "/var/lib/trustforge/native-foundation/nf3";
const ZERO: &str = "0000000000000000000000000000000000000000000000000000000000000000";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum State {
    Prepared,
    Claimed,
    Committed,
    Tombstoned,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Binding {
    pub transaction_id: String,
    pub request_sha256: String,
    pub foundation_sha256: String,
    pub boot_id: String,
    pub deadline_boottime_ns: u64,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Request {
    pub foundation_sha256: String,
    pub operation: String,
    pub payload: Vec<u8>,
    pub deadline_boottime_ns: u64,
}

pub struct LedgerStore {
    locks: Dir,
    burns: Dir,
    heads: Dir,
    poison: Dir,
    store_id: String,
    failed_closed: AtomicBool,
}

pub struct ClaimSession<'a> {
    store: &'a LedgerStore,
    binding: Binding,
    lock: Option<StoreLockGuard<'a>>,
    terminal: bool,
    creator_pid: u32,
    _not_send_sync: PhantomData<Rc<()>>,
}

impl LedgerStore {
    pub fn provision(store_id: &str) -> Result<Self, Error> {
        Self::provision_at(Path::new(PRODUCTION_ROOT), store_id)
    }
    #[cfg(any(test, feature = "adversarial-test-hooks"))]
    pub fn provision_for_test(root: &Path, store_id: &str) -> Result<Self, Error> {
        Self::provision_at(root, store_id)
    }
    fn provision_at(root: &Path, store_id: &str) -> Result<Self, Error> {
        valid_hex(store_id)?;
        let vfs = Vfs::open(root)?;
        let provision_lock = match vfs.root().lock("provision.lock") {
            Ok(lock) => lock,
            Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => {
                vfs.root().create_new("provision.lock", b"", 0)?;
                vfs.root().lock("provision.lock")?
            }
            Err(error) => return Err(error),
        };
        for name in ["locks", "burns", "heads", "poison"] {
            match vfs.root().open_dir(name) {
                Ok(_) => {}
                Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => {
                    vfs.root().mkdir(name)?;
                }
                Err(error) => return Err(error),
            }
        }
        match vfs
            .root()
            .create_new("store-id", format!("{store_id}\n").as_bytes(), 65)
        {
            Ok(_) => {}
            Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                let existing = vfs.root().read("store-id", 65)?;
                if existing != format!("{store_id}\n").as_bytes() {
                    return Err(Error::UnsafeObject("store id mismatch"));
                }
            }
            Err(error) => return Err(error),
        }
        validate_root_names(&vfs.root().safe_names()?)?;
        provision_lock.revalidate()?;
        drop(provision_lock);
        Self::open_at(root)
    }

    pub fn open() -> Result<Self, Error> {
        Self::open_at(Path::new(PRODUCTION_ROOT))
    }
    #[cfg(any(test, feature = "adversarial-test-hooks"))]
    pub fn open_for_test(root: &Path) -> Result<Self, Error> {
        Self::open_at(root)
    }
    fn open_at(root: &Path) -> Result<Self, Error> {
        let vfs = Vfs::open(root)?;
        validate_root_names(&vfs.root().safe_names()?)?;
        let bytes = vfs.root().read("store-id", 65)?;
        let store_id = std::str::from_utf8(&bytes)
            .map_err(|_| Error::UnsafeObject("store id utf8"))?
            .strip_suffix('\n')
            .ok_or(Error::UnsafeObject("store id newline"))?
            .to_owned();
        valid_hex(&store_id)?;
        let store = Self {
            locks: vfs.root().open_dir("locks")?,
            burns: vfs.root().open_dir("burns")?,
            heads: vfs.root().open_dir("heads")?,
            poison: vfs.root().open_dir("poison")?,
            store_id,
            failed_closed: AtomicBool::new(false),
        };
        {
            let lock = store.locks.lock("global.lock")?;
            if let Err(error) = store.recover_locked(&lock) {
                store.poison_or_latch(&lock)?;
                return Err(error);
            }
        }
        Ok(store)
    }

    pub fn claim(&self, transaction_id: &str, request: Request) -> Result<ClaimSession<'_>, Error> {
        if self.failed_closed.load(Ordering::SeqCst) {
            return Err(Error::UnsafeObject("STORE_POISONED"));
        }
        valid_hex(transaction_id)?;
        let canonical = canonical_request(&request)?;
        let replay = canonical_replay_identity(&request)?;
        let mut binding = Binding {
            transaction_id: transaction_id.into(),
            request_sha256: request_digest(&replay)?,
            foundation_sha256: request.foundation_sha256,
            boot_id: String::new(),
            deadline_boottime_ns: request.deadline_boottime_ns,
        };
        let lock = self.locks.lock("global.lock")?;
        binding.boot_id = kernel_boot_id()?;
        let now_boottime_ns = internal_boottime_ns()?;
        validate_binding(&binding)?;
        if now_boottime_ns >= binding.deadline_boottime_ns {
            return Err(Error::UnsafeObject("stale request"));
        }
        if binding
            .deadline_boottime_ns
            .checked_sub(now_boottime_ns)
            .ok_or(Error::UnsafeObject("stale request"))?
            > MAX_FUTURE_NS
        {
            return Err(Error::UnsafeObject("deadline too far"));
        }
        if let Err(error) = self.recover_locked(&lock) {
            self.poison_or_latch(&lock)?;
            return Err(error);
        }
        self.reserve_claim_capacity(&lock)?;
        if self.checked_records(&lock)?.iter().any(|record| {
            record.binding.transaction_id == binding.transaction_id
                && record.binding.request_sha256 != binding.request_sha256
        }) {
            return Err(Error::UnsafeObject("transaction id reused"));
        }
        let burn_name = format!("{}.burn", binding.request_sha256);
        let burn = format!(
            "v1\nrequest={}\ntransaction={}\nstore={}\nlength={}\ncanonical={}\nreplay_length={}\nreplay={}\n",
            binding.request_sha256,
            binding.transaction_id,
            self.store_id,
            canonical.len(),
            hex(&canonical),
            replay.len(),
            hex(&replay)
        );
        match lock.create_in(&self.burns, &burn_name, burn.as_bytes(), RECORD_MAX) {
            Ok(_) => {}
            Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                return Err(Error::UnsafeObject("request already burned"));
            }
            Err(error) => {
                self.poison_or_latch(&lock)?;
                return Err(error);
            }
        }
        if let Err(error) = self
            .append(&lock, &binding, State::Prepared)
            .and_then(|_| self.append(&lock, &binding, State::Claimed))
        {
            self.poison_or_latch(&lock)?;
            return Err(error);
        }
        recheck_kernel(&binding)?;
        Ok(ClaimSession {
            store: self,
            binding,
            lock: Some(lock),
            terminal: false,
            creator_pid: kernel_pid(),
            _not_send_sync: PhantomData,
        })
    }

    fn recover_locked(&self, lock: &StoreLockGuard<'_>) -> Result<(), Error> {
        if !lock.entries_in(&self.poison)?.is_empty() {
            return Err(Error::UnsafeObject("STORE_POISONED"));
        }
        let records = self.checked_records(lock)?;
        for entry in lock.entries_in(&self.burns)? {
            if !entry.name.ends_with(".burn") {
                return Err(Error::UnsafeObject("unknown burn entry"));
            }
            let bytes = lock.read_in(&self.burns, &entry.name, RECORD_MAX)?;
            let burn = parse_burn(&bytes, &self.store_id)?;
            if records
                .iter()
                .any(|r| r.binding.transaction_id == burn.1 && r.binding.request_sha256 != burn.0)
            {
                return Err(Error::UnsafeObject("transaction/request confusion"));
            }
            match records
                .iter()
                .rev()
                .find(|r| r.binding.transaction_id == burn.1 && r.binding.request_sha256 == burn.0)
            {
                None => {
                    let binding = Binding {
                        transaction_id: burn.1,
                        request_sha256: burn.0,
                        foundation_sha256: ZERO.into(),
                        boot_id: "recovered".into(),
                        deadline_boottime_ns: 1,
                    };
                    self.append(lock, &binding, State::Tombstoned)?;
                }
                Some(record) if matches!(record.state, State::Prepared | State::Claimed) => {
                    self.append(lock, &record.binding, State::Tombstoned)?
                }
                _ => {}
            }
        }
        Ok(())
    }

    fn records(&self, _lock: &StoreLockGuard<'_>) -> Result<Vec<Record>, Error> {
        let entries = _lock.entries_in(&self.heads)?;
        let mut records: Vec<Record> = Vec::new();
        let mut previous = ZERO.to_owned();
        for (sequence, entry) in entries.iter().enumerate() {
            let bytes = _lock.read_in(&self.heads, &entry.name, RECORD_MAX)?;
            let record = parse_record(&bytes)?;
            let hash = hash_bytes(&bytes, RECORD_MAX)?;
            let expected = format!("{sequence:08}-{hash}.record");
            if entry.name != expected
                || record.sequence != sequence as u64
                || record.previous_sha256 != previous
                || record.store_id != self.store_id
            {
                return Err(Error::UnsafeObject("STORE_POISONED"));
            }
            if let Some(prior) = records
                .iter()
                .rev()
                .find(|prior| prior.binding.transaction_id == record.binding.transaction_id)
            {
                let legal = matches!(
                    (prior.state, record.state),
                    (State::Prepared, State::Claimed)
                        | (State::Prepared, State::Tombstoned)
                        | (State::Claimed, State::Committed)
                        | (State::Claimed, State::Tombstoned)
                );
                if !legal {
                    return Err(Error::UnsafeObject("STORE_POISONED"));
                }
            } else if !matches!(record.state, State::Prepared | State::Tombstoned) {
                return Err(Error::UnsafeObject("STORE_POISONED"));
            }
            previous = hash;
            records.push(record);
        }
        Ok(records)
    }

    fn checked_records(&self, lock: &StoreLockGuard<'_>) -> Result<Vec<Record>, Error> {
        match self.records(lock) {
            Ok(records) => Ok(records),
            Err(error) => {
                self.poison_or_latch(lock)?;
                Err(error)
            }
        }
    }

    fn poison_or_latch(&self, lock: &StoreLockGuard<'_>) -> Result<(), Error> {
        if self.failed_closed.swap(true, Ordering::SeqCst) {
            return Ok(());
        }
        match lock.create_in(&self.poison, "store.poison", b"v1\nSTORE_POISONED\n", 32) {
            Ok(_) => Ok(()),
            Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::AlreadyExists => Ok(()),
            Err(_) => Err(Error::UnsafeObject("STORE_POISONED")),
        }
    }

    fn reserve_claim_capacity(&self, lock: &StoreLockGuard<'_>) -> Result<(), Error> {
        let poison_count = lock.entries_in(&self.poison)?.len();
        let used = lock
            .entries_in(&self.burns)?
            .len()
            .checked_add(lock.entries_in(&self.heads)?.len())
            .and_then(|v| v.checked_add(poison_count))
            .ok_or(Error::UnsafeObject("store quota overflow"))?;
        if used > STORE_MAX_ENTRIES.saturating_sub(RESERVED_RECOVERY_SLOTS) {
            return Err(Error::UnsafeObject("store quota exhausted"));
        }
        Ok(())
    }

    fn append(
        &self,
        lock: &StoreLockGuard<'_>,
        binding: &Binding,
        state: State,
    ) -> Result<(), Error> {
        let records = self.checked_records(lock)?;
        let sequence = records.len() as u64;
        let previous = records
            .last()
            .map(|r| r.hash.clone())
            .unwrap_or_else(|| ZERO.into());
        let bytes = encode_record(sequence, state, binding, &self.store_id, &previous);
        let hash = hash_bytes(&bytes, RECORD_MAX)?;
        lock.create_in(
            &self.heads,
            &format!("{sequence:08}-{hash}.record"),
            &bytes,
            RECORD_MAX,
        )?;
        let reread = self.checked_records(lock)?;
        if reread.len() != sequence as usize + 1 {
            return Err(Error::UnsafeObject("STORE_POISONED"));
        }
        Ok(())
    }
}

impl ClaimSession<'_> {
    pub fn binding(&self) -> &Binding {
        &self.binding
    }
    pub fn commit(mut self) -> Result<(), Error> {
        self.finish(State::Committed)
    }
    pub fn tombstone(mut self) -> Result<(), Error> {
        self.finish(State::Tombstoned)
    }
    fn finish(&mut self, state: State) -> Result<(), Error> {
        if kernel_pid() != self.creator_pid {
            let lock = self
                .lock
                .as_ref()
                .ok_or(Error::UnsafeObject("session terminal"))?;
            self.store.poison_or_latch(lock)?;
            return Err(Error::UnsafeObject("creator pid changed"));
        }
        if state == State::Committed {
            recheck_kernel(&self.binding)?;
        }
        let lock = self
            .lock
            .as_ref()
            .ok_or(Error::UnsafeObject("session terminal"))?;
        self.store.append(lock, &self.binding, state)?;
        if lock.revalidate().is_err() {
            self.store.failed_closed.store(true, Ordering::SeqCst);
            return Err(Error::UnsafeObject("STORE_POISONED"));
        }
        self.terminal = true;
        self.lock.take();
        Ok(())
    }
}

impl Drop for ClaimSession<'_> {
    fn drop(&mut self) {
        if kernel_pid() != self.creator_pid {
            if let Some(lock) = self.lock.as_ref() {
                let _ = self.store.poison_or_latch(lock);
            }
            return;
        }
        if !self.terminal
            && let Some(lock) = self.lock.as_ref()
            && self
                .store
                .append(lock, &self.binding, State::Tombstoned)
                .is_err()
        {
            let marker = format!("{}.poison", self.binding.transaction_id);
            if lock
                .create_in(&self.store.poison, &marker, b"v1\nSTORE_POISONED\n", 32)
                .is_err()
            {
                self.store.failed_closed.store(true, Ordering::SeqCst);
            }
        }
    }
}

#[derive(Clone)]
struct Record {
    sequence: u64,
    state: State,
    binding: Binding,
    store_id: String,
    previous_sha256: String,
    hash: String,
}

fn encode_record(seq: u64, state: State, b: &Binding, store: &str, previous: &str) -> Vec<u8> {
    format!("v1\nsequence={seq}\nstate={}\ntransaction={}\nrequest={}\nfoundation={}\nboot={}\ndeadline={}\nstore={store}\nprevious={previous}\n",
        state_name(state), b.transaction_id, b.request_sha256, b.foundation_sha256,
        b.boot_id, b.deadline_boottime_ns).into_bytes()
}
fn parse_record(bytes: &[u8]) -> Result<Record, Error> {
    let text = std::str::from_utf8(bytes).map_err(|_| Error::UnsafeObject("record utf8"))?;
    let mut lines = text
        .strip_suffix('\n')
        .ok_or(Error::UnsafeObject("record newline"))?
        .lines();
    if lines.next() != Some("v1") {
        return Err(Error::UnsafeObject("record version"));
    }
    let seq = value(&mut lines, "sequence")?
        .parse()
        .map_err(|_| Error::UnsafeObject("sequence"))?;
    let state = match value(&mut lines, "state")? {
        "PREPARED" => State::Prepared,
        "CLAIMED" => State::Claimed,
        "COMMITTED" => State::Committed,
        "TOMBSTONED" => State::Tombstoned,
        _ => return Err(Error::UnsafeObject("state")),
    };
    let transaction_id = value(&mut lines, "transaction")?.to_owned();
    let request_sha256 = value(&mut lines, "request")?.to_owned();
    let foundation_sha256 = value(&mut lines, "foundation")?.to_owned();
    let boot_id = value(&mut lines, "boot")?.to_owned();
    let deadline_boottime_ns = value(&mut lines, "deadline")?
        .parse()
        .map_err(|_| Error::UnsafeObject("deadline"))?;
    let store_id = value(&mut lines, "store")?.to_owned();
    let previous_sha256 = value(&mut lines, "previous")?.to_owned();
    if lines.next().is_some() {
        return Err(Error::UnsafeObject("record fields"));
    }
    let binding = Binding {
        transaction_id,
        request_sha256,
        foundation_sha256,
        boot_id,
        deadline_boottime_ns,
    };
    validate_binding(&binding)?;
    valid_hex(&store_id)?;
    valid_hex(&previous_sha256)?;
    Ok(Record {
        sequence: seq,
        state,
        binding,
        store_id,
        previous_sha256,
        hash: hash_bytes(bytes, RECORD_MAX)?,
    })
}
fn value<'a>(lines: &mut impl Iterator<Item = &'a str>, key: &str) -> Result<&'a str, Error> {
    lines
        .next()
        .and_then(|line| line.strip_prefix(&format!("{key}=")))
        .ok_or(Error::UnsafeObject("record field"))
}
fn parse_burn(bytes: &[u8], store: &str) -> Result<(String, String), Error> {
    let text = std::str::from_utf8(bytes).map_err(|_| Error::UnsafeObject("burn utf8"))?;
    let mut l = text
        .strip_suffix('\n')
        .ok_or(Error::UnsafeObject("burn newline"))?
        .lines();
    if l.next() != Some("v1") {
        return Err(Error::UnsafeObject("burn version"));
    }
    let request = value(&mut l, "request")?.to_owned();
    let tx = value(&mut l, "transaction")?.to_owned();
    if value(&mut l, "store")? != store {
        return Err(Error::UnsafeObject("burn store"));
    }
    let length: usize = value(&mut l, "length")?
        .parse()
        .map_err(|_| Error::UnsafeObject("burn length"))?;
    let canonical = decode_hex(value(&mut l, "canonical")?)?;
    let replay_length: usize = value(&mut l, "replay_length")?
        .parse()
        .map_err(|_| Error::UnsafeObject("burn replay length"))?;
    let replay = decode_hex(value(&mut l, "replay")?)?;
    if canonical.len() != length
        || replay.len() != replay_length
        || request_digest(&replay)? != request
        || l.next().is_some()
    {
        return Err(Error::UnsafeObject("burn canonical"));
    }
    valid_hex(&request)?;
    valid_hex(&tx)?;
    Ok((request, tx))
}
fn validate_binding(b: &Binding) -> Result<(), Error> {
    valid_hex(&b.transaction_id)?;
    valid_hex(&b.request_sha256)?;
    valid_hex(&b.foundation_sha256)?;
    if b.boot_id.is_empty()
        || b.boot_id.len() > 128
        || !b
            .boot_id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"-_.".contains(&c))
    {
        return Err(Error::UnsafeObject("boot id"));
    }
    if b.deadline_boottime_ns == 0 {
        return Err(Error::UnsafeObject("deadline"));
    }
    Ok(())
}
fn valid_hex(v: &str) -> Result<(), Error> {
    if v.len() != 64
        || !v
            .bytes()
            .all(|b| b.is_ascii_digit() || matches!(b, b'a'..=b'f'))
    {
        return Err(Error::UnsafeObject("hex"));
    }
    Ok(())
}
fn state_name(s: State) -> &'static str {
    match s {
        State::Prepared => "PREPARED",
        State::Claimed => "CLAIMED",
        State::Committed => "COMMITTED",
        State::Tombstoned => "TOMBSTONED",
    }
}
fn validate_root_names(names: &[String]) -> Result<(), Error> {
    let expected = [
        "burns",
        "heads",
        "locks",
        "poison",
        "provision.lock",
        "store-id",
    ];
    if names.len() != expected.len() || names.iter().map(String::as_str).ne(expected) {
        return Err(Error::UnsafeObject("unknown store root entry"));
    }
    Ok(())
}
fn canonical_request(r: &Request) -> Result<Vec<u8>, Error> {
    valid_hex(&r.foundation_sha256)?;
    if r.operation.is_empty()
        || r.operation.len() > 64
        || !r
            .operation
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
        || r.payload.len() > 1024
        || r.deadline_boottime_ns == 0
    {
        return Err(Error::UnsafeObject("request bounds"));
    }
    Ok(format!(
        "v1\nfoundation={}\noperation={}\npayload={}\ndeadline={}\n",
        r.foundation_sha256,
        r.operation,
        hex(&r.payload),
        r.deadline_boottime_ns
    )
    .into_bytes())
}
fn canonical_replay_identity(r: &Request) -> Result<Vec<u8>, Error> {
    valid_hex(&r.foundation_sha256)?;
    if r.operation.is_empty()
        || r.operation.len() > 64
        || !r
            .operation
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
        || r.payload.len() > 1024
    {
        return Err(Error::UnsafeObject("request bounds"));
    }
    Ok(format!(
        "v1\nfoundation={}\noperation={}\npayload={}\n",
        r.foundation_sha256,
        r.operation,
        hex(&r.payload)
    )
    .into_bytes())
}
fn request_digest(c: &[u8]) -> Result<String, Error> {
    let mut input = b"trustforge:nf3:request:v1\0".to_vec();
    input.extend_from_slice(&(c.len() as u64).to_be_bytes());
    input.extend_from_slice(c);
    hash_bytes(&input, 4096)
}
fn hash_bytes(bytes: &[u8], max: usize) -> Result<String, Error> {
    Ok(hex(&digest(bytes, max).map_err(Error::UnsafeObject)?))
}
fn decode_hex(v: &str) -> Result<Vec<u8>, Error> {
    if !v.len().is_multiple_of(2) || v.len() > 4096 {
        return Err(Error::UnsafeObject("hex bytes"));
    }
    v.as_bytes()
        .chunks_exact(2)
        .map(|p| {
            u8::from_str_radix(
                std::str::from_utf8(p).map_err(|_| Error::UnsafeObject("hex bytes"))?,
                16,
            )
            .map_err(|_| Error::UnsafeObject("hex bytes"))
        })
        .collect()
}
fn kernel_boot_id() -> Result<String, Error> {
    let text = std::fs::read_to_string("/proc/sys/kernel/random/boot_id")?;
    let value = text.strip_suffix('\n').unwrap_or(&text);
    if value.len() != 36 || !value.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-') {
        return Err(Error::UnsafeObject("kernel boot id"));
    }
    Ok(value.to_owned())
}
fn recheck_kernel(b: &Binding) -> Result<(), Error> {
    if kernel_boot_id()? != b.boot_id || internal_boottime_ns()? >= b.deadline_boottime_ns {
        return Err(Error::UnsafeObject("request expired"));
    }
    Ok(())
}
#[cfg(test)]
static TEST_BOOTTIME_NS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
fn internal_boottime_ns() -> Result<u64, Error> {
    #[cfg(test)]
    {
        let injected = TEST_BOOTTIME_NS.load(Ordering::SeqCst);
        if injected != 0 {
            return Ok(injected);
        }
    }
    kernel_boottime_ns()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    static TEST_CLOCK_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    #[test]
    fn canonical_record_round_trip_and_extra_field_rejected() {
        let binding = Binding {
            transaction_id: "11".repeat(32),
            request_sha256: "22".repeat(32),
            foundation_sha256: "33".repeat(32),
            boot_id: "boot-A".into(),
            deadline_boottime_ns: 42,
        };
        let bytes = encode_record(0, State::Prepared, &binding, &"44".repeat(32), ZERO);
        assert_eq!(parse_record(&bytes).unwrap().binding, binding);
        let mut extra = bytes;
        extra.extend_from_slice(b"extra=x\n");
        assert!(parse_record(&extra).is_err());
    }

    #[test]
    fn binding_validation_is_strict() {
        let binding = Binding {
            transaction_id: "AA".repeat(32),
            request_sha256: "22".repeat(32),
            foundation_sha256: "33".repeat(32),
            boot_id: "bad boot".into(),
            deadline_boottime_ns: 0,
        };
        assert!(validate_binding(&binding).is_err());
    }

    #[test]
    fn claim_commit_and_request_burn_are_durable() {
        let _clock = TEST_CLOCK_LOCK.lock().unwrap();
        if crate::linux::kernel_boottime_ns().is_err() {
            return;
        }
        let path = Path::new("/root").join(format!(
            ".trustforge-a2-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&path).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
        TEST_BOOTTIME_NS.store(100, Ordering::SeqCst);
        let store = LedgerStore::provision_for_test(&path, &"44".repeat(32)).unwrap();
        let request = Request {
            foundation_sha256: "33".repeat(32),
            operation: "execute".into(),
            payload: b"canonical".to_vec(),
            deadline_boottime_ns: 200,
        };
        store
            .claim(&"11".repeat(32), request.clone())
            .unwrap()
            .commit()
            .unwrap();
        let mut changed_deadline = request;
        changed_deadline.deadline_boottime_ns = 201;
        assert!(store.claim(&"22".repeat(32), changed_deadline).is_err());
        let base = Request {
            foundation_sha256: "33".repeat(32),
            operation: "execute".into(),
            payload: b"other".to_vec(),
            deadline_boottime_ns: 100,
        };
        assert!(store.claim(&"55".repeat(32), base.clone()).is_err());
        let mut future = base;
        future.deadline_boottime_ns = 100 + MAX_FUTURE_NS + 1;
        assert!(store.claim(&"66".repeat(32), future).is_err());
        let boundary = Request {
            foundation_sha256: "33".repeat(32),
            operation: "execute".into(),
            payload: b"boundary".to_vec(),
            deadline_boottime_ns: 100 + MAX_FUTURE_NS,
        };
        store
            .claim(&"77".repeat(32), boundary)
            .unwrap()
            .tombstone()
            .unwrap();
        drop(store);
        assert!(LedgerStore::open_for_test(&path).is_ok());
        TEST_BOOTTIME_NS.store(0, Ordering::SeqCst);
        std::fs::remove_dir_all(path).unwrap();
    }

    #[test]
    fn corrupt_unknown_head_persists_poison() {
        let path = test_root("poison");
        let store = LedgerStore::provision_for_test(&path, &"44".repeat(32)).unwrap();
        drop(store);
        std::fs::write(path.join("heads/unknown"), b"bad\n").unwrap();
        std::fs::set_permissions(
            path.join("heads/unknown"),
            std::fs::Permissions::from_mode(0o600),
        )
        .unwrap();
        assert!(LedgerStore::open_for_test(&path).is_err());
        assert!(path.join("poison/store.poison").exists());
        std::fs::remove_dir_all(path).unwrap();
    }

    #[test]
    fn missing_head_gap_persists_poison() {
        let _clock = TEST_CLOCK_LOCK.lock().unwrap();
        let path = test_root("gap");
        TEST_BOOTTIME_NS.store(100, Ordering::SeqCst);
        let store = LedgerStore::provision_for_test(&path, &"44".repeat(32)).unwrap();
        store
            .claim(
                &"11".repeat(32),
                Request {
                    foundation_sha256: "33".repeat(32),
                    operation: "execute".into(),
                    payload: b"gap".to_vec(),
                    deadline_boottime_ns: 200,
                },
            )
            .unwrap()
            .commit()
            .unwrap();
        drop(store);
        let mut heads = std::fs::read_dir(path.join("heads"))
            .unwrap()
            .map(|e| e.unwrap().path())
            .collect::<Vec<_>>();
        heads.sort();
        std::fs::remove_file(&heads[0]).unwrap();
        assert!(LedgerStore::open_for_test(&path).is_err());
        assert!(path.join("poison/store.poison").exists());
        TEST_BOOTTIME_NS.store(0, Ordering::SeqCst);
        std::fs::remove_dir_all(path).unwrap();
    }

    #[test]
    fn quota_reserves_recovery_slots() {
        let path = test_root("quota");
        let store = LedgerStore::provision_for_test(&path, &"44".repeat(32)).unwrap();
        let lock = store.locks.lock("global.lock").unwrap();
        for index in 0..STORE_MAX_ENTRIES - RESERVED_RECOVERY_SLOTS {
            lock.create_in(&store.burns, &format!("quota-{index:04}"), b"", 0)
                .unwrap();
        }
        assert!(store.reserve_claim_capacity(&lock).is_ok());
        lock.create_in(&store.burns, "quota-over", b"", 0).unwrap();
        assert!(store.reserve_claim_capacity(&lock).is_err());
        drop(lock);
        drop(store);
        std::fs::remove_dir_all(path).unwrap();
    }

    fn test_root(tag: &str) -> std::path::PathBuf {
        let path = Path::new("/root").join(format!(
            ".trustforge-a2-{tag}-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&path).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
        path
    }
}
