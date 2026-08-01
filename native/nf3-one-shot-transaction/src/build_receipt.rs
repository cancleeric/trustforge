use crate::Error;
use crate::foundation::BuildIdentity;
use crate::linux::Vfs;
use trustforge_native_sys::sha256::{digest, hex};
use std::io::Read;
use std::os::fd::AsRawFd;
use std::os::unix::fs::MetadataExt;
use std::path::Path;

const RECEIPT_ROOT: &str = "/run/trustforge-nf3-build";
const RECEIPT_NAME: &str = "receipt.v1";
const RECEIPT_MAX: usize = 2048;
const EXE_MAX: usize = 64 * 1024 * 1024;
const BLOCKED_RECEIPT: &str = "0000000000000000000000000000000000000000000000000000000000000000";
const SOURCE: &str = "2c948fcca2c9194fce13e212e449739e5ecaa2b35256e7709b929b7822c85983";
const SOURCE_TREE_RECEIPT: &str =
    "636361176b16b3d85ccce2db3789d69a193a984619df3a76617f34a1dac7700a";
const TOOLCHAIN: &str = "3ddca04f9011db7eba5f0a85103ce62710f6be8d20aca02850aec5774301ee26";
// interim: repin on .83 musl build (PR-B2/B3) — manifest.rs changed in PR-B1.
const EVIDENCE_RLIB: &str = "bada9d9e97d961c7660b55678c518e56d1b3867b36a489d18648e0b6f26aa22b";
const EVIDENCE_PROFILE: &str = "7f53b287a6944a5978b02dfcd35e50b5955be28107ac457369a70d22115f79a5";
// interim: repin on .83 musl build (PR-B2/B3) — manifest.rs changed in PR-B1.
const RELEASE_RLIB: &str = "ef9e4d796488d40fce33188505abfcc8c610cb74ccd2592a410bfc1d3812ec38";
const RELEASE_PROFILE: &str = "5cc871f48193094c28b5df2691c63b2f3c6649686b3573243de5daed90e6e070";

// Covered: missing/mismatched receipts and non-root mutation/forgery.
// Explicit nonclaim: malicious-root receipt authorship; root provisioning is
// trusted by the approved native-evidence threat model.
pub(crate) fn verify() -> Result<BuildIdentity, Error> {
    let vfs = Vfs::open(Path::new(RECEIPT_ROOT))?;
    let bytes = vfs.root().read_readonly(RECEIPT_NAME, RECEIPT_MAX)?;
    parse(&bytes, &executable_sha256()?)
}

fn parse(bytes: &[u8], actual_executable: &str) -> Result<BuildIdentity, Error> {
    if EVIDENCE_RLIB == BLOCKED_RECEIPT || RELEASE_RLIB == BLOCKED_RECEIPT {
        return Err(Error::UnsafeObject("build receipt unbound"));
    }
    let text = std::str::from_utf8(bytes).map_err(|_| Error::UnsafeObject("receipt utf8"))?;
    let mut lines = text
        .strip_suffix('\n')
        .ok_or(Error::UnsafeObject("receipt newline"))?
        .lines();
    if lines.next() != Some("v1") {
        return Err(Error::UnsafeObject("receipt version"));
    }
    let profile = field(&mut lines, "profile")?;
    let executable = field(&mut lines, "executable_sha256")?;
    let source = field(&mut lines, "linked_nf2_source_sha256")?;
    let rlib = field(&mut lines, "linked_nf2_rlib_sha256")?;
    let profile_receipt = field(&mut lines, "profile_receipt_sha256")?;
    let toolchain = field(&mut lines, "toolchain_receipt_sha256")?;
    let tree = field(&mut lines, "source_tree_receipt_sha256")?;
    if lines.next().is_some() {
        return Err(Error::UnsafeObject("receipt extra field"));
    }
    for value in [executable, source, rlib, profile_receipt, toolchain, tree] {
        valid_hex(value)?;
    }
    let expected = match profile {
        "evidence" => (EVIDENCE_RLIB, EVIDENCE_PROFILE),
        "release" => (RELEASE_RLIB, RELEASE_PROFILE),
        _ => return Err(Error::UnsafeObject("receipt profile")),
    };
    if source != SOURCE
        || crate::foundation::linked_nf2_source_sha256() != SOURCE
        || rlib != expected.0
        || profile_receipt != expected.1
        || toolchain != TOOLCHAIN
        || tree != SOURCE_TREE_RECEIPT
        || executable != actual_executable
    {
        return Err(Error::UnsafeObject("build receipt mismatch"));
    }
    Ok(BuildIdentity {
        profile: if profile == "evidence" {
            "evidence"
        } else {
            "release"
        },
        linked_source_sha256: source.to_owned(),
        linked_rlib_sha256: expected.0,
        profile_receipt_sha256: expected.1,
    })
}

fn executable_sha256() -> Result<String, Error> {
    let mut file = std::fs::File::open("/proc/self/exe")?;
    let before = file.metadata()?;
    if !before.file_type().is_file() || before.len() as usize > EXE_MAX {
        return Err(Error::UnsafeObject("unsafe executable"));
    }
    let retained = std::fs::metadata(format!("/proc/self/fd/{}", file.as_raw_fd()))?;
    let named_before = std::fs::metadata("/proc/self/exe")?;
    if metadata_generation(&before) != metadata_generation(&retained)
        || metadata_generation(&before) != metadata_generation(&named_before)
    {
        return Err(Error::IdentityChanged);
    }
    let mut bytes = Vec::with_capacity(before.len() as usize);
    file.read_to_end(&mut bytes)?;
    if bytes.len() != before.len() as usize {
        return Err(Error::IdentityChanged);
    }
    let after = file.metadata()?;
    let named_after = std::fs::metadata("/proc/self/exe")?;
    if metadata_generation(&before) != metadata_generation(&after)
        || metadata_generation(&after) != metadata_generation(&named_after)
    {
        return Err(Error::IdentityChanged);
    }
    Ok(hex(&digest(&bytes, EXE_MAX).map_err(Error::UnsafeObject)?))
}

type MetadataGeneration = (u64, u64, u32, u32, u32, u64, u64, i64, i64, i64, i64);

fn metadata_generation(value: &std::fs::Metadata) -> MetadataGeneration {
    (
        value.dev(),
        value.ino(),
        value.mode(),
        value.uid(),
        value.gid(),
        value.nlink(),
        value.size(),
        value.mtime(),
        value.mtime_nsec(),
        value.ctime(),
        value.ctime_nsec(),
    )
}

fn field<'a>(lines: &mut impl Iterator<Item = &'a str>, name: &str) -> Result<&'a str, Error> {
    lines
        .next()
        .and_then(|line| line.strip_prefix(&format!("{name}=")))
        .ok_or(Error::UnsafeObject("receipt field"))
}

fn valid_hex(value: &str) -> Result<(), Error> {
    if value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
    {
        Ok(())
    } else {
        Err(Error::UnsafeObject("receipt hex"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    fn receipt(extra: &str) -> String {
        format!(
            "v1\nprofile=evidence\nexecutable_sha256={}\nlinked_nf2_source_sha256={SOURCE}\nlinked_nf2_rlib_sha256={EVIDENCE_RLIB}\nprofile_receipt_sha256={EVIDENCE_PROFILE}\ntoolchain_receipt_sha256={TOOLCHAIN}\nsource_tree_receipt_sha256={SOURCE_TREE_RECEIPT}\n{extra}",
            "aa".repeat(32)
        )
    }

    #[test]
    fn canonical_receipt_accepts_and_unknown_or_malformed_frames_fail() {
        let executable = "aa".repeat(32);
        assert_eq!(
            parse(receipt("").as_bytes(), &executable).unwrap().profile,
            "evidence"
        );
        assert!(parse(receipt("unknown=x\n").as_bytes(), &executable).is_err());
        assert!(
            parse(
                receipt("").replace("profile=", "profile =").as_bytes(),
                &executable
            )
            .is_err()
        );
        assert!(parse(receipt("").as_bytes(), &"bb".repeat(32)).is_err());
    }

    #[test]
    fn metadata_generation_detects_mode_and_size_changes() {
        let root = std::path::Path::new("/root").join(format!(
            ".trustforge-nf3-receipt-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::DirBuilder::new()
            .mode(0o700)
            .create(&root)
            .unwrap();
        let root_metadata = std::fs::symlink_metadata(&root).unwrap();
        assert!(root_metadata.file_type().is_dir());
        assert_eq!(root_metadata.uid(), 0);
        assert_eq!(root_metadata.permissions().mode() & 0o777, 0o700);

        let path = root.join("receipt");
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(0o400000) // Linux O_NOFOLLOW.
            .open(&path)
            .unwrap();
        file.write_all(b"a").unwrap();
        file.sync_all().unwrap();
        let before = file.metadata().unwrap();
        file.set_permissions(std::fs::Permissions::from_mode(0o400))
            .unwrap();
        let mode_changed = file.metadata().unwrap();
        assert_ne!(
            metadata_generation(&before),
            metadata_generation(&mode_changed)
        );
        file.set_permissions(std::fs::Permissions::from_mode(0o600))
            .unwrap();
        file.write_all(b"longer").unwrap();
        file.sync_all().unwrap();
        let after = file.metadata().unwrap();
        assert_ne!(metadata_generation(&before), metadata_generation(&after));
        drop(file);
        std::fs::remove_file(path).unwrap();
        std::fs::remove_dir(root).unwrap();
    }
}
