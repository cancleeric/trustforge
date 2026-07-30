use core::ffi::{c_char, c_long};

const SYS_EXECVEAT: c_long = 322;
const SYS_WRITE: c_long = 1;
const AT_EMPTY_PATH: c_long = 0x1000;

unsafe extern "C" {
    fn syscall(number: c_long, ...) -> c_long;
}

fn main() {
    if std::env::args().nth(1).as_deref() == Some("SECOND") {
        // This is a test-only escape marker. A correct broker stops the second
        // execveat at its seccomp event, so this image is never allowed to run.
        unsafe {
            syscall(SYS_WRITE, 1, b"SECOND-EXEC-RAN\n".as_ptr(), 16_usize);
        }
        std::process::exit(91);
    }
    let pathname = c"/proc/self/exe";
    let argv = [
        c"nf2-second-exec-fixture".as_ptr(),
        c"SECOND".as_ptr(),
        std::ptr::null(),
    ];
    let environment = [std::ptr::null::<c_char>()];
    unsafe {
        syscall(
            SYS_EXECVEAT,
            3,
            pathname.as_ptr(),
            argv.as_ptr(),
            environment.as_ptr(),
            AT_EMPTY_PATH,
        );
    }
    std::process::exit(92);
}
