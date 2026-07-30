use crate::{manifest, sha256};
use core::ffi::{c_char, c_int, c_long};
use std::ffi::CString;
use std::fs::{File, Metadata};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::fs::MetadataExt;

const INSTALL_ROOT: &str = "opt/trustforge/native-foundation/current";
const MANIFEST_MAX_BYTES: u64 = 64 * 1024 * 1024;
const RUNTIME_MAX_BYTES: u64 = 32 * 1024 * 1024;
const O_RDONLY: c_int = 0;
const O_CLOEXEC: c_int = 0o2000000;
const O_NOFOLLOW: c_int = 0o400000;
const O_DIRECTORY: c_int = 0o200000;
const O_PATH: c_int = 0o10000000;
const RESOLVE_NO_MAGICLINKS: u64 = 0x02;
const RESOLVE_NO_SYMLINKS: u64 = 0x04;
const RESOLVE_BENEATH: u64 = 0x08;
const SYS_OPENAT2: c_long = 437;

#[repr(C)]
struct OpenHow {
    flags: u64,
    mode: u64,
    resolve: u64,
}

unsafe extern "C" {
    fn open(path: *const c_char, flags: c_int, ...) -> c_int;
    fn syscall(number: c_long, ...) -> c_long;
    fn pread(fd: c_int, buffer: *mut u8, count: usize, offset: i64) -> isize;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct Identity {
    device: u64,
    inode: u64,
    mode: u32,
    owner: u32,
    group: u32,
    links: u64,
    size: u64,
    modified_seconds: i64,
    modified_nanoseconds: i64,
    changed_seconds: i64,
    changed_nanoseconds: i64,
}

impl Identity {
    fn capture(metadata: &Metadata) -> Self {
        Self {
            device: metadata.dev(),
            inode: metadata.ino(),
            mode: metadata.mode(),
            owner: metadata.uid(),
            group: metadata.gid(),
            links: metadata.nlink(),
            size: metadata.size(),
            modified_seconds: metadata.mtime(),
            modified_nanoseconds: metadata.mtime_nsec(),
            changed_seconds: metadata.ctime(),
            changed_nanoseconds: metadata.ctime_nsec(),
        }
    }
}

pub struct SealedNf1 {
    filesystem_root: File,
    install: File,
    package: File,
    manifest: File,
    runtime: File,
    install_identity: Identity,
    filesystem_root_identity: Identity,
    package_identity: Identity,
    manifest_identity: Identity,
    runtime_identity: Identity,
    runtime_digest: [u8; 32],
    manifest_digest: [u8; 32],
    binding: manifest::RuntimeBinding,
}

impl SealedNf1 {
    pub fn open() -> Result<Self, &'static str> {
        let filesystem_root = open_filesystem_root()?;
        let install = open_beneath(&filesystem_root, INSTALL_ROOT, O_PATH | O_DIRECTORY)?;
        validate_directory(&install, "install root")?;
        adversarial_pause("after-install")?;
        let package = open_beneath(&install, "package", O_PATH | O_DIRECTORY)?;
        validate_directory(&package, "package root")?;
        let manifest_file = open_beneath(&install, "native-hermetic-provenance.json", O_RDONLY)?;
        validate_regular(&manifest_file, 0o444, "manifest")?;
        adversarial_pause("after-manifest")?;
        let manifest_bytes = bounded_pread(&manifest_file, MANIFEST_MAX_BYTES, "manifest")?;
        let binding = manifest::validate_accepted(&manifest_bytes)?;
        let runtime = open_beneath(&package, manifest::RUNTIME_PATH, O_RDONLY)?;
        validate_regular(&runtime, binding.mode, "runtime")?;
        let runtime_metadata = runtime.metadata().map_err(|_| "runtime stat failed")?;
        if runtime_metadata.size() != binding.size {
            return Err("runtime size differs from manifest");
        }
        let runtime_bytes = bounded_pread(&runtime, RUNTIME_MAX_BYTES, "runtime")?;
        let digest = sha256::digest(&runtime_bytes);
        if digest != binding.sha256 {
            return Err("runtime digest differs from manifest");
        }
        let sealed = Self {
            filesystem_root_identity: identity(&filesystem_root, "filesystem root identity")?,
            filesystem_root,
            install_identity: identity(&install, "install identity")?,
            package_identity: identity(&package, "package identity")?,
            manifest_identity: identity(&manifest_file, "manifest identity")?,
            runtime_identity: identity(&runtime, "runtime identity")?,
            install,
            package,
            manifest: manifest_file,
            runtime,
            runtime_digest: digest,
            manifest_digest: sha256::digest(&manifest_bytes),
            binding,
        };
        sealed.reverify()?;
        Ok(sealed)
    }

    pub fn runtime_fd(&self) -> c_int {
        self.runtime.as_raw_fd()
    }

    pub fn filesystem_root_fd(&self) -> c_int {
        self.filesystem_root.as_raw_fd()
    }

    pub fn runtime_device_inode(&self) -> (u64, u64) {
        (self.runtime_identity.device, self.runtime_identity.inode)
    }

    pub fn expected_stdout(&self) -> Vec<u8> {
        format!(
            "trustforge-native-foundation/v1:{}\n",
            self.binding.source_epoch
        )
        .into_bytes()
    }

    pub fn reverify(&self) -> Result<(), &'static str> {
        compare_identity(&self.install, self.install_identity, "install changed")?;
        compare_identity(
            &self.filesystem_root,
            self.filesystem_root_identity,
            "filesystem root changed",
        )?;
        compare_identity(&self.package, self.package_identity, "package changed")?;
        compare_identity(&self.manifest, self.manifest_identity, "manifest changed")?;
        compare_identity(&self.runtime, self.runtime_identity, "runtime changed")?;
        let manifest_bytes = bounded_pread(&self.manifest, MANIFEST_MAX_BYTES, "manifest")?;
        if sha256::digest(&manifest_bytes) != self.manifest_digest
            || manifest::validate_accepted(&manifest_bytes)? != self.binding
        {
            return Err("manifest content changed");
        }
        let bytes = bounded_pread(&self.runtime, RUNTIME_MAX_BYTES, "runtime")?;
        if sha256::digest(&bytes) != self.runtime_digest {
            return Err("runtime content changed");
        }
        Ok(())
    }
}

fn open_filesystem_root() -> Result<File, &'static str> {
    let path = CString::new("/").map_err(|_| "root path contains NUL")?;
    // SAFETY: path is NUL terminated and flags retain a no-follow directory handle.
    let fd = unsafe {
        open(
            path.as_ptr(),
            O_PATH | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC,
            0,
        )
    };
    owned_file(fd, "filesystem root open failed")
}

fn open_beneath(root: &File, relative: &str, flags: c_int) -> Result<File, &'static str> {
    let path = CString::new(relative).map_err(|_| "relative path contains NUL")?;
    let how = OpenHow {
        flags: (flags | O_CLOEXEC | O_NOFOLLOW) as u64,
        mode: 0,
        resolve: RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS,
    };
    // SAFETY: openat2 receives a retained directory FD and initialized arguments.
    let fd = unsafe {
        syscall(
            SYS_OPENAT2,
            root.as_raw_fd(),
            path.as_ptr(),
            &how,
            std::mem::size_of::<OpenHow>(),
        )
    } as c_int;
    owned_file(fd, "retained openat2 resolution failed")
}

fn owned_file(fd: c_int, error: &'static str) -> Result<File, &'static str> {
    if fd < 0 {
        return Err(error);
    }
    // SAFETY: a successful open/openat2 returns a newly owned descriptor.
    Ok(unsafe { File::from_raw_fd(fd) })
}

fn validate_directory(file: &File, label: &'static str) -> Result<(), &'static str> {
    let metadata = file.metadata().map_err(|_| "directory stat failed")?;
    if !metadata.is_dir() || metadata.uid() != 0 || metadata.mode() & 0o022 != 0 {
        return Err(label);
    }
    Ok(())
}

fn validate_regular(
    file: &File,
    expected_mode: u32,
    label: &'static str,
) -> Result<(), &'static str> {
    let metadata = file.metadata().map_err(|_| "regular stat failed")?;
    if !metadata.is_file()
        || metadata.uid() != 0
        || metadata.nlink() != 1
        || metadata.mode() & 0o777 != expected_mode
    {
        return Err(label);
    }
    Ok(())
}

fn identity(file: &File, error: &'static str) -> Result<Identity, &'static str> {
    file.metadata()
        .map(|metadata| Identity::capture(&metadata))
        .map_err(|_| error)
}

fn compare_identity(
    file: &File,
    expected: Identity,
    error: &'static str,
) -> Result<(), &'static str> {
    if identity(file, error)? != expected {
        return Err(error);
    }
    Ok(())
}

fn adversarial_pause(label: &str) -> Result<(), &'static str> {
    #[cfg(feature = "adversarial-test-hooks")]
    {
        if std::env::var("TRUSTFORGE_NF2_SEALED_PAUSE").as_deref() == Ok(label) {
            let base = format!("/tmp/trustforge-nf2-{}-{label}", std::process::id());
            std::fs::write(format!("{base}.ready"), b"ready")
                .map_err(|_| "adversarial pause marker failed")?;
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
            while !std::path::Path::new(&format!("{base}.continue")).exists() {
                if std::time::Instant::now() >= deadline {
                    return Err("adversarial pause deadline exceeded");
                }
                std::thread::sleep(std::time::Duration::from_millis(2));
            }
        }
    }
    #[cfg(not(feature = "adversarial-test-hooks"))]
    let _ = label;
    Ok(())
}

fn bounded_pread(file: &File, maximum: u64, label: &'static str) -> Result<Vec<u8>, &'static str> {
    let size = file.metadata().map_err(|_| "bounded stat failed")?.size();
    if size > maximum || size > usize::MAX as u64 {
        return Err(label);
    }
    let mut bytes = vec![0_u8; size as usize];
    let mut offset = 0_usize;
    while offset < bytes.len() {
        // SAFETY: the slice is valid for the requested remaining byte count.
        let count = unsafe {
            pread(
                file.as_raw_fd(),
                bytes[offset..].as_mut_ptr(),
                bytes.len() - offset,
                offset as i64,
            )
        };
        if count <= 0 {
            return Err("bounded descriptor read truncated");
        }
        offset += count as usize;
    }
    let mut extra = [0_u8; 1];
    // SAFETY: the one-byte output buffer is valid and offset is bounded above.
    if unsafe { pread(file.as_raw_fd(), extra.as_mut_ptr(), 1, size as i64) } != 0 {
        return Err("descriptor grew during bounded read");
    }
    if file.metadata().map_err(|_| "bounded restat failed")?.size() != size {
        return Err("descriptor size changed during bounded read");
    }
    Ok(bytes)
}
