use super::live::{LiveAuthority, PeerCredential};
use super::sealed::SealedNf1;
use core::ffi::{c_char, c_int, c_long, c_void};
use std::os::fd::{FromRawFd, OwnedFd, RawFd};
#[cfg(feature = "adversarial-test-hooks")]
use std::os::unix::fs::MetadataExt;
use std::time::{Duration, Instant};

const O_CLOEXEC: c_int = 0o2000000;
const F_DUPFD_CLOEXEC: c_int = 1030;
const SIGKILL: c_int = 9;
const SIGSTOP: c_int = 19;
const SIGTRAP: c_int = 5;
const WNOHANG: c_int = 1;
const WEXITED: c_int = 4;
const P_PIDFD: c_int = 3;
const PR_SET_PDEATHSIG: c_int = 1;
const PR_GET_PDEATHSIG: c_int = 2;
const PR_SET_NO_NEW_PRIVS: c_int = 38;
const PR_GET_NO_NEW_PRIVS: c_int = 39;
const PR_GET_SECCOMP: c_int = 21;
const PTRACE_TRACEME: c_int = 0;
const PTRACE_PEEKDATA: c_int = 2;
const PTRACE_CONT: c_int = 7;
const PTRACE_GETREGS: c_int = 12;
const PTRACE_SETOPTIONS: c_int = 0x4200;
const PTRACE_O_TRACEEXEC: usize = 0x10;
const PTRACE_O_TRACEEXIT: usize = 0x40;
const PTRACE_O_TRACESECCOMP: usize = 0x80;
const PTRACE_O_EXITKILL: usize = 0x0010_0000;
const PTRACE_EVENT_EXEC: c_int = 4;
const PTRACE_EVENT_EXIT: c_int = 6;
const PTRACE_EVENT_SECCOMP: c_int = 7;
const SECCOMP_SET_MODE_FILTER: c_int = 1;
const SECCOMP_MODE_FILTER: c_int = 2;
const SYS_CLOSE_RANGE: c_long = 436;
const SYS_EXECVEAT: c_long = 322;
const SYS_POLL: u32 = 7;
const SYS_PIDFD_OPEN: c_long = 434;
const SYS_PIDFD_SEND_SIGNAL: c_long = 424;
const SYS_SECCOMP: c_long = 317;
const AT_EMPTY_PATH: c_int = 0x1000;
const AUDIT_ARCH_X86_64: u32 = 0xc000_003e;
const BPF_LD_W_ABS: u16 = 0x20;
const BPF_JMP_JEQ_K: u16 = 0x15;
const BPF_RET_K: u16 = 0x06;
const SECCOMP_RET_KILL_PROCESS: u32 = 0x8000_0000;
const SECCOMP_RET_TRACE: u32 = 0x7ff0_0000;
const SECCOMP_RET_ALLOW: u32 = 0x7fff_0000;
const DEADLINE: Duration = Duration::from_secs(5);
const POLLIN: i16 = 1;
#[cfg(feature = "adversarial-test-hooks")]
const SYS_GETPID: c_long = 39;

// SO_PEERCRED peer-credential channel. The child connects to the broker's
// abstract listener so SO_PEERCRED on the accepted socket attests the *child's*
// identity (a socketpair created in the broker would attest the broker).
const AF_UNIX: c_int = 1;
const SOCK_STREAM: c_int = 1;
const SOCK_CLOEXEC: c_int = 0o2000000;
const SYS_SOCKET: c_long = 41;
const SYS_BIND: c_long = 49;
const SYS_LISTEN: c_long = 50;
const SYS_ACCEPT4: c_long = 288;
const SYS_CONNECT: c_long = 42;
const SYS_CLOSE: c_long = 3;
const PEER_LISTEN_BACKLOG: c_int = 1;
const PEER_NAME_PREFIX: &[u8] = b"trustforge-nf2-peer-";

// Static-musl startup plus exit. write/prctl/execveat are constrained below.
const ALLOWED_SYSCALLS: [u32; 18] = [
    3, SYS_POLL, 9, 10, 11, 12, 13, 14, 15, 60, 131, 158, 202, 218, 231, 273, 318, 334,
];
const SYS_WRITE: u32 = 1;
const SYS_PRCTL: u32 = 157;
const SYS_PRLIMIT64: u32 = 302;

#[repr(C)]
#[derive(Clone, Copy)]
struct SockFilter {
    code: u16,
    jt: u8,
    jf: u8,
    k: u32,
}

#[repr(C)]
struct SockFprog {
    length: u16,
    filter: *const SockFilter,
}

#[repr(C)]
struct PollFd {
    fd: c_int,
    events: i16,
    revents: i16,
}

#[repr(C)]
#[derive(Default)]
struct UserRegs {
    r15: u64,
    r14: u64,
    r13: u64,
    r12: u64,
    rbp: u64,
    rbx: u64,
    r11: u64,
    r10: u64,
    r9: u64,
    r8: u64,
    rax: u64,
    rcx: u64,
    rdx: u64,
    rsi: u64,
    rdi: u64,
    orig_rax: u64,
    rip: u64,
    cs: u64,
    eflags: u64,
    rsp: u64,
    ss: u64,
    fs_base: u64,
    gs_base: u64,
    ds: u64,
    es: u64,
    fs: u64,
    gs: u64,
}

#[repr(C, align(8))]
struct SigInfo {
    signo: c_int,
    error: c_int,
    code: c_int,
    padding: c_int,
    pid: c_int,
    uid: u32,
    status: c_int,
    remaining: [u8; 100],
}

#[repr(C)]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
struct SockAddrUn {
    sun_family: u16,
    sun_path: [u8; 108],
}

unsafe extern "C" {
    fn pipe2(pipefd: *mut c_int, flags: c_int) -> c_int;
    fn fcntl(fd: c_int, command: c_int, ...) -> c_int;
    fn fork() -> c_int;
    fn dup3(old: c_int, new: c_int, flags: c_int) -> c_int;
    fn prctl(option: c_int, ...) -> c_int;
    fn ptrace(request: c_int, pid: c_int, address: *mut c_void, data: *mut c_void) -> c_long;
    fn syscall(number: c_long, ...) -> c_long;
    fn waitpid(pid: c_int, status: *mut c_int, options: c_int) -> c_int;
    fn waitid(idtype: c_int, id: u32, information: *mut c_void, options: c_int) -> c_int;
    fn kill(pid: c_int, signal: c_int) -> c_int;
    fn poll(descriptors: *mut PollFd, count: usize, timeout_ms: c_int) -> c_int;
    fn read(fd: c_int, buffer: *mut c_void, count: usize) -> isize;
    fn write(fd: c_int, buffer: *const c_void, count: usize) -> isize;
    fn getppid() -> c_int;
    fn raise(signal: c_int) -> c_int;
    fn _exit(status: c_int) -> !;
}

struct Child {
    pid: c_int,
    pidfd: OwnedFd,
    reaped: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum TraceStage {
    AwaitFirstSeccomp,
    AwaitExec,
    AwaitExit,
    Complete,
}

impl TraceStage {
    fn accept(&mut self, event: c_int) -> Result<(), &'static str> {
        *self = match (*self, event) {
            (Self::AwaitFirstSeccomp, PTRACE_EVENT_SECCOMP) => Self::AwaitExec,
            (Self::AwaitExec, PTRACE_EVENT_EXEC) => Self::AwaitExit,
            (Self::AwaitExit, PTRACE_EVENT_EXIT) => Self::Complete,
            _ => return Err("unexpected ptrace event ordering"),
        };
        Ok(())
    }

    fn expected(self) -> Result<c_int, &'static str> {
        match self {
            Self::AwaitFirstSeccomp => Ok(PTRACE_EVENT_SECCOMP),
            Self::AwaitExec => Ok(PTRACE_EVENT_EXEC),
            Self::AwaitExit => Ok(PTRACE_EVENT_EXIT),
            Self::Complete => Err("ptrace lifecycle already complete"),
        }
    }
}

impl Child {
    fn ensure_live(&self) -> Result<(), &'static str> {
        let mut descriptor = PollFd {
            fd: self.pidfd.as_raw_fd(),
            events: POLLIN,
            revents: 0,
        };
        // SAFETY: zero-time poll of one initialized retained pidfd.
        if unsafe { poll(&mut descriptor, 1, 0) } != 0 {
            return Err("pidfd reports child exit");
        }
        Ok(())
    }

    fn kill_and_reap(&mut self) -> Result<(), &'static str> {
        if self.reaped {
            return Ok(());
        }
        // SAFETY: pidfd is retained for this directly forked child.
        let signaled = unsafe {
            syscall(
                SYS_PIDFD_SEND_SIGNAL,
                self.pidfd.as_raw_fd(),
                SIGKILL,
                std::ptr::null::<c_void>(),
                0,
            )
        };
        // The caller may already have consumed a ptrace-stop notification.
        // Resume this exact child best-effort so a pending SIGKILL can reach a
        // terminal wait status. A running or already-dead child can reject the
        // request; bounded exact-child reaping below remains authoritative.
        if signaled == 0 {
            unsafe {
                ptrace(
                    PTRACE_CONT,
                    self.pid,
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                );
            }
        }
        if let Err(error) = bounded_waitpid_reap(self.pid, signaled == 0) {
            return if signaled == 0 {
                Err(error)
            } else {
                Err("pidfd kill failed")
            };
        }
        self.reaped = true;
        Ok(())
    }

    fn continue_and_reap_clean(&mut self) -> Result<(), &'static str> {
        // SAFETY: traced child is stopped at the ptrace exit barrier.
        if unsafe {
            ptrace(
                PTRACE_CONT,
                self.pid,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            )
        } != 0
        {
            return Err("final ptrace continue failed");
        }
        wait_pidfd_readable(&self.pidfd)?;
        let mut information = SigInfo {
            signo: 0,
            error: 0,
            code: 0,
            padding: 0,
            pid: 0,
            uid: 0,
            status: -1,
            remaining: [0; 100],
        };
        // SAFETY: readable retained pidfd and ABI-shaped siginfo output.
        let waited = unsafe {
            waitid(
                P_PIDFD,
                self.pidfd.as_raw_fd() as u32,
                (&mut information as *mut SigInfo).cast(),
                WEXITED,
            )
        };
        if waited != 0 {
            return Err("child waitid failed");
        }
        // WEXITED consumed the exact child even when its disposition is unsafe.
        // Mark it reaped before validating siginfo so Drop does not mask the
        // original lifecycle error by attempting to kill an already-reaped PID.
        self.reaped = true;
        if information.code != 1 || information.status != 0 || information.pid != self.pid {
            return Err("child exit status mismatch");
        }
        Ok(())
    }
}

impl Drop for Child {
    fn drop(&mut self) {
        if let Err(error) = self.kill_and_reap() {
            // SAFETY: write is async-signal-safe and the error is a fixed
            // static diagnostic. Cleanup ambiguity must remain fail closed.
            unsafe {
                write(2, b"BLOCK: ".as_ptr().cast(), 7);
                write(2, error.as_ptr().cast(), error.len());
                write(2, b"\n".as_ptr().cast(), 1);
            }
            // SAFETY: cleanup ambiguity must not return a live broker; PDEATHSIG
            // kills the child when this broker process exits.
            unsafe { _exit(70) }
        }
    }
}

use std::os::fd::AsRawFd;

pub fn run<S: crate::CapabilitySink>(sealed: &SealedNf1, sink: &S) -> Result<(), &'static str> {
    sealed.reverify()?;
    let test_mode = test_mode();
    #[cfg(feature = "adversarial-test-hooks")]
    let fixture = if test_mode == 12 {
        Some(
            std::fs::File::open(
                std::env::var_os("TRUSTFORGE_NF2_SECOND_EXEC_FIXTURE")
                    .ok_or("second-exec fixture path absent")?,
            )
            .map_err(|_| "second-exec fixture unavailable")?,
        )
    } else {
        None
    };
    let (stdin_read, stdin_write) = pipe()?;
    let (stdout_read, stdout_write) = pipe()?;
    let (stderr_read, stderr_write) = pipe()?;
    #[cfg(feature = "adversarial-test-hooks")]
    let runtime_fd = fixture
        .as_ref()
        .map_or(sealed.runtime_fd(), |file| file.as_raw_fd());
    #[cfg(not(feature = "adversarial-test-hooks"))]
    let runtime_fd = sealed.runtime_fd();
    let child_runtime = duplicate_high(runtime_fd)?;
    let child_stdin = duplicate_high(stdin_read.as_raw_fd())?;
    let child_stdout = duplicate_high(stdout_write.as_raw_fd())?;
    let child_stderr = duplicate_high(stderr_write.as_raw_fd())?;
    let parent_pid = std::process::id() as c_int;
    let peer_listener = create_peer_listener(parent_pid)?;
    // SAFETY: fork is performed before this broker creates any threads.
    let pid = unsafe { fork() };
    if pid < 0 {
        return Err("fork failed");
    }
    if pid == 0 {
        drop(stdin_write);
        drop(stdout_read);
        drop(stderr_read);
        child_exec(
            child_runtime.as_raw_fd(),
            child_stdin.as_raw_fd(),
            child_stdout.as_raw_fd(),
            child_stderr.as_raw_fd(),
            parent_pid,
            test_mode,
        );
    }
    drop(stdin_read);
    drop(stdin_write);
    drop(child_runtime);
    drop(child_stdin);
    drop(child_stdout);
    drop(child_stderr);
    drop(stdout_write);
    drop(stderr_write);
    // SAFETY: pidfd_open receives the directly returned child PID.
    let pidfd_raw = unsafe { syscall(SYS_PIDFD_OPEN, pid, 0) } as c_int;
    if pidfd_raw < 0 {
        // SAFETY: exact directly forked child cleanup before returning.
        let kill_was_sent = unsafe { kill(pid, SIGKILL) } == 0;
        bounded_waitpid_reap(pid, kill_was_sent)?;
        return Err("pidfd_open failed");
    }
    // SAFETY: successful pidfd_open returns a new owned descriptor.
    let pidfd = unsafe { OwnedFd::from_raw_fd(pidfd_raw) };
    let mut child = Child {
        pid,
        pidfd,
        reaped: false,
    };
    wait_for_stop(&child, SIGSTOP, 0)?;
    // The child connected to the peer listener before raising SIGSTOP, so its
    // connection is queued and its connector descriptor is still open. Capture
    // the kernel credential now, then close the listener; neither descriptor
    // lives in the child's post-exec FD space.
    let peer_credential = capture_child_peer_credential(&peer_listener, &child)?;
    drop(peer_listener);
    #[cfg(feature = "adversarial-test-hooks")]
    let peer_credential = if test_mode == 16 {
        PeerCredential {
            pid: peer_credential.pid,
            uid: peer_credential.uid ^ 1,
            gid: peer_credential.gid,
        }
    } else {
        peer_credential
    };
    pause_broker_at(test_mode, 13);
    set_initial_trace_options(&child)?;
    let mut trace_stage = TraceStage::AwaitFirstSeccomp;
    continue_trace_stage(&child, &mut trace_stage)?;
    child.ensure_live()?;
    sealed.reverify()?;
    #[cfg(feature = "adversarial-test-hooks")]
    let expected_runtime = if let Some(file) = fixture.as_ref() {
        let metadata = file
            .metadata()
            .map_err(|_| "second-exec fixture identity unavailable")?;
        (metadata.dev(), metadata.ino())
    } else {
        sealed.runtime_device_inode()
    };
    #[cfg(not(feature = "adversarial-test-hooks"))]
    let expected_runtime = sealed.runtime_device_inode();
    verify_initial_exec_event(&child, sealed, expected_runtime)?;
    pause_broker_at(test_mode, 14);
    continue_trace_stage(&child, &mut trace_stage)?;
    child.ensure_live()?;
    sealed.reverify()?;
    notify_sink(sink.on_ready_bound(), "ready_bound")?;
    pause_broker_at(test_mode, 15);
    #[cfg(feature = "adversarial-test-hooks")]
    if test_mode == 12 {
        continue_to_stop(&child, PTRACE_EVENT_SECCOMP)?;
        return Err("second exec transition rejected");
    }
    #[cfg(feature = "adversarial-test-hooks")]
    let authority_pid = if test_mode == 6 { pid + 1 } else { pid };
    #[cfg(not(feature = "adversarial-test-hooks"))]
    let authority_pid = pid;
    let authority = LiveAuthority::capture(authority_pid, sealed)?;
    #[cfg(feature = "adversarial-test-hooks")]
    let mut authority = authority;
    #[cfg(feature = "adversarial-test-hooks")]
    if test_mode == 9 {
        authority.inject_executable_identity_mismatch();
    }
    #[cfg(feature = "adversarial-test-hooks")]
    if test_mode == 10 {
        authority.inject_map_identity_mismatch();
    }
    authority.reverify(sealed)?;
    authority.verify_peer_credential(sealed, peer_credential, child.pid)?;
    let (runtime_device, runtime_inode) = sealed.runtime_device_inode();
    notify_sink(
        sink.on_capability_issued(runtime_device, runtime_inode),
        "capability_issued",
    )?;
    continue_with_exit_trace(&child, &mut trace_stage)?;
    child.ensure_live()?;
    sealed.reverify()?;
    authority.reverify(sealed)?;
    authority.verify_peer_credential(sealed, peer_credential, child.pid)?;
    notify_sink(sink.on_derived_pending_recheck(), "derived_pending_recheck")?;
    read_exact_diagnostics(stdout_read.as_raw_fd(), stderr_read.as_raw_fd(), sealed)?;
    child.ensure_live()?;
    sealed.reverify()?;
    authority.reverify(sealed)?;
    authority.verify_peer_credential(sealed, peer_credential, child.pid)?;
    child.continue_and_reap_clean()?;
    notify_sink(sink.on_committed(), "committed")?;
    Ok(())
}

/// Records a capability-sink rejection to stderr, then forwards the error so the
/// caller's `?` aborts the transaction. The live `Child` is still in scope at
/// every call site, so returning `Err` reuses the existing fail-closed
/// kill/reap cleanup in `Child::drop`; no new kill path is introduced.
fn notify_sink(result: Result<(), &'static str>, stage: &str) -> Result<(), &'static str> {
    if let Err(error) = result {
        // SAFETY: write is async-signal-safe; all buffers are fixed/static. This
        // is broker-thread code (not a signal handler), matching Child::drop's
        // diagnostic style. The error is a fixed &'static str from the sink.
        unsafe {
            const TAG: &[u8] = b"CAPABILITY-SINK: ";
            write(2, TAG.as_ptr().cast(), TAG.len());
            write(2, stage.as_ptr().cast(), stage.len());
            const ARROW: &[u8] = b" -> ";
            write(2, ARROW.as_ptr().cast(), ARROW.len());
            write(2, error.as_ptr().cast(), error.len());
            write(2, b"\n".as_ptr().cast(), 1);
        }
        return Err(error);
    }
    Ok(())
}

fn read_exact_diagnostics(
    stdout: RawFd,
    stderr: RawFd,
    sealed: &SealedNf1,
) -> Result<(), &'static str> {
    let expected = sealed.expected_stdout();
    if expected.len() > 256 {
        return Err("expected diagnostic exceeds bound");
    }
    wait_readable(stdout, DEADLINE.as_millis() as c_int)?;
    let mut observed = [0_u8; 257];
    // SAFETY: fixed output buffer is valid and pipe is readable.
    let count = unsafe { read(stdout, observed.as_mut_ptr().cast(), observed.len()) };
    if count != expected.len() as isize || observed[..expected.len()] != expected {
        return Err("NF1 diagnostic output mismatch");
    }
    if poll_readable(stdout, 0)? || poll_readable(stderr, 0)? {
        return Err("unexpected extra diagnostic output");
    }
    Ok(())
}

fn wait_readable(fd: RawFd, timeout_ms: c_int) -> Result<(), &'static str> {
    if poll_readable(fd, timeout_ms)? {
        Ok(())
    } else {
        Err("diagnostic deadline exceeded")
    }
}

fn poll_readable(fd: RawFd, timeout_ms: c_int) -> Result<bool, &'static str> {
    let mut descriptor = PollFd {
        fd,
        events: POLLIN,
        revents: 0,
    };
    // SAFETY: one initialized pollfd is supplied.
    let result = unsafe { poll(&mut descriptor, 1, timeout_ms) };
    if result < 0 {
        return Err("diagnostic poll failed");
    }
    Ok(result == 1 && descriptor.revents & POLLIN != 0)
}

fn bounded_waitpid_reap(pid: c_int, kill_was_sent: bool) -> Result<(), &'static str> {
    let deadline = Instant::now() + DEADLINE;
    loop {
        // SAFETY: nonblocking reap of exact direct child.
        let mut status = 0;
        let result = unsafe { waitpid(pid, &mut status, WNOHANG) };
        if result == pid {
            let low = status & 0x7f;
            if low != 0x7f {
                return Ok(());
            }
            // A traced stop is not a reap. SIGKILL remains pending and the
            // exact direct child must be resumed to reach a terminal status.
            if !kill_was_sent {
                return Err("cleanup child stopped without confirmed kill");
            }
            // SAFETY: waitpid reported this exact direct child ptrace-stopped.
            if unsafe { ptrace(PTRACE_CONT, pid, std::ptr::null_mut(), std::ptr::null_mut()) } != 0
            {
                return Err("cleanup ptrace continue failed");
            }
            continue;
        }
        if result < 0 {
            return Err("fallback waitpid failed");
        }
        if Instant::now() >= deadline {
            return Err("fallback reap deadline exceeded");
        }
        std::thread::sleep(Duration::from_millis(2));
    }
}

fn wait_pidfd_readable(pidfd: &OwnedFd) -> Result<(), &'static str> {
    let mut descriptor = PollFd {
        fd: pidfd.as_raw_fd(),
        events: POLLIN,
        revents: 0,
    };
    // SAFETY: one initialized retained pidfd is supplied.
    if unsafe { poll(&mut descriptor, 1, DEADLINE.as_millis() as c_int) } != 1
        || descriptor.revents & POLLIN == 0
    {
        return Err("pidfd completion deadline exceeded");
    }
    Ok(())
}

fn pipe() -> Result<(OwnedFd, OwnedFd), &'static str> {
    let mut descriptors = [-1; 2];
    // SAFETY: output array has exactly two descriptor slots.
    if unsafe { pipe2(descriptors.as_mut_ptr(), O_CLOEXEC) } != 0 {
        return Err("diagnostic pipe creation failed");
    }
    // SAFETY: successful pipe2 returns two newly owned descriptors.
    Ok(unsafe {
        (
            OwnedFd::from_raw_fd(descriptors[0]),
            OwnedFd::from_raw_fd(descriptors[1]),
        )
    })
}

fn duplicate_high(fd: RawFd) -> Result<OwnedFd, &'static str> {
    // SAFETY: fcntl duplicates the retained descriptor at an unused fd >= 100.
    let duplicate = unsafe { fcntl(fd, F_DUPFD_CLOEXEC, 100) };
    if duplicate < 0 {
        return Err("child source descriptor duplication failed");
    }
    // SAFETY: successful F_DUPFD_CLOEXEC returns a newly owned descriptor.
    Ok(unsafe { OwnedFd::from_raw_fd(duplicate) })
}

/// Builds the abstract AF_UNIX address both endpoints share. The address is
/// keyed by the broker pid so concurrent brokers cannot collide, and is built
/// with no allocation so the post-fork child (async-signal-safe context) and
/// the broker thread produce byte-identical sockaddrs. `sun_path[0] == 0`
/// selects the abstract namespace, which is independent of the filesystem and
/// thus unaffected by the broker's retained-root resolution constraints.
fn peer_address(name_key: c_int) -> (SockAddrUn, c_int) {
    let mut addr = SockAddrUn {
        sun_family: AF_UNIX as u16,
        sun_path: [0; 108],
    };
    let mut offset = 1usize;
    addr.sun_path[offset..offset + PEER_NAME_PREFIX.len()].copy_from_slice(PEER_NAME_PREFIX);
    offset += PEER_NAME_PREFIX.len();
    offset += write_decimal(&mut addr.sun_path[offset..], name_key.max(0) as u64);
    let addrlen = (2 + offset) as c_int;
    (addr, addrlen)
}

/// Writes the decimal expansion of `value` into `target` without allocating.
/// Returns the number of digits written. Async-signal-safe.
fn write_decimal(target: &mut [u8], value: u64) -> usize {
    let mut digits = [0u8; 20];
    let mut count = 0usize;
    let mut remaining = value;
    if remaining == 0 {
        digits[0] = b'0';
        count = 1;
    } else {
        while remaining > 0 {
            digits[count] = b'0' + (remaining % 10) as u8;
            count += 1;
            remaining /= 10;
        }
        digits[..count].reverse();
    }
    target[..count].copy_from_slice(&digits[..count]);
    count
}

/// Creates the broker-side listening AF_UNIX socket in the abstract namespace.
/// Created before fork; the child inherits a copy that close_range later
/// discards, so the listener never reaches the NF1 runtime FD closure.
fn create_peer_listener(name_key: c_int) -> Result<OwnedFd, &'static str> {
    // SAFETY: socket allocates a new AF_UNIX stream socket.
    let fd = unsafe {
        syscall(
            SYS_SOCKET,
            AF_UNIX as c_long,
            (SOCK_STREAM | SOCK_CLOEXEC) as c_long,
            0,
        )
    } as c_int;
    if fd < 0 {
        return Err("peer listener socket creation failed");
    }
    // SAFETY: successful socket returns a newly owned descriptor.
    let listener = unsafe { OwnedFd::from_raw_fd(fd) };
    let (addr, addrlen) = peer_address(name_key);
    // SAFETY: bind receives the initialized abstract sockaddr and its length.
    if unsafe {
        syscall(
            SYS_BIND,
            fd as c_long,
            &addr as *const SockAddrUn as *const c_void,
            addrlen as c_long,
        )
    } != 0
    {
        return Err("peer listener bind failed");
    }
    // SAFETY: listen marks the bound socket with a single-slot backlog.
    if unsafe { syscall(SYS_LISTEN, fd as c_long, PEER_LISTEN_BACKLOG as c_long) } != 0 {
        return Err("peer listener listen failed");
    }
    Ok(listener)
}

/// Accepts the child's connection (queued before it raised SIGSTOP) and reads
/// its SO_PEERCRED. The child's connector descriptor is still open at this
/// point, so the accepted socket carries a live, kernel-verified credential.
fn capture_child_peer_credential(
    listener: &OwnedFd,
    child: &Child,
) -> Result<PeerCredential, &'static str> {
    child.ensure_live()?;
    if !poll_readable(listener.as_raw_fd(), DEADLINE.as_millis() as c_int)? {
        return Err("peer credential listener deadline exceeded");
    }
    // SAFETY: listener is readable with a queued connection; accept4 returns a
    // newly owned connected descriptor carrying the child's peer credential.
    let accepted = unsafe {
        syscall(
            SYS_ACCEPT4,
            listener.as_raw_fd() as c_long,
            core::ptr::null::<c_void>(),
            core::ptr::null::<c_void>(),
            SOCK_CLOEXEC as c_long,
        )
    } as c_int;
    if accepted < 0 {
        return Err("peer credential accept failed");
    }
    // SAFETY: successful accept4 returns a newly owned descriptor.
    let accepted = unsafe { OwnedFd::from_raw_fd(accepted) };
    LiveAuthority::capture_peer_credential(accepted.as_raw_fd())
}

fn wait_for_stop(child: &Child, signal: c_int, event: c_int) -> Result<(), &'static str> {
    let deadline = Instant::now() + DEADLINE;
    loop {
        let mut status = 0;
        // SAFETY: waits only for the exact directly forked PID without blocking.
        let result = unsafe { waitpid(child.pid, &mut status, WNOHANG) };
        if result == child.pid {
            let stopped = status & 0xff == 0x7f;
            let observed_signal = (status >> 8) & 0xff;
            let observed_event = status >> 16;
            if stopped && observed_signal == signal && observed_event == event {
                return Ok(());
            }
            return Err("unexpected child stop or exit");
        }
        if result < 0 {
            return Err("waitpid failed");
        }
        if Instant::now() >= deadline {
            return Err("child boundary deadline exceeded");
        }
        std::thread::sleep(Duration::from_millis(2));
    }
}

fn child_exec(
    runtime: RawFd,
    stdin: RawFd,
    stdout: RawFd,
    stderr: RawFd,
    parent: c_int,
    test_mode: u8,
) -> ! {
    // Only async-signal-safe syscalls are used after fork.
    let mut death_signal = 0;
    // SAFETY: fixed prctl operations and initialized output pointer.
    if unsafe { prctl(PR_SET_PDEATHSIG, SIGKILL) } != 0
        || unsafe { prctl(PR_GET_PDEATHSIG, &mut death_signal) } != 0
        || death_signal != SIGKILL
        || unsafe { getppid() } != parent
        || unsafe { dup3(stdin, 0, 0) } != 0
        || unsafe { dup3(stdout, 1, 0) } != 1
        || unsafe { dup3(stderr, 2, 0) } != 2
        || unsafe { dup3(runtime, 3, O_CLOEXEC) } != 3
    {
        child_fail();
    }
    // Declared at function scope so it survives across the close_range and the
    // exec unsafe blocks: assigned after close_range, closed before execveat.
    let peer_sock: c_int;
    // SAFETY: close all descriptors above the retained exec FD.
    unsafe {
        if !skip_close_range(test_mode) && syscall(SYS_CLOSE_RANGE, 4_u32, u32::MAX, 0_u32) != 0 {
            child_fail();
        }
        #[cfg(feature = "adversarial-test-hooks")]
        if test_mode == 1 {
            loop {
                syscall(
                    35 as c_long,
                    std::ptr::null::<c_void>(),
                    std::ptr::null::<c_void>(),
                );
            }
        }
        #[cfg(feature = "adversarial-test-hooks")]
        {
            let injection: Option<(c_int, &[u8])> = match test_mode {
                4 => Some((1, b"WRONG\n")),
                7 => Some((2, b"UNEXPECTED-STDERR\n")),
                8 => Some((1, b"trustforge-native-")),
                _ => None,
            };
            if let Some((fd, bytes)) = injection
                && write(fd, bytes.as_ptr().cast(), bytes.len()) != bytes.len() as isize
            {
                child_fail();
            }
        }
        // Connect to the broker's abstract listener so SO_PEERCRED on the
        // accepted socket attests THIS child. Created after close_range (lands
        // on a fresh descriptor) and held open across SIGSTOP so the broker can
        // accept it; closed before execveat below so it never enters the NF1 FD
        // closure.
        peer_sock = syscall(SYS_SOCKET, AF_UNIX as c_long, SOCK_STREAM as c_long, 0) as c_int;
        if peer_sock < 0 {
            child_fail();
        }
        {
            let (addr, addrlen) = peer_address(parent);
            if syscall(
                SYS_CONNECT,
                peer_sock as c_long,
                &addr as *const SockAddrUn as *const c_void,
                addrlen as c_long,
            ) != 0
            {
                child_fail();
            }
        }
        if ptrace(
            PTRACE_TRACEME,
            0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        ) != 0
            || raise(SIGSTOP) != 0
            || prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0
            || prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1
        {
            child_fail();
        }
    }
    install_seccomp();
    #[cfg(feature = "adversarial-test-hooks")]
    if test_mode == 3 {
        // SAFETY: deliberately forbidden syscall for the debug-only adversarial harness.
        unsafe {
            syscall(SYS_GETPID);
        }
        child_fail();
    }
    // SAFETY: read back the installed policy before exec.
    if unsafe { prctl(PR_GET_SECCOMP, 0, 0, 0, 0) } != SECCOMP_MODE_FILTER {
        child_fail();
    }
    #[cfg(feature = "adversarial-test-hooks")]
    let pathname = if test_mode == 11 { c"/bin/sh" } else { c"" };
    #[cfg(not(feature = "adversarial-test-hooks"))]
    let pathname = c"";
    let argv = [c"trustforge-native-foundation".as_ptr(), std::ptr::null()];
    let environment = [std::ptr::null::<c_char>()];
    // SAFETY: descriptor 3 is the retained NF1 executable; arrays are terminated.
    #[cfg(feature = "adversarial-test-hooks")]
    let exec_fd = if test_mode == 5 { 2 } else { 3 };
    #[cfg(not(feature = "adversarial-test-hooks"))]
    let exec_fd = 3;
    unsafe {
        // Close the peer-credential descriptor so the exec descriptor set is
        // exactly {0,1,2,3} at the exec boundary. close is syscall 3, which the
        // seccomp policy allowlists; it is never traced.
        if syscall(SYS_CLOSE, peer_sock as c_long) != 0 {
            child_fail();
        }
        syscall(
            SYS_EXECVEAT,
            exec_fd,
            pathname.as_ptr(),
            argv.as_ptr(),
            environment.as_ptr(),
            AT_EMPTY_PATH,
        );
    }
    child_fail()
}

fn skip_close_range(test_mode: u8) -> bool {
    #[cfg(feature = "adversarial-test-hooks")]
    {
        test_mode == 2
    }
    #[cfg(not(feature = "adversarial-test-hooks"))]
    {
        let _ = test_mode;
        false
    }
}

fn test_mode() -> u8 {
    #[cfg(feature = "adversarial-test-hooks")]
    {
        match std::env::var("TRUSTFORGE_NF2_TEST_MODE").as_deref() {
            Ok("hang") => 1,
            Ok("extra-fd") => 2,
            Ok("forbidden-syscall") => 3,
            Ok("wrong-output") => 4,
            Ok("exec-mismatch") => 5,
            Ok("pid-substitution") => 6,
            Ok("stderr-output") => 7,
            Ok("partial-output") => 8,
            Ok("live-exec-substitution") => 9,
            Ok("live-map-substitution") => 10,
            Ok("absolute-exec-path") => 11,
            Ok("second-exec") => 12,
            Ok("pause-bootstrap-stop") => 13,
            Ok("pause-seccomp-stop") => 14,
            Ok("pause-post-exec-stop") => 15,
            Ok("peer-mismatch") => 16,
            _ => 0,
        }
    }
    #[cfg(not(feature = "adversarial-test-hooks"))]
    {
        0
    }
}

fn pause_broker_at(test_mode: u8, stage: u8) {
    #[cfg(feature = "adversarial-test-hooks")]
    if test_mode == stage {
        loop {
            std::thread::sleep(Duration::from_secs(1));
        }
    }
    #[cfg(not(feature = "adversarial-test-hooks"))]
    {
        let _ = (test_mode, stage);
    }
}

fn install_seccomp() {
    let filters = build_filter();
    let program = SockFprog {
        length: filters.len() as u16,
        filter: filters.as_ptr(),
    };
    // SAFETY: program points to a live initialized filter array.
    if unsafe { syscall(SYS_SECCOMP, SECCOMP_SET_MODE_FILTER, 0, &program) } != 0 {
        child_fail();
    }
}

fn build_filter() -> [SockFilter; 70] {
    const FILTER_LENGTH: usize = 70;
    let mut filters = [SockFilter {
        code: 0,
        jt: 0,
        jf: 0,
        k: 0,
    }; FILTER_LENGTH];
    let mut length = 0;
    filters[length] = statement(BPF_LD_W_ABS, 4);
    length += 1;
    filters[length] = jump(AUDIT_ARCH_X86_64, 1, 0);
    length += 1;
    filters[length] = statement(BPF_RET_K, SECCOMP_RET_KILL_PROCESS);
    length += 1;
    filters[length] = statement(BPF_LD_W_ABS, 0);
    length += 1;
    for syscall in ALLOWED_SYSCALLS {
        filters[length] = jump(syscall, 0, 1);
        length += 1;
        filters[length] = statement(BPF_RET_K, SECCOMP_RET_ALLOW);
        length += 1;
    }
    append_argument_allowlist(&mut filters, &mut length, SYS_WRITE, 16, 1, 2);
    append_single_argument_allowlist(
        &mut filters,
        &mut length,
        SYS_PRCTL,
        16,
        PR_GET_SECCOMP as u32,
    );
    append_prlimit64_read_self_allowlist(&mut filters, &mut length);
    append_execveat_allowlist(&mut filters, &mut length, SYS_EXECVEAT as u32);
    filters[length] = statement(BPF_RET_K, SECCOMP_RET_KILL_PROCESS);
    length += 1;
    if length != FILTER_LENGTH {
        unreachable!("compile-time filter length");
    }
    filters
}

fn append_prlimit64_read_self_allowlist(filters: &mut [SockFilter], length: &mut usize) {
    // prlimit64 is read-only and self-only: pid == 0 and new_limit == NULL,
    // including both halves of each 64-bit seccomp_data argument.
    filters[*length] = jump_with(BPF_JMP_JEQ_K, SYS_PRLIMIT64, 0, 10);
    *length += 1;
    for (offset, failure_skip) in [(16, 7), (20, 5), (32, 3), (36, 1)] {
        filters[*length] = statement(BPF_LD_W_ABS, offset);
        *length += 1;
        filters[*length] = jump(0, 0, failure_skip);
        *length += 1;
    }
    filters[*length] = statement(BPF_RET_K, SECCOMP_RET_ALLOW);
    *length += 1;
    filters[*length] = statement(BPF_RET_K, SECCOMP_RET_KILL_PROCESS);
    *length += 1;
}

fn append_argument_allowlist(
    filters: &mut [SockFilter],
    length: &mut usize,
    syscall_number: u32,
    argument_offset: u32,
    first: u32,
    second: u32,
) {
    filters[*length] = jump_with(BPF_JMP_JEQ_K, syscall_number, 0, 5);
    *length += 1;
    filters[*length] = statement(BPF_LD_W_ABS, argument_offset);
    *length += 1;
    filters[*length] = jump(first, 2, 0);
    *length += 1;
    filters[*length] = jump(second, 1, 0);
    *length += 1;
    filters[*length] = statement(BPF_RET_K, SECCOMP_RET_KILL_PROCESS);
    *length += 1;
    filters[*length] = statement(BPF_RET_K, SECCOMP_RET_ALLOW);
    *length += 1;
}

fn append_single_argument_allowlist(
    filters: &mut [SockFilter],
    length: &mut usize,
    syscall_number: u32,
    argument_offset: u32,
    allowed: u32,
) {
    filters[*length] = jump_with(BPF_JMP_JEQ_K, syscall_number, 0, 4);
    *length += 1;
    filters[*length] = statement(BPF_LD_W_ABS, argument_offset);
    *length += 1;
    filters[*length] = jump(allowed, 1, 0);
    *length += 1;
    filters[*length] = statement(BPF_RET_K, SECCOMP_RET_KILL_PROCESS);
    *length += 1;
    filters[*length] = statement(BPF_RET_K, SECCOMP_RET_ALLOW);
    *length += 1;
}

fn append_execveat_allowlist(filters: &mut [SockFilter], length: &mut usize, syscall_number: u32) {
    filters[*length] = jump_with(BPF_JMP_JEQ_K, syscall_number, 0, 6);
    *length += 1;
    filters[*length] = statement(BPF_LD_W_ABS, 16);
    *length += 1;
    filters[*length] = jump(3, 0, 2);
    *length += 1;
    filters[*length] = statement(BPF_LD_W_ABS, 48);
    *length += 1;
    filters[*length] = jump(AT_EMPTY_PATH as u32, 1, 0);
    *length += 1;
    filters[*length] = statement(BPF_RET_K, SECCOMP_RET_KILL_PROCESS);
    *length += 1;
    filters[*length] = statement(BPF_RET_K, SECCOMP_RET_TRACE);
    *length += 1;
}

const fn statement(code: u16, k: u32) -> SockFilter {
    SockFilter {
        code,
        jt: 0,
        jf: 0,
        k,
    }
}

const fn jump(k: u32, jt: u8, jf: u8) -> SockFilter {
    jump_with(BPF_JMP_JEQ_K, k, jt, jf)
}

const fn jump_with(code: u16, k: u32, jt: u8, jf: u8) -> SockFilter {
    SockFilter { code, jt, jf, k }
}

fn child_fail() -> ! {
    // SAFETY: immediate async-signal-safe child termination.
    unsafe { _exit(72) }
}

#[allow(dead_code)]
fn continue_with_exit_trace(
    child: &Child,
    trace_stage: &mut TraceStage,
) -> Result<(), &'static str> {
    continue_trace_stage(child, trace_stage)
}

fn set_initial_trace_options(child: &Child) -> Result<(), &'static str> {
    let options =
        PTRACE_O_TRACEEXEC | PTRACE_O_TRACEEXIT | PTRACE_O_TRACESECCOMP | PTRACE_O_EXITKILL;
    // SAFETY: exact child is stopped after PTRACE_TRACEME and before seccomp install.
    if unsafe {
        ptrace(
            PTRACE_SETOPTIONS,
            child.pid,
            std::ptr::null_mut(),
            options as *mut c_void,
        )
    } != 0
    {
        return Err("ptrace option install failed");
    }
    Ok(())
}

fn verify_initial_exec_event(
    child: &Child,
    sealed: &SealedNf1,
    expected_runtime: (u64, u64),
) -> Result<(), &'static str> {
    let mut registers = UserRegs::default();
    // SAFETY: exact child is stopped at a seccomp ptrace event and the output
    // buffer has the native x86_64 user_regs_struct layout.
    if unsafe {
        ptrace(
            PTRACE_GETREGS,
            child.pid,
            std::ptr::null_mut(),
            (&mut registers as *mut UserRegs).cast(),
        )
    } != 0
    {
        return Err("ptrace register read failed");
    }
    if registers.orig_rax != SYS_EXECVEAT as u64
        || registers.rdi != 3
        || registers.r8 != AT_EMPTY_PATH as u64
    {
        return Err("traced execveat arguments mismatch");
    }
    // SAFETY: PTRACE_PEEKDATA reads one machine word from the stopped child.
    // A legitimate empty C string always has a zero low byte, so even the
    // ambiguous all-ones return value is safely rejected.
    let pathname_word = unsafe {
        ptrace(
            PTRACE_PEEKDATA,
            child.pid,
            registers.rsi as *mut c_void,
            std::ptr::null_mut(),
        )
    };
    if pathname_word as u64 & 0xff != 0 {
        return Err("traced execveat pathname is not empty");
    }
    LiveAuthority::verify_pre_exec_identity(child.pid, sealed, expected_runtime)
}

fn continue_to_stop(child: &Child, event: c_int) -> Result<(), &'static str> {
    // SAFETY: ptrace resumes only the exact retained stopped child.
    if unsafe {
        ptrace(
            PTRACE_CONT,
            child.pid,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    } != 0
    {
        return Err("ptrace continue failed");
    }
    wait_for_stop(child, SIGTRAP, event)
}

fn continue_trace_stage(child: &Child, trace_stage: &mut TraceStage) -> Result<(), &'static str> {
    let event = trace_stage.expected()?;
    continue_to_stop(child, event)?;
    trace_stage.accept(event)
}

#[allow(dead_code)]
const _: c_int = SIGSTOP;

#[cfg(test)]
mod tests {
    use super::*;

    fn evaluate(
        filter: &[SockFilter],
        arch: u32,
        number: u32,
        ip: u64,
        arg0: u64,
        arg2: u64,
        arg4: u64,
    ) -> u32 {
        let mut accumulator = 0_u32;
        let mut pc = 0_usize;
        loop {
            let instruction = filter[pc];
            match instruction.code {
                BPF_LD_W_ABS => {
                    accumulator = match instruction.k {
                        0 => number,
                        4 => arch,
                        8 => ip as u32,
                        12 => (ip >> 32) as u32,
                        16 => arg0 as u32,
                        20 => (arg0 >> 32) as u32,
                        32 => arg2 as u32,
                        36 => (arg2 >> 32) as u32,
                        48 => arg4 as u32,
                        _ => panic!("unexpected load"),
                    };
                    pc += 1;
                }
                BPF_JMP_JEQ_K => {
                    pc += 1 + if accumulator == instruction.k {
                        instruction.jt as usize
                    } else {
                        instruction.jf as usize
                    };
                }
                BPF_RET_K => return instruction.k,
                _ => panic!("unexpected instruction"),
            }
        }
    }

    #[test]
    fn filter_constrains_arch_write_prctl_and_exec_transition() {
        let filter = build_filter();
        // The sealed runtime polls only its inherited stdout/stderr descriptors.
        assert_eq!(
            evaluate(&filter, AUDIT_ARCH_X86_64, SYS_POLL, 0, 0, 0, 0),
            SECCOMP_RET_ALLOW
        );
        assert_eq!(
            evaluate(&filter, AUDIT_ARCH_X86_64, SYS_WRITE, 0, 1, 0, 0),
            SECCOMP_RET_ALLOW
        );
        assert_eq!(
            evaluate(&filter, AUDIT_ARCH_X86_64, SYS_WRITE, 0, 3, 0, 0),
            SECCOMP_RET_KILL_PROCESS
        );
        assert_eq!(
            evaluate(&filter, AUDIT_ARCH_X86_64, SYS_WRITE, 0, 0, 0, 0),
            SECCOMP_RET_KILL_PROCESS
        );
        assert_eq!(
            evaluate(
                &filter,
                AUDIT_ARCH_X86_64,
                SYS_PRCTL,
                0,
                PR_GET_SECCOMP as u64,
                0,
                0
            ),
            SECCOMP_RET_ALLOW
        );
        assert_eq!(
            evaluate(&filter, AUDIT_ARCH_X86_64, SYS_PRCTL, 0, 999, 0, 0),
            SECCOMP_RET_KILL_PROCESS
        );
        assert_eq!(
            evaluate(
                &filter,
                AUDIT_ARCH_X86_64,
                SYS_PRCTL,
                0,
                PR_GET_NO_NEW_PRIVS as u64,
                0,
                0
            ),
            SECCOMP_RET_KILL_PROCESS
        );
        assert_eq!(
            evaluate(
                &filter,
                AUDIT_ARCH_X86_64,
                SYS_EXECVEAT as u32,
                0,
                3,
                0,
                AT_EMPTY_PATH as u64
            ),
            SECCOMP_RET_TRACE
        );
        assert_eq!(
            evaluate(
                &filter,
                AUDIT_ARCH_X86_64,
                SYS_EXECVEAT as u32,
                0,
                2,
                0,
                AT_EMPTY_PATH as u64
            ),
            SECCOMP_RET_KILL_PROCESS
        );
        assert_eq!(
            evaluate(&filter, AUDIT_ARCH_X86_64, SYS_EXECVEAT as u32, 0, 3, 0, 0),
            SECCOMP_RET_KILL_PROCESS
        );
        assert_eq!(
            evaluate(&filter, AUDIT_ARCH_X86_64, SYS_PRLIMIT64, 0, 0, 0, 0),
            SECCOMP_RET_ALLOW
        );
        assert_eq!(
            evaluate(&filter, AUDIT_ARCH_X86_64, SYS_PRLIMIT64, 0, 1, 0, 0),
            SECCOMP_RET_KILL_PROCESS
        );
        assert_eq!(
            evaluate(&filter, AUDIT_ARCH_X86_64, SYS_PRLIMIT64, 0, 0, 1, 0),
            SECCOMP_RET_KILL_PROCESS
        );
        assert_eq!(
            evaluate(&filter, AUDIT_ARCH_X86_64, SYS_PRLIMIT64, 0, 1 << 32, 0, 0),
            SECCOMP_RET_KILL_PROCESS
        );
        assert_eq!(
            evaluate(&filter, AUDIT_ARCH_X86_64, SYS_PRLIMIT64, 0, 0, 1 << 32, 0),
            SECCOMP_RET_KILL_PROCESS
        );
        assert_eq!(
            evaluate(&filter, 0, 60, 0, 0, 0, 0),
            SECCOMP_RET_KILL_PROCESS
        );
        assert_eq!(
            evaluate(&filter, AUDIT_ARCH_X86_64, 999, 0, 0, 0, 0),
            SECCOMP_RET_KILL_PROCESS
        );
    }

    #[test]
    fn child_sources_are_duplicated_above_remap_targets() {
        let (read, write) = pipe().expect("pipe");
        let first = duplicate_high(read.as_raw_fd()).expect("first duplicate");
        let second = duplicate_high(write.as_raw_fd()).expect("second duplicate");
        assert!(first.as_raw_fd() >= 100);
        assert!(second.as_raw_fd() >= 100);
        assert_ne!(first.as_raw_fd(), second.as_raw_fd());
    }

    #[test]
    fn ptrace_state_machine_is_one_shot_and_ordered() {
        let mut stage = TraceStage::AwaitFirstSeccomp;
        for event in [PTRACE_EVENT_SECCOMP, PTRACE_EVENT_EXEC, PTRACE_EVENT_EXIT] {
            stage.accept(event).expect("valid transition");
        }
        assert_eq!(stage, TraceStage::Complete);

        for sequence in [
            [PTRACE_EVENT_SECCOMP, PTRACE_EVENT_SECCOMP],
            [PTRACE_EVENT_EXEC, PTRACE_EVENT_EXIT],
            [PTRACE_EVENT_EXIT, PTRACE_EVENT_EXEC],
            [99, PTRACE_EVENT_SECCOMP],
        ] {
            let mut stage = TraceStage::AwaitFirstSeccomp;
            assert!(
                sequence
                    .into_iter()
                    .any(|event| stage.accept(event).is_err()),
                "{sequence:?}"
            );
        }
        let mut runtime = TraceStage::AwaitExit;
        assert!(runtime.accept(PTRACE_EVENT_SECCOMP).is_err());
        assert_eq!(runtime, TraceStage::AwaitExit);
    }

    #[test]
    fn decimal_expansion_is_exact_and_unpadded() {
        let mut buf = [b'X'; 8];
        let n = write_decimal(&mut buf, 0);
        assert_eq!(&buf[..n], b"0");
        let n = write_decimal(&mut buf, 7);
        assert_eq!(&buf[..n], b"7");
        let n = write_decimal(&mut buf, 12345);
        assert_eq!(&buf[..n], b"12345");
        // No leading zeros, no trailing bytes touched beyond the digits.
        let n = write_decimal(&mut buf, 42);
        assert_eq!(&buf[..n], b"42");
        assert_eq!(buf[n], b'X');
    }

    #[test]
    fn peer_address_is_abstract_and_deterministic() {
        let (addr, addrlen) = peer_address(99);
        // Abstract namespace: sun_path[0] is the null marker.
        assert_eq!(addr.sun_family, AF_UNIX as u16);
        assert_eq!(addr.sun_path[0], 0);
        let name = &addr.sun_path[1..(addrlen as usize - 2)];
        let expected = b"trustforge-nf2-peer-99";
        assert_eq!(name, expected);
        // Broker and child build byte-identical addresses from the same key.
        assert_eq!(peer_address(99), peer_address(99));
        assert_ne!(peer_address(99), peer_address(100));
        // Name stays well inside the 108-byte sun_path bound.
        assert!((addrlen as usize) < 2 + 108);
    }
}
