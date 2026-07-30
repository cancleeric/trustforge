use crate::Error;
use std::ffi::{CStr, CString};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::ffi::OsStrExt;
use std::path::{Component, Path};

const MAX_NAME: usize = 255;
const MAX_ENTRIES: usize = 4096;
const MAX_PAYLOAD: usize = 64 * 1024;
const O_RDONLY: u64 = 0;
const O_WRONLY: u64 = 1;
const O_CREAT: u64 = 0o100;
const O_EXCL: u64 = 0o200;
const O_CLOEXEC: u64 = 0o2000000;
const O_DIRECTORY: u64 = 0o200000;
const O_NOFOLLOW: u64 = 0o400000;
const O_NONBLOCK: u64 = 0o4000;
const RESOLVE_NO_MAGICLINKS: u64 = 0x02;
const RESOLVE_NO_SYMLINKS: u64 = 0x04;
const RESOLVE_BENEATH: u64 = 0x08;
const AT_SYMLINK_NOFOLLOW: i32 = 0x100;
const RENAME_NOREPLACE: u32 = 1;

pub struct Vfs {
    root: Dir,
}
pub struct Dir {
    fd: OwnedFd,
    uid: u32,
}

#[derive(Debug, Clone, Eq, PartialEq)]
pub struct Entry {
    pub name: String,
    pub device: u64,
    pub inode: u64,
    pub size: u64,
}

impl Vfs {
    pub fn open(root: &Path) -> Result<Self, Error> {
        if sys::geteuid() != 0 {
            return Err(Error::UnsafeObject("process is not root"));
        }
        if !root.is_absolute() {
            return Err(Error::UnsafeObject("root is not absolute"));
        }
        let mut fd = sys::open(
            CString::new("/").unwrap().as_c_str(),
            O_RDONLY | O_DIRECTORY | O_CLOEXEC,
            0,
        )?;
        verify_dir(&fd, 0, false)?;
        for component in root.components() {
            match component {
                Component::RootDir => {}
                Component::Normal(name) => {
                    let name = checked_bytes(name.as_bytes())?;
                    fd = sys::openat2(&fd, name.as_c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC, 0)?;
                    verify_dir(&fd, 0, true)?;
                }
                _ => return Err(Error::UnsafeObject("non-normal root component")),
            }
        }
        verify_dir_exact(&fd, 0, 0o700)?;
        Ok(Self {
            root: Dir { fd, uid: 0 },
        })
    }
    pub fn root(&self) -> &Dir {
        &self.root
    }
}

impl Dir {
    pub fn mkdir(&self, name: &str) -> Result<Dir, Error> {
        let name = checked_name(name)?;
        sys::mkdirat(&self.fd, name.as_c_str(), 0o700)?;
        let fd = sys::openat2(
            &self.fd,
            name.as_c_str(),
            O_RDONLY | O_DIRECTORY | O_CLOEXEC,
            0,
        )?;
        sys::fchmod(&fd, 0o700)?;
        verify_dir_exact(&fd, self.uid, 0o700)?;
        sys::fsync(&self.fd)?;
        Ok(Dir { fd, uid: self.uid })
    }

    pub fn open_dir(&self, name: &str) -> Result<Dir, Error> {
        let name = checked_name(name)?;
        let fd = sys::openat2(
            &self.fd,
            name.as_c_str(),
            O_RDONLY | O_DIRECTORY | O_CLOEXEC,
            0,
        )?;
        verify_dir(&fd, self.uid, true)?;
        Ok(Dir { fd, uid: self.uid })
    }

    pub fn create_new(&self, name: &str, bytes: &[u8], max: usize) -> Result<Entry, Error> {
        if max > MAX_PAYLOAD || bytes.len() > max {
            return Err(Error::UnsafeObject("payload exceeds bound"));
        }
        let name = checked_name(name)?;
        let fd = sys::openat2(
            &self.fd,
            name.as_c_str(),
            O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW,
            0o600,
        )?;
        sys::fchmod(&fd, 0o600)?;
        verify_file(&fd, self.uid, Some(0o600), None)?;
        write_loop(&fd, bytes)?;
        sys::fdatasync(&fd)?;
        let stat = verify_file(&fd, self.uid, Some(0o600), None)?;
        let named = sys::statat(&self.fd, name.as_c_str(), AT_SYMLINK_NOFOLLOW)?;
        if identity(&stat) != identity(&named) {
            return Err(Error::IdentityChanged);
        }
        sys::fsync(&self.fd)?;
        Ok(entry(name.to_str().map_err(|_| Error::InvalidName)?, &stat))
    }

    pub fn rename(&self, old: &str, new: &str) -> Result<(), Error> {
        let old = checked_name(old)?;
        let new = checked_name(new)?;
        let source = self.open_verified(old.as_c_str(), None)?;
        let before = sys::fstat(&source)?;
        sys::fdatasync(&source)?;
        sys::renameat2(
            &self.fd,
            old.as_c_str(),
            &self.fd,
            new.as_c_str(),
            RENAME_NOREPLACE,
        )?;
        let validation = (|| {
            #[cfg(test)]
            if FORCE_POST_RENAME_FAILURE.load(std::sync::atomic::Ordering::SeqCst) {
                return Err(Error::IdentityChanged);
            }
            let after = sys::fstat(&self.open_verified(new.as_c_str(), None)?)?;
            if identity(&before) != identity(&after) {
                return Err(Error::IdentityChanged);
            }
            Ok(())
        })();
        sys::fsync(&self.fd)?;
        validation
    }

    pub fn read(&self, name: &str, max: usize) -> Result<Vec<u8>, Error> {
        if max > MAX_PAYLOAD {
            return Err(Error::UnsafeObject("payload bound exceeds cap"));
        }
        let name = checked_name(name)?;
        let fd = self.open_verified(name.as_c_str(), Some(max))?;
        let before = sys::fstat(&fd)?;
        let capacity = max
            .checked_add(1)
            .ok_or(Error::UnsafeObject("payload bound overflow"))?;
        let mut bytes = Vec::new();
        bytes
            .try_reserve_exact(capacity)
            .map_err(|_| Error::UnsafeObject("payload allocation"))?;
        bytes.resize(capacity, 0);
        let mut count = 0;
        while count < bytes.len() {
            let read = match sys::read(&fd, &mut bytes[count..]) {
                Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::Interrupted => {
                    continue;
                }
                result => result?,
            };
            if read == 0 {
                break;
            }
            count += read;
        }
        bytes.truncate(count);
        if bytes.len() > max {
            return Err(Error::UnsafeObject("file exceeds bound"));
        }
        let after = sys::fstat(&fd)?;
        if identity(&before) != identity(&after) {
            return Err(Error::IdentityChanged);
        }
        Ok(bytes)
    }

    pub fn entries(&self) -> Result<Vec<Entry>, Error> {
        let first = self.scan_once()?;
        #[cfg(test)]
        if FORCE_SCAN_MUTATION.load(std::sync::atomic::Ordering::SeqCst) {
            self.create_new("forced-mutation", b"x", 1)?;
        }
        let second = self.scan_once()?;
        if first != second {
            return Err(Error::UnsafeObject("directory mutated during scan"));
        }
        Ok(second.1)
    }

    fn scan_once(&self) -> Result<(DirectoryGeneration, Vec<Entry>), Error> {
        let dot = CString::new(".").unwrap();
        let scan = sys::openat2(
            &self.fd,
            dot.as_c_str(),
            O_RDONLY | O_DIRECTORY | O_CLOEXEC,
            0,
        )?;
        let before = generation(&sys::fstat(&scan)?);
        let mut output = Vec::new();
        let mut buffer = [0_u8; 8192];
        loop {
            let count = match sys::getdents64(&scan, &mut buffer) {
                Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::Interrupted => {
                    continue;
                }
                result => result?,
            };
            if count == 0 {
                break;
            }
            let mut offset = 0;
            while offset < count {
                if count - offset < 19 {
                    return Err(Error::UnsafeObject("truncated dirent"));
                }
                let record = &buffer[offset..count];
                let reclen = u16::from_ne_bytes([record[16], record[17]]) as usize;
                if reclen < 20 || offset + reclen > count {
                    return Err(Error::UnsafeObject("invalid dirent"));
                }
                let names = &record[19..reclen];
                let end = names
                    .iter()
                    .position(|b| *b == 0)
                    .ok_or(Error::InvalidName)?;
                let name = std::str::from_utf8(&names[..end]).map_err(|_| Error::InvalidName)?;
                if name != "." && name != ".." {
                    if output.len() == MAX_ENTRIES {
                        return Err(Error::TooManyEntries);
                    }
                    let checked = checked_name(name)?;
                    let fd = self.open_verified(checked.as_c_str(), None)?;
                    output.push(entry(name, &sys::fstat(&fd)?));
                }
                offset += reclen;
            }
        }
        output.sort_by(|a, b| a.name.cmp(&b.name));
        let after = generation(&sys::fstat(&scan)?);
        if before != after {
            return Err(Error::UnsafeObject("directory mutated during scan"));
        }
        Ok((after, output))
    }

    fn open_verified(&self, name: &CStr, max: Option<usize>) -> Result<OwnedFd, Error> {
        let fd = sys::openat2(
            &self.fd,
            name,
            O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK,
            0,
        )?;
        let opened = verify_file(&fd, self.uid, Some(0o600), max)?;
        let named = sys::statat(&self.fd, name, AT_SYMLINK_NOFOLLOW)?;
        if identity(&opened) != identity(&named) {
            return Err(Error::IdentityChanged);
        }
        Ok(fd)
    }
}

fn checked_name(name: &str) -> Result<CString, Error> {
    checked_bytes(name.as_bytes())
}
fn checked_bytes(name: &[u8]) -> Result<CString, Error> {
    if name.is_empty()
        || name.len() > MAX_NAME
        || name == b"."
        || name == b".."
        || name.contains(&b'/')
    {
        return Err(Error::InvalidName);
    }
    CString::new(name).map_err(|_| Error::InvalidName)
}

fn verify_dir(fd: &OwnedFd, uid: u32, enforce_mode: bool) -> Result<sys::Stat, Error> {
    let stat = sys::fstat(fd)?;
    if stat.mode & 0o170000 != 0o040000
        || stat.uid != uid
        || stat.gid != 0
        || (enforce_mode && stat.mode & 0o022 != 0)
    {
        return Err(Error::UnsafeObject("unsafe directory"));
    }
    Ok(stat)
}
fn verify_dir_exact(fd: &OwnedFd, uid: u32, mode: u32) -> Result<sys::Stat, Error> {
    let stat = verify_dir(fd, uid, true)?;
    if stat.mode & 0o777 != mode {
        return Err(Error::UnsafeObject("directory mode mismatch"));
    }
    Ok(stat)
}
fn verify_file(
    fd: &OwnedFd,
    uid: u32,
    mode: Option<u32>,
    max: Option<usize>,
) -> Result<sys::Stat, Error> {
    let stat = sys::fstat(fd)?;
    if stat.mode & 0o170000 != 0o100000
        || stat.uid != uid
        || stat.gid != 0
        || stat.nlink != 1
        || mode.is_some_and(|m| stat.mode & 0o777 != m)
        || max.is_some_and(|m| stat.size < 0 || stat.size as usize > m)
    {
        return Err(Error::UnsafeObject("unsafe regular file"));
    }
    Ok(stat)
}
fn identity(stat: &sys::Stat) -> (u64, u64) {
    (stat.dev, stat.ino)
}
#[derive(Clone, Debug, Eq, PartialEq)]
struct DirectoryGeneration {
    dev: u64,
    ino: u64,
    mode: u32,
    uid: u32,
    gid: u32,
    nlink: u64,
    size: i64,
    mtime_sec: i64,
    mtime_nsec: i64,
    ctime_sec: i64,
    ctime_nsec: i64,
}
fn generation(s: &sys::Stat) -> DirectoryGeneration {
    DirectoryGeneration {
        dev: s.dev,
        ino: s.ino,
        mode: s.mode,
        uid: s.uid,
        gid: s.gid,
        nlink: s.nlink,
        size: s.size,
        mtime_sec: s.mtime_sec,
        mtime_nsec: s.mtime_nsec,
        ctime_sec: s.ctime_sec,
        ctime_nsec: s.ctime_nsec,
    }
}
#[cfg(test)]
static FORCE_POST_RENAME_FAILURE: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);
#[cfg(test)]
static FORCE_SCAN_MUTATION: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);
fn entry(name: &str, stat: &sys::Stat) -> Entry {
    Entry {
        name: name.into(),
        device: stat.dev,
        inode: stat.ino,
        size: stat.size as u64,
    }
}
fn write_loop(fd: &OwnedFd, mut bytes: &[u8]) -> Result<(), Error> {
    while !bytes.is_empty() {
        let count = match sys::write(fd, bytes) {
            Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            result => result?,
        };
        if count == 0 {
            return Err(std::io::Error::from(std::io::ErrorKind::WriteZero).into());
        }
        bytes = &bytes[count..];
    }
    Ok(())
}

mod sys {
    use super::*;
    use std::ffi::{c_int, c_long};

    const SYS_READ: c_long = 0;
    const SYS_WRITE: c_long = 1;
    const SYS_OPEN: c_long = 2;
    const SYS_FSTAT: c_long = 5;
    const SYS_FSYNC: c_long = 74;
    const SYS_FDATASYNC: c_long = 75;
    const SYS_FCHMOD: c_long = 91;
    const SYS_GETEUID: c_long = 107;
    const SYS_GETDENTS64: c_long = 217;
    #[cfg(test)]
    const SYS_MKNODAT: c_long = 259;
    const SYS_MKDIRAT: c_long = 258;
    const SYS_NEWFSTATAT: c_long = 262;
    const SYS_RENAMEAT2: c_long = 316;
    const SYS_OPENAT2: c_long = 437;
    #[repr(C)]
    struct OpenHow {
        flags: u64,
        mode: u64,
        resolve: u64,
    }
    #[repr(C)]
    #[derive(Clone, Copy)]
    pub struct Stat {
        pub dev: u64,
        pub ino: u64,
        pub nlink: u64,
        pub mode: u32,
        pub uid: u32,
        pub gid: u32,
        _pad0: u32,
        pub rdev: u64,
        pub size: i64,
        pub blksize: i64,
        pub blocks: i64,
        _atime_sec: i64,
        _atime_nsec: i64,
        pub mtime_sec: i64,
        pub mtime_nsec: i64,
        pub ctime_sec: i64,
        pub ctime_nsec: i64,
        _reserved: [i64; 3],
    }
    unsafe extern "C" {
        fn syscall(number: c_long, ...) -> c_long;
    }
    fn cvt(value: c_long) -> Result<c_long, Error> {
        if value == -1 {
            Err(std::io::Error::last_os_error().into())
        } else {
            Ok(value)
        }
    }
    fn owned(value: c_long) -> Result<OwnedFd, Error> {
        Ok(unsafe { OwnedFd::from_raw_fd(cvt(value)? as c_int) })
    }
    pub fn geteuid() -> u32 {
        unsafe { syscall(SYS_GETEUID) as u32 }
    }
    pub fn open(path: &CStr, flags: u64, mode: u32) -> Result<OwnedFd, Error> {
        owned(unsafe { syscall(SYS_OPEN, path.as_ptr(), flags, mode) })
    }
    pub fn openat2(dir: &OwnedFd, path: &CStr, flags: u64, mode: u32) -> Result<OwnedFd, Error> {
        let how = OpenHow {
            flags,
            mode: mode as u64,
            resolve: RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS,
        };
        owned(unsafe {
            syscall(
                SYS_OPENAT2,
                dir.as_raw_fd(),
                path.as_ptr(),
                &how,
                std::mem::size_of::<OpenHow>(),
            )
        })
    }
    macro_rules! unit_fd {
        ($name:ident,$nr:ident) => {
            pub fn $name(fd: &OwnedFd) -> Result<(), Error> {
                cvt(unsafe { syscall($nr, fd.as_raw_fd()) }).map(|_| ())
            }
        };
    }
    unit_fd!(fsync, SYS_FSYNC);
    unit_fd!(fdatasync, SYS_FDATASYNC);
    pub fn fchmod(fd: &OwnedFd, mode: u32) -> Result<(), Error> {
        cvt(unsafe { syscall(SYS_FCHMOD, fd.as_raw_fd(), mode) }).map(|_| ())
    }
    pub fn mkdirat(dir: &OwnedFd, path: &CStr, mode: u32) -> Result<(), Error> {
        cvt(unsafe { syscall(SYS_MKDIRAT, dir.as_raw_fd(), path.as_ptr(), mode) }).map(|_| ())
    }
    #[cfg(test)]
    pub fn mkfifoat(dir: &OwnedFd, path: &CStr) -> Result<(), Error> {
        cvt(unsafe { syscall(SYS_MKNODAT, dir.as_raw_fd(), path.as_ptr(), 0o010600, 0) })
            .map(|_| ())
    }
    pub fn renameat2(
        a: &OwnedFd,
        old: &CStr,
        b: &OwnedFd,
        new: &CStr,
        flags: u32,
    ) -> Result<(), Error> {
        cvt(unsafe {
            syscall(
                SYS_RENAMEAT2,
                a.as_raw_fd(),
                old.as_ptr(),
                b.as_raw_fd(),
                new.as_ptr(),
                flags,
            )
        })
        .map(|_| ())
    }
    fn blank_stat() -> Stat {
        unsafe { std::mem::zeroed() }
    }
    pub fn fstat(fd: &OwnedFd) -> Result<Stat, Error> {
        let mut stat = blank_stat();
        cvt(unsafe { syscall(SYS_FSTAT, fd.as_raw_fd(), &mut stat) })?;
        Ok(stat)
    }
    pub fn statat(dir: &OwnedFd, path: &CStr, flags: i32) -> Result<Stat, Error> {
        let mut stat = blank_stat();
        cvt(unsafe {
            syscall(
                SYS_NEWFSTATAT,
                dir.as_raw_fd(),
                path.as_ptr(),
                &mut stat,
                flags,
            )
        })?;
        Ok(stat)
    }
    pub fn write(fd: &OwnedFd, bytes: &[u8]) -> Result<usize, Error> {
        cvt(unsafe { syscall(SYS_WRITE, fd.as_raw_fd(), bytes.as_ptr(), bytes.len()) })
            .map(|n| n as usize)
    }
    pub fn read(fd: &OwnedFd, bytes: &mut [u8]) -> Result<usize, Error> {
        cvt(unsafe { syscall(SYS_READ, fd.as_raw_fd(), bytes.as_mut_ptr(), bytes.len()) })
            .map(|n| n as usize)
    }
    pub fn getdents64(fd: &OwnedFd, bytes: &mut [u8]) -> Result<usize, Error> {
        cvt(unsafe {
            syscall(
                SYS_GETDENTS64,
                fd.as_raw_fd(),
                bytes.as_mut_ptr(),
                bytes.len(),
            )
        })
        .map(|n| n as usize)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{PermissionsExt, symlink};
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    static TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    struct Fixture {
        path: std::path::PathBuf,
        vfs: Vfs,
    }
    impl Fixture {
        fn new() -> Option<Self> {
            if sys::geteuid() != 0 {
                return None;
            }
            let path = Path::new("/root").join(format!(
                ".trustforge-nf3-vfs-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir(&path).unwrap();
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
            Some(Self {
                vfs: Vfs::open(&path).unwrap(),
                path,
            })
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.path);
            let _ = std::fs::remove_dir_all(self.path.with_extension("old"));
        }
    }

    #[test]
    fn create_read_rename_enumerate_identity() {
        let _guard = TEST_LOCK.lock().unwrap();
        let Some(f) = Fixture::new() else { return };
        let dir = f.vfs.root().mkdir("records").unwrap();
        let made = dir.create_new("a.tmp", b"payload", 32).unwrap();
        assert_eq!(dir.read("a.tmp", 32).unwrap(), b"payload");
        dir.rename("a.tmp", "a.record").unwrap();
        let entries = dir.entries().unwrap();
        assert_eq!((entries.len(), entries[0].inode), (1, made.inode));
        assert_eq!(dir.entries().unwrap(), entries);
        dir.create_new("occupied", b"existing", 32).unwrap();
        dir.create_new("candidate", b"new", 32).unwrap();
        assert!(dir.rename("candidate", "occupied").is_err());
        assert_eq!(dir.read("occupied", 32).unwrap(), b"existing");
        assert_eq!(dir.read("candidate", 32).unwrap(), b"new");
        assert_eq!(dir.entries().unwrap().len(), 3);
    }

    #[test]
    fn rejects_symlink_hardlink_fifo_and_permissions() {
        let _guard = TEST_LOCK.lock().unwrap();
        let Some(f) = Fixture::new() else { return };
        let path = f.path.join("records");
        let dir = f.vfs.root().mkdir("records").unwrap();
        dir.create_new("good", b"x", 8).unwrap();
        symlink("good", path.join("link")).unwrap();
        assert!(dir.read("link", 8).is_err());
        std::fs::hard_link(path.join("good"), path.join("hard")).unwrap();
        assert!(dir.read("good", 8).is_err());
        assert!(dir.read("hard", 8).is_err());
        sys::mkfifoat(&dir.fd, checked_name("fifo").unwrap().as_c_str()).unwrap();
        assert!(dir.read("fifo", 8).is_err());
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o722)).unwrap();
        assert!(f.vfs.root().open_dir("records").is_err());
    }

    #[test]
    fn retained_fd_resists_path_substitution() {
        let _guard = TEST_LOCK.lock().unwrap();
        let Some(f) = Fixture::new() else { return };
        f.vfs.root().create_new("sentinel", b"old", 8).unwrap();
        let old = f.path.with_extension("old");
        std::fs::rename(&f.path, &old).unwrap();
        std::fs::create_dir(&f.path).unwrap();
        std::fs::set_permissions(&f.path, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::fs::write(f.path.join("sentinel"), b"new").unwrap();
        std::fs::set_permissions(
            f.path.join("sentinel"),
            std::fs::Permissions::from_mode(0o600),
        )
        .unwrap();
        assert_eq!(f.vfs.root().read("sentinel", 8).unwrap(), b"old");
    }

    #[test]
    fn rejects_symlinked_root_unsafe_names_and_bounds() {
        let _guard = TEST_LOCK.lock().unwrap();
        let Some(f) = Fixture::new() else { return };
        std::fs::set_permissions(&f.path, std::fs::Permissions::from_mode(0o750)).unwrap();
        assert!(Vfs::open(&f.path).is_err());
        std::fs::set_permissions(&f.path, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::os::unix::fs::chown(&f.path, None, Some(1)).unwrap();
        assert!(Vfs::open(&f.path).is_err());
        std::os::unix::fs::chown(&f.path, None, Some(0)).unwrap();
        let link = f.path.with_extension("link");
        symlink(&f.path, &link).unwrap();
        assert!(Vfs::open(&link).is_err());
        std::fs::remove_file(link).unwrap();
        for name in ["", ".", "..", "a/b"] {
            assert!(f.vfs.root().create_new(name, b"x", 8).is_err());
        }
        assert!(f.vfs.root().create_new("large", &[0; 9], 8).is_err());
        assert!(
            f.vfs
                .root()
                .create_new("overflow", b"x", usize::MAX)
                .is_err()
        );
        assert!(f.vfs.root().read("missing", usize::MAX).is_err());
    }

    #[test]
    fn mutation_blocks_scan_and_postrename_failure_still_publishes() {
        let _guard = TEST_LOCK.lock().unwrap();
        let Some(f) = Fixture::new() else { return };
        let dir = f.vfs.root().mkdir("records").unwrap();
        dir.create_new("source", b"x", 8).unwrap();
        FORCE_SCAN_MUTATION.store(true, Ordering::SeqCst);
        assert!(dir.entries().is_err());
        FORCE_SCAN_MUTATION.store(false, Ordering::SeqCst);
        assert!(
            dir.entries()
                .unwrap()
                .iter()
                .any(|e| e.name == "forced-mutation")
        );

        FORCE_POST_RENAME_FAILURE.store(true, Ordering::SeqCst);
        assert!(matches!(
            dir.rename("source", "published"),
            Err(Error::IdentityChanged)
        ));
        FORCE_POST_RENAME_FAILURE.store(false, Ordering::SeqCst);
        assert_eq!(dir.read("published", 8).unwrap(), b"x");
        assert!(dir.read("source", 8).is_err());
    }
}
