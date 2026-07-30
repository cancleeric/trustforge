use super::live::LiveAuthority;
use super::sealed::SealedNf1;
use core::ffi::{c_char, c_int, c_long, c_void};
use std::os::fd::{FromRawFd, OwnedFd, RawFd};
use std::time::{Duration, Instant};

const O_CLOEXEC: c_int = 0o2000000;
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
const PTRACE_CONT: c_int = 7;
const PTRACE_SETOPTIONS: c_int = 0x4200;
const PTRACE_O_TRACEEXIT: usize = 0x40;
const PTRACE_EVENT_EXIT: c_int = 6;
const SECCOMP_SET_MODE_FILTER: c_int = 1;
const SECCOMP_MODE_FILTER: c_int = 2;
const SYS_CLOSE_RANGE: c_long = 436;
const SYS_EXECVEAT: c_long = 322;
const SYS_PIDFD_OPEN: c_long = 434;
const SYS_PIDFD_SEND_SIGNAL: c_long = 424;
const SYS_SECCOMP: c_long = 317;
const AT_EMPTY_PATH: c_int = 0x1000;
const AUDIT_ARCH_X86_64: u32 = 0xc000_003e;
const BPF_LD_W_ABS: u16 = 0x20;
const BPF_JMP_JEQ_K: u16 = 0x15;
const BPF_RET_K: u16 = 0x06;
const SECCOMP_RET_KILL_PROCESS: u32 = 0x8000_0000;
const SECCOMP_RET_ALLOW: u32 = 0x7fff_0000;
const DEADLINE: Duration = Duration::from_secs(5);
const POLLIN: i16 = 1;
#[cfg(feature = "adversarial-test-hooks")]
const SYS_GETPID: c_long = 39;

// Static-musl startup plus exit. write/prctl/execveat are constrained below.
const ALLOWED_SYSCALLS: [u32; 17] = [
    3, 9, 10, 11, 12, 13, 14, 15, 60, 131, 158, 202, 218, 231, 273, 318, 334,
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

#[repr(C, align(8))]
struct SigInfoStorage([u8; 128]);

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

unsafe extern "C" {
    fn pipe2(pipefd: *mut c_int, flags: c_int) -> c_int;
    fn fork() -> c_int;
    fn dup3(old: c_int, new: c_int, flags: c_int) -> c_int;
    fn close(fd: c_int) -> c_int;
    fn prctl(option: c_int, ...) -> c_int;
    fn ptrace(request: c_int, pid: c_int, address: *mut c_void, data: *mut c_void) -> c_long;
    fn syscall(number: c_long, ...) -> c_long;
    fn waitpid(pid: c_int, status: *mut c_int, options: c_int) -> c_int;
    fn waitid(idtype: c_int, id: u32, information: *mut c_void, options: c_int) -> c_int;
    fn kill(pid: c_int, signal: c_int) -> c_int;
    fn poll(descriptors: *mut PollFd, count: usize, timeout_ms: c_int) -> c_int;
    fn read(fd: c_int, buffer: *mut c_void, count: usize) -> isize;
    #[cfg(feature = "adversarial-test-hooks")]
    fn write(fd: c_int, buffer: *const c_void, count: usize) -> isize;
    fn getppid() -> c_int;
    fn _exit(status: c_int) -> !;
}

struct Child {
    pid: c_int,
    pidfd: OwnedFd,
    reaped: bool,
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
        if signaled != 0 {
            return Err("pidfd kill failed");
        }
        let mut descriptor = PollFd {
            fd: self.pidfd.as_raw_fd(),
            events: POLLIN,
            revents: 0,
        };
        // SAFETY: descriptor points to one initialized pollfd.
        if unsafe { poll(&mut descriptor, 1, DEADLINE.as_millis() as c_int) } != 1
            || descriptor.revents & POLLIN == 0
        {
            return Err("pidfd exit deadline exceeded");
        }
        // SAFETY: readable pidfd means waitid(P_PIDFD) cannot block.
        let result = unsafe {
            let mut information = SigInfoStorage([0_u8; 128]);
            waitid(
                P_PIDFD,
                self.pidfd.as_raw_fd() as u32,
                information.0.as_mut_ptr().cast(),
                WEXITED,
            )
        };
        if result != 0 {
            return Err("pidfd waitid failed");
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
        if unsafe {
            waitid(
                P_PIDFD,
                self.pidfd.as_raw_fd() as u32,
                (&mut information as *mut SigInfo).cast(),
                WEXITED,
            )
        } != 0
            || information.code != 1
            || information.status != 0
            || information.pid != self.pid
        {
            return Err("child exit status mismatch");
        }
        self.reaped = true;
        Ok(())
    }
}

impl Drop for Child {
    fn drop(&mut self) {
        if self.kill_and_reap().is_err() {
            // SAFETY: cleanup ambiguity must not return a live broker; PDEATHSIG
            // kills the child when this broker process exits.
            unsafe { _exit(70) }
        }
    }
}

use std::os::fd::AsRawFd;

pub fn run(sealed: &SealedNf1) -> Result<(), &'static str> {
    sealed.reverify()?;
    let (stdout_read, stdout_write) = pipe()?;
    let (stderr_read, stderr_write) = pipe()?;
    let parent_pid = std::process::id() as c_int;
    let test_mode = test_mode();
    // SAFETY: fork is performed before this broker creates any threads.
    let pid = unsafe { fork() };
    if pid < 0 {
        return Err("fork failed");
    }
    if pid == 0 {
        drop(stdout_read);
        drop(stderr_read);
        child_exec(
            sealed.runtime_fd(),
            stdout_write.as_raw_fd(),
            stderr_write.as_raw_fd(),
            parent_pid,
            test_mode,
        );
    }
    drop(stdout_write);
    drop(stderr_write);
    // SAFETY: pidfd_open receives the directly returned child PID.
    let pidfd_raw = unsafe { syscall(SYS_PIDFD_OPEN, pid, 0) } as c_int;
    if pidfd_raw < 0 {
        // SAFETY: exact directly forked child cleanup before returning.
        unsafe {
            kill(pid, SIGKILL);
        }
        bounded_waitpid_reap(pid)?;
        return Err("pidfd_open failed");
    }
    // SAFETY: successful pidfd_open returns a new owned descriptor.
    let pidfd = unsafe { OwnedFd::from_raw_fd(pidfd_raw) };
    let mut child = Child {
        pid,
        pidfd,
        reaped: false,
    };
    wait_for_stop(&child, SIGTRAP, 0)?;
    child.ensure_live()?;
    sealed.reverify()?;
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
    continue_with_exit_trace(&child)?;
    child.ensure_live()?;
    sealed.reverify()?;
    authority.reverify(sealed)?;
    read_exact_diagnostics(stdout_read.as_raw_fd(), stderr_read.as_raw_fd(), sealed)?;
    child.ensure_live()?;
    sealed.reverify()?;
    authority.reverify(sealed)?;
    child.continue_and_reap_clean()?;
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

fn bounded_waitpid_reap(pid: c_int) -> Result<(), &'static str> {
    let deadline = Instant::now() + DEADLINE;
    loop {
        // SAFETY: nonblocking reap of exact direct child.
        let result = unsafe { waitpid(pid, std::ptr::null_mut(), WNOHANG) };
        if result == pid {
            return Ok(());
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

fn child_exec(runtime: RawFd, stdout: RawFd, stderr: RawFd, parent: c_int, test_mode: u8) -> ! {
    // Only async-signal-safe syscalls are used after fork.
    let mut death_signal = 0;
    // SAFETY: fixed prctl operations and initialized output pointer.
    if unsafe { prctl(PR_SET_PDEATHSIG, SIGKILL) } != 0
        || unsafe { prctl(PR_GET_PDEATHSIG, &mut death_signal) } != 0
        || death_signal != SIGKILL
        || unsafe { getppid() } != parent
        || unsafe { dup3(stdout, 1, 0) } != 1
        || unsafe { dup3(stderr, 2, 0) } != 2
        || unsafe { dup3(runtime, 3, O_CLOEXEC) } != 3
    {
        child_fail();
    }
    // SAFETY: close fixed inherited stdin and all descriptors above retained exec FD.
    unsafe {
        close(0);
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
        if ptrace(
            PTRACE_TRACEME,
            0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        ) != 0
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
    let empty = c"";
    let argv = [c"trustforge-native-foundation".as_ptr(), std::ptr::null()];
    let environment = [std::ptr::null::<c_char>()];
    // SAFETY: descriptor 3 is the retained NF1 executable; arrays are terminated.
    #[cfg(feature = "adversarial-test-hooks")]
    let exec_fd = if test_mode == 5 { 2 } else { 3 };
    #[cfg(not(feature = "adversarial-test-hooks"))]
    let exec_fd = 3;
    unsafe {
        syscall(
            SYS_EXECVEAT,
            exec_fd,
            empty.as_ptr(),
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
            _ => 0,
        }
    }
    #[cfg(not(feature = "adversarial-test-hooks"))]
    {
        0
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

fn build_filter() -> [SockFilter; 68] {
    const FILTER_LENGTH: usize = 68;
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
    filters[*length] = jump(3, 0, 3);
    *length += 1;
    filters[*length] = statement(BPF_LD_W_ABS, 48);
    *length += 1;
    filters[*length] = jump(AT_EMPTY_PATH as u32, 1, 0);
    *length += 1;
    filters[*length] = statement(BPF_RET_K, SECCOMP_RET_KILL_PROCESS);
    *length += 1;
    filters[*length] = statement(BPF_RET_K, SECCOMP_RET_ALLOW);
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
fn continue_with_exit_trace(child: &Child) -> Result<(), &'static str> {
    // SAFETY: ptrace operations target the exact stopped traced child.
    if unsafe {
        ptrace(
            PTRACE_SETOPTIONS,
            child.pid,
            std::ptr::null_mut(),
            PTRACE_O_TRACEEXIT as *mut c_void,
        )
    } != 0
        || unsafe {
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
    wait_for_stop(child, SIGTRAP, PTRACE_EVENT_EXIT)
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
            SECCOMP_RET_ALLOW
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
}
