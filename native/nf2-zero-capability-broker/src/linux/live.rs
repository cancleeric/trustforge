use super::sealed::SealedNf1;
use core::ffi::{c_char, c_int, c_long, c_void};
use std::ffi::CString;
use std::fs::File;
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::fs::MetadataExt;

const O_RDONLY: c_int = 0;
const O_CLOEXEC: c_int = 0o2000000;
const O_DIRECTORY: c_int = 0o200000;
const O_PATH: c_int = 0o10000000;
const RESOLVE_NO_MAGICLINKS: u64 = 0x02;
const RESOLVE_NO_SYMLINKS: u64 = 0x04;
const RESOLVE_BENEATH: u64 = 0x08;
const SYS_OPENAT2: c_long = 437;
const SYS_GETDENTS64: c_long = 217;
const SYS_GETSOCKOPT: c_long = 55;
const SOL_SOCKET: c_int = 1;
const SO_PEERCRED: c_int = 17;
const PROC_TEXT_MAX: usize = 64 * 1024;

#[repr(C)]
struct OpenHow {
    flags: u64,
    mode: u64,
    resolve: u64,
}

unsafe extern "C" {
    fn openat(directory: c_int, path: *const c_char, flags: c_int, ...) -> c_int;
    fn pread(fd: c_int, buffer: *mut u8, count: usize, offset: i64) -> isize;
    fn syscall(number: c_long, ...) -> c_long;
    fn geteuid() -> u32;
    fn getegid() -> u32;
}

#[derive(Clone, Copy, Eq, PartialEq)]
struct ProcIdentity {
    device: u64,
    inode: u64,
    owner: u32,
    group: u32,
}

/// Kernel-mediated peer identity captured over an AF_UNIX socket via
/// `SO_PEERCRED`. This is the fourth independent child-identity signal,
/// alongside pidfd (PID-reuse guard), /proc starttime, and the exe
/// device/inode. It is structurally distinct from the /proc-based checks
/// because it never touches the procfs at all.
///
/// Per unix(7), `SO_PEERCRED` returns "the credentials ... in effect at the
/// time of the call to connect(2), listen(2), or socketpair(2)". A socketpair
/// created in this broker would therefore carry the *broker's* credentials and
/// could never attest the child; the child must be the connector. `capture_*`
/// callers arrange exactly that.
#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub struct PeerCredential {
    pub pid: i32,
    pub uid: u32,
    pub gid: u32,
}

#[repr(C)]
struct Ucred {
    pid: i32,
    uid: u32,
    gid: u32,
}

pub struct LiveAuthority {
    proc_dir: File,
    proc_identity: ProcIdentity,
    executable: File,
    executable_identity: (u64, u64),
    starttime: String,
    #[cfg(feature = "adversarial-test-hooks")]
    injected_map_mismatch: bool,
}

impl LiveAuthority {
    pub fn verify_pre_exec_identity(
        pid: c_int,
        sealed: &SealedNf1,
        expected_runtime: (u64, u64),
    ) -> Result<(), &'static str> {
        let relative = format!("proc/{pid}");
        let proc_dir = open_beneath_directory(sealed.filesystem_root_fd(), &relative)?;
        let identity = proc_identity(&proc_dir)?;
        // SAFETY: identity queries have no preconditions.
        if identity.owner != unsafe { geteuid() } || identity.group != unsafe { getegid() } {
            return Err("pre-exec proc directory owner differs from broker child");
        }
        let fd_directory = open_proc_member(&proc_dir, "fd", O_RDONLY | O_DIRECTORY)?;
        if directory_names(&fd_directory)? != ["0", "1", "2", "3"] {
            return Err("pre-exec descriptor set mismatch");
        }
        let runtime = open_proc_member(&fd_directory, "3", O_PATH)?;
        let metadata = runtime
            .metadata()
            .map_err(|_| "pre-exec runtime descriptor stat failed")?;
        if (metadata.dev(), metadata.ino()) != expected_runtime {
            return Err("pre-exec runtime descriptor identity mismatch");
        }
        Ok(())
    }

    pub fn capture(pid: c_int, sealed: &SealedNf1) -> Result<Self, &'static str> {
        let relative = format!("proc/{pid}");
        let proc_dir = open_beneath_directory(sealed.filesystem_root_fd(), &relative)?;
        let proc_identity = proc_identity(&proc_dir)?;
        // SAFETY: identity queries have no preconditions.
        if proc_identity.owner != unsafe { geteuid() }
            || proc_identity.group != unsafe { getegid() }
        {
            return Err("proc directory owner differs from broker child");
        }
        let starttime = read_starttime(&proc_dir)?;
        let executable = open_proc_member(&proc_dir, "exe", O_PATH)?;
        let executable_metadata = executable
            .metadata()
            .map_err(|_| "live executable stat failed")?;
        let executable_identity = (executable_metadata.dev(), executable_metadata.ino());
        if executable_identity != sealed.runtime_device_inode() {
            return Err("live executable differs from retained runtime");
        }
        let authority = Self {
            proc_dir,
            proc_identity,
            executable,
            executable_identity,
            starttime,
            #[cfg(feature = "adversarial-test-hooks")]
            injected_map_mismatch: false,
        };
        authority.reverify(sealed)?;
        Ok(authority)
    }

    pub fn reverify(&self, sealed: &SealedNf1) -> Result<(), &'static str> {
        #[cfg(feature = "adversarial-test-hooks")]
        if self.injected_map_mismatch {
            return Err("mapped file outside retained runtime closure");
        }
        if proc_identity(&self.proc_dir)? != self.proc_identity
            || read_starttime(&self.proc_dir)? != self.starttime
        {
            return Err("live process identity changed");
        }
        let executable = self
            .executable
            .metadata()
            .map_err(|_| "live executable identity lost")?;
        if (executable.dev(), executable.ino()) != self.executable_identity
            || self.executable_identity != sealed.runtime_device_inode()
        {
            return Err("live executable substitution");
        }
        let status = read_proc_text(&self.proc_dir, "status")?;
        if !status.lines().any(|line| line == "NoNewPrivs:\t1") {
            return Err("live no-new-privileges readback mismatch");
        }
        let seccomp_count = status
            .lines()
            .filter(|line| line.starts_with("Seccomp:"))
            .count();
        if seccomp_count == 0 {
            return Err("live seccomp readback is absent");
        }
        if seccomp_count != 1 {
            return Err("live seccomp readback is duplicated");
        }
        if !exact_seccomp_status(&status) {
            let seccomp = status
                .lines()
                .find(|line| line.starts_with("Seccomp:"))
                .ok_or("live seccomp readback is absent")?;
            let fields: Vec<_> = seccomp.split_ascii_whitespace().collect();
            return match fields.as_slice() {
                ["Seccomp:", "0"] => Err("live seccomp readback reports disabled"),
                ["Seccomp:", "1"] => Err("live seccomp readback reports strict mode"),
                ["Seccomp:", "3"] => Err("live seccomp readback reports value 3"),
                ["Seccomp:", "4"] => Err("live seccomp readback reports value 4"),
                ["Seccomp:", "5"] => Err("live seccomp readback reports value 5"),
                ["Seccomp:", "6"] => Err("live seccomp readback reports value 6"),
                ["Seccomp:", "7"] => Err("live seccomp readback reports value 7"),
                ["Seccomp:", "8"] => Err("live seccomp readback reports value 8"),
                ["Seccomp:", "9"] => Err("live seccomp readback reports value 9"),
                ["Seccomp:", _] => Err("live seccomp readback value is unsupported"),
                ["Seccomp:", _, ..] => Err("live seccomp readback has extra tokens"),
                _ => Err("live seccomp readback is malformed"),
            };
        }
        if directory_names(&open_proc_member(
            &self.proc_dir,
            "fd",
            O_RDONLY | O_DIRECTORY,
        )?)? != ["0", "1", "2"]
        {
            return Err("live descriptor set mismatch");
        }
        let map_directory = open_proc_member(&self.proc_dir, "map_files", O_RDONLY | O_DIRECTORY)?;
        let names = directory_names(&map_directory)?;
        if names.is_empty() {
            return Err("live map_files closure unavailable");
        }
        for name in names {
            if !valid_map_name(&name) {
                return Err("map_files entry name malformed");
            }
            let mapping = open_proc_member(&map_directory, &name, O_PATH)?;
            let metadata = mapping.metadata().map_err(|_| "mapped file stat failed")?;
            if (metadata.dev(), metadata.ino()) != sealed.runtime_device_inode() {
                return Err("mapped file outside retained runtime closure");
            }
        }
        Ok(())
    }

    /// Reads the kernel peer credential of a connected AF_UNIX socket via the
    /// `SO_PEERCRED` option. The returned credentials reflect the process that
    /// *connected* the socket (see the `PeerCredential` docs), so the caller
    /// must ensure the child is the connector. `sock_fd` must be the broker's
    /// accepted end of such a connection.
    pub fn capture_peer_credential(sock_fd: c_int) -> Result<PeerCredential, &'static str> {
        let mut ucred = Ucred {
            pid: 0,
            uid: 0,
            gid: 0,
        };
        let mut optlen: u32 = std::mem::size_of::<Ucred>() as u32;
        // SAFETY: getsockopt writes a fixed 12-byte ucred into the sized buffer.
        let result = unsafe {
            syscall(
                SYS_GETSOCKOPT,
                sock_fd as c_long,
                SOL_SOCKET as c_long,
                SO_PEERCRED as c_long,
                &mut ucred as *mut Ucred as *mut c_void,
                &mut optlen as *mut u32,
            )
        };
        if result != 0 || optlen as usize != std::mem::size_of::<Ucred>() {
            return Err("peer credential capture failed");
        }
        Ok(PeerCredential {
            pid: ucred.pid,
            uid: ucred.uid,
            gid: ucred.gid,
        })
    }

    /// Cross-checks the kernel peer credential against the directly-forked
    /// child pid and the broker's own euid/egid (which the child inherits and
    /// which /proc also independently reports). `expected_pid` is the exact
    /// child pid already guarded against reuse by the retained pidfd, so a
    /// matching `cred.pid` is an independent confirmation rather than a
    /// tautology.
    pub fn verify_peer_credential(
        &self,
        sealed: &SealedNf1,
        cred: PeerCredential,
        expected_pid: c_int,
    ) -> Result<(), &'static str> {
        if cred.pid != expected_pid {
            return Err("peer credential pid mismatch");
        }
        // SAFETY: identity queries have no preconditions.
        let euid = unsafe { geteuid() };
        let egid = unsafe { getegid() };
        if cred.uid != euid || cred.gid != egid {
            return Err("peer credential identity mismatch");
        }
        // Cross-check the socket channel against the procfs-derived child
        // identity retained at capture time: the two paths must agree.
        if cred.uid != self.proc_identity.owner || cred.gid != self.proc_identity.group {
            return Err("peer credential diverges from proc identity");
        }
        let _ = sealed;
        Ok(())
    }

    #[cfg(feature = "adversarial-test-hooks")]
    pub fn inject_executable_identity_mismatch(&mut self) {
        self.executable_identity.1 ^= 1;
    }

    #[cfg(feature = "adversarial-test-hooks")]
    pub fn inject_map_identity_mismatch(&mut self) {
        self.injected_map_mismatch = true;
    }
}

fn valid_map_name(name: &str) -> bool {
    let Some((start, end)) = name.split_once('-') else {
        return false;
    };
    !start.is_empty()
        && !end.is_empty()
        && start.bytes().all(|byte| byte.is_ascii_hexdigit())
        && end.bytes().all(|byte| byte.is_ascii_hexdigit())
        && match (u64::from_str_radix(start, 16), u64::from_str_radix(end, 16)) {
            (Ok(start), Ok(end)) => start < end,
            _ => false,
        }
}

fn exact_status_field(line: &str, name: &str, value: &str) -> bool {
    let mut fields = line.split_ascii_whitespace();
    fields.next() == Some(name) && fields.next() == Some(value) && fields.next().is_none()
}

fn exact_seccomp_status(status: &str) -> bool {
    let mut lines = status.lines().filter(|line| line.starts_with("Seccomp:"));
    matches!(lines.next(), Some(line) if exact_status_field(line, "Seccomp:", "2"))
        && lines.next().is_none()
}

fn open_beneath_directory(root: c_int, relative: &str) -> Result<File, &'static str> {
    let relative = CString::new(relative).map_err(|_| "proc locator contains NUL")?;
    let how = OpenHow {
        flags: (O_PATH | O_DIRECTORY | O_CLOEXEC) as u64,
        mode: 0,
        resolve: RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS,
    };
    // SAFETY: openat2 receives retained root and initialized fixed arguments.
    let fd = unsafe {
        syscall(
            SYS_OPENAT2,
            root,
            relative.as_ptr(),
            &how,
            std::mem::size_of::<OpenHow>(),
        )
    } as c_int;
    owned(fd, "retained proc directory unavailable")
}

fn open_proc_member(directory: &File, name: &str, flags: c_int) -> Result<File, &'static str> {
    let name = CString::new(name).map_err(|_| "proc member contains NUL")?;
    // SAFETY: openat is relative to a retained proc/map directory descriptor.
    let fd = unsafe { openat(directory.as_raw_fd(), name.as_ptr(), flags | O_CLOEXEC, 0) };
    owned(fd, "retained proc member unavailable")
}

fn owned(fd: c_int, error: &'static str) -> Result<File, &'static str> {
    if fd < 0 {
        return Err(error);
    }
    // SAFETY: successful open returns a newly owned descriptor.
    Ok(unsafe { File::from_raw_fd(fd) })
}

fn proc_identity(file: &File) -> Result<ProcIdentity, &'static str> {
    let metadata = file.metadata().map_err(|_| "proc identity stat failed")?;
    Ok(ProcIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
        owner: metadata.uid(),
        group: metadata.gid(),
    })
}

fn read_starttime(proc_dir: &File) -> Result<String, &'static str> {
    let stat = read_proc_text(proc_dir, "stat")?;
    let end = stat.rfind(')').ok_or("proc stat comm malformed")?;
    stat[end + 2..]
        .split_whitespace()
        .nth(19)
        .map(str::to_owned)
        .ok_or("proc starttime absent")
}

fn read_proc_text(directory: &File, name: &str) -> Result<String, &'static str> {
    let file = open_proc_member(directory, name, O_RDONLY)?;
    let mut bytes = vec![0_u8; PROC_TEXT_MAX + 1];
    // SAFETY: output buffer is writable and fixed-bounded.
    let count = unsafe { pread(file.as_raw_fd(), bytes.as_mut_ptr(), bytes.len(), 0) };
    if count < 0 || count as usize > PROC_TEXT_MAX {
        return Err("proc text missing or exceeds bound");
    }
    bytes.truncate(count as usize);
    String::from_utf8(bytes).map_err(|_| "proc text is not UTF-8")
}

fn directory_names(directory: &File) -> Result<Vec<String>, &'static str> {
    let mut names = Vec::new();
    let mut buffer = [0_u8; 8192];
    loop {
        // SAFETY: getdents64 writes at most the fixed buffer size.
        let count = unsafe {
            syscall(
                SYS_GETDENTS64,
                directory.as_raw_fd(),
                buffer.as_mut_ptr(),
                buffer.len(),
            )
        };
        if count < 0 {
            return Err("proc directory enumeration failed");
        }
        if count == 0 {
            break;
        }
        let mut offset = 0_usize;
        while offset < count as usize {
            let record = &buffer[offset..count as usize];
            if record.len() < 19 {
                return Err("getdents record truncated");
            }
            let record_length = u16::from_ne_bytes([record[16], record[17]]) as usize;
            if record_length < 20 || offset + record_length > count as usize {
                return Err("getdents record malformed");
            }
            let name_bytes = &record[19..record_length];
            let end = name_bytes
                .iter()
                .position(|byte| *byte == 0)
                .ok_or("getdents name unterminated")?;
            let name =
                std::str::from_utf8(&name_bytes[..end]).map_err(|_| "getdents name invalid")?;
            if name != "." && name != ".." {
                names.push(name.to_owned());
                if names.len() > 4096 {
                    return Err("proc directory cardinality exceeds bound");
                }
            }
            offset += record_length;
        }
    }
    names.sort();
    Ok(names)
}

#[cfg(test)]
mod tests {
    #[test]
    fn map_name_grammar_is_exact() {
        assert!(super::valid_map_name("400000-401000"));
        for invalid in ["", "400000", "-401000", "401000-400000", "zz-ff", "1-1"] {
            assert!(!super::valid_map_name(invalid), "{invalid}");
        }
    }

    #[test]
    fn proc_status_field_allows_kernel_spacing_but_not_extra_tokens() {
        assert!(super::exact_status_field("Seccomp:\t2", "Seccomp:", "2"));
        assert!(super::exact_status_field(
            "Seccomp:        2",
            "Seccomp:",
            "2"
        ));
        for invalid in [
            "Seccomp: 0",
            "Seccomp: 1",
            "Seccomp: 20",
            "Seccomp: 2 extra",
            "Seccomp2: 2",
        ] {
            assert!(!super::exact_status_field(invalid, "Seccomp:", "2"));
        }
    }

    #[test]
    fn seccomp_status_requires_exactly_one_valid_field() {
        for valid in ["Name:\tchild\nSeccomp:\t2\n", "Seccomp:        2\n"] {
            assert!(super::exact_seccomp_status(valid));
        }
        for invalid in [
            "",
            "Seccomp:\t2\nSeccomp:\t2\n",
            "Seccomp:\t2\nSeccomp:\t0\n",
        ] {
            assert!(!super::exact_seccomp_status(invalid));
        }
    }
}
