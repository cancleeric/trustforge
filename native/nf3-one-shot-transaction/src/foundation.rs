use crate::Error;
use trustforge_native_sys::sha256::{digest, hex};
use trustforge_nf2_zero_capability_broker::manifest::{
    ACCEPTED_ARCHIVE_SHA256, ACCEPTED_MANIFEST_SHA256, ACCEPTED_RUNTIME_SHA256,
};

const DOMAIN: &[u8] = b"trustforge.native-foundation-binding.v1\0";
// SHA-256("git-commit-sha1\0" || accepted 40-byte NF2 merge identifier).
const NF2_MERGE_SHA256: &str = "d049ced955afca1ea3e426bdc19be0b449a1ab5ba130ac9dce386123dba38bab";

const NF2_SOURCE_TREE_RECEIPT_SHA256: &str =
    "c0ff1fa4d9338074db2068e8fd0924ae13dbe08a166744691a40996cc6f6c019";
// interim: repin on .83 musl build (PR-B2/B3) — manifest.rs changed in PR-B1.
const NF2_LINKED_EVIDENCE_RLIB_SHA256: &str =
    "bada9d9e97d961c7660b55678c518e56d1b3867b36a489d18648e0b6f26aa22b";
// interim: repin on .83 musl build (PR-B2/B3) — manifest.rs changed in PR-B1.
const NF2_LINKED_RELEASE_RLIB_SHA256: &str =
    "ef9e4d796488d40fce33188505abfcc8c610cb74ccd2592a410bfc1d3812ec38";
const NF2_FIXED_TOOLCHAIN_RECEIPT_SHA256: &str =
    "3ddca04f9011db7eba5f0a85103ce62710f6be8d20aca02850aec5774301ee26";
#[cfg(test)]
const NF2_EVIDENCE_PROFILE_RECEIPT_SHA256: &str =
    "7f53b287a6944a5978b02dfcd35e50b5955be28107ac457369a70d22115f79a5";
#[cfg(test)]
const NF2_RELEASE_PROFILE_RECEIPT_SHA256: &str =
    "5cc871f48193094c28b5df2691c63b2f3c6649686b3573243de5daed90e6e070";

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BuildIdentity {
    pub profile: &'static str,
    pub linked_source_sha256: String,
    pub linked_rlib_sha256: &'static str,
    pub profile_receipt_sha256: &'static str,
}

// SHA-256 of this reviewed, canonical fixed-toolchain recipe receipt:
// v1
// merge=7c26416581a8437a6d00d7941357826b2650c474
// target=x86_64-unknown-linux-musl
// profile=release
// locked=true
// rust_release=1.96.0
// rust_commit=ac68faa20c58cbccd01ee7208bf3b6e93a7d7f96
// target_receipt=49c92219312e619b6b49b9355425fa84c21da02fb38828819ba41ecc3b3489d1
// target_entries=738cd55ce0397d85b911f5171ef68c48b320465f58a8e2fd65e4067a2668979a
// cargo_lock=28f0970413222e7d6c65da3aa379e5ca1cbb8c30345a16d04204762ac1e30cbb
// cargo_toml=3b28816d29673cf4e4a1b6554fa42d367e796c4b1dd3850320af335cf73033d2
// source_remap=/workspace/trustforge
/// Returns the sole accepted NF1+NF2 foundation identity.
///
/// The caller cannot provide or override any component.
pub fn accepted_foundation_sha256() -> Result<String, Error> {
    if !accepted_receipts_bound() {
        return Err(Error::UnsafeObject("foundation build receipt unbound"));
    }
    let identity = accepted_build_identity()?;
    Ok(foundation_sha256(&identity))
}

fn accepted_receipts_bound() -> bool {
    const BLOCKED: &str =
        "0000000000000000000000000000000000000000000000000000000000000000";
    NF2_LINKED_EVIDENCE_RLIB_SHA256 != BLOCKED
        && NF2_LINKED_RELEASE_RLIB_SHA256 != BLOCKED
}

fn foundation_sha256(identity: &BuildIdentity) -> String {
    let nf2_build = linked_nf2_build_sha256(identity);
    let fields = [
        ("nf1_manifest_sha256", ACCEPTED_MANIFEST_SHA256),
        ("nf1_runtime_sha256", ACCEPTED_RUNTIME_SHA256),
        ("nf1_archive_sha256", ACCEPTED_ARCHIVE_SHA256),
        ("nf2_merge_sha256", NF2_MERGE_SHA256),
        ("nf2_build_sha256", nf2_build.as_str()),
    ];
    let mut canonical = DOMAIN.to_vec();
    for (name, value) in fields {
        assert!(valid_lower_hex(value), "invalid compiled foundation anchor");
        frame(&mut canonical, name.as_bytes());
        frame(&mut canonical, value.as_bytes());
    }
    hex(&digest(&canonical, 1024).expect("compiled foundation binding is bounded"))
}

pub fn accepted_build_identity() -> Result<BuildIdentity, Error> {
    #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
    {
        crate::build_receipt::verify()
    }
    #[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
    {
        Err(Error::UnsupportedPlatform)
    }
}

fn linked_nf2_build_sha256(identity: &BuildIdentity) -> String {
    let mut canonical = b"trustforge.nf2.linked-build.v1\0".to_vec();
    for (name, value) in [
        (
            "linked_source_sha256",
            identity.linked_source_sha256.as_str(),
        ),
        (
            "fixed_toolchain_receipt_sha256",
            NF2_FIXED_TOOLCHAIN_RECEIPT_SHA256,
        ),
        ("source_tree_receipt_sha256", NF2_SOURCE_TREE_RECEIPT_SHA256),
        ("profile_receipt_sha256", identity.profile_receipt_sha256),
        ("linked_profile_rlib_sha256", identity.linked_rlib_sha256),
    ] {
        frame(&mut canonical, name.as_bytes());
        frame(&mut canonical, value.as_bytes());
    }
    hex(&digest(&canonical, 1024).expect("compiled NF2 build binding is bounded"))
}

pub(crate) fn linked_nf2_source_sha256() -> String {
    const SOURCES: [(&str, &[u8]); 11] = [
        // The workspace-root Cargo.lock is the authoritative resolution for
        // every member build (nf2/nf3/hermetic-package share it); per-crate
        // locks are no longer maintained once the workspace exists.
        (
            "Cargo.lock",
            include_bytes!("../../Cargo.lock"),
        ),
        (
            "Cargo.toml",
            include_bytes!("../../nf2-zero-capability-broker/Cargo.toml"),
        ),
        (
            "src/canonical_json.rs",
            include_bytes!("../../nf2-zero-capability-broker/src/canonical_json.rs"),
        ),
        (
            "src/capability.rs",
            include_bytes!("../../nf2-zero-capability-broker/src/capability.rs"),
        ),
        (
            "src/lib.rs",
            include_bytes!("../../nf2-zero-capability-broker/src/lib.rs"),
        ),
        (
            "src/linux.rs",
            include_bytes!("../../nf2-zero-capability-broker/src/linux.rs"),
        ),
        (
            "src/linux/live.rs",
            include_bytes!("../../nf2-zero-capability-broker/src/linux/live.rs"),
        ),
        (
            "src/linux/process.rs",
            include_bytes!("../../nf2-zero-capability-broker/src/linux/process.rs"),
        ),
        (
            "src/linux/sealed.rs",
            include_bytes!("../../nf2-zero-capability-broker/src/linux/sealed.rs"),
        ),
        (
            "src/main.rs",
            include_bytes!("../../nf2-zero-capability-broker/src/main.rs"),
        ),
        (
            "src/manifest.rs",
            include_bytes!("../../nf2-zero-capability-broker/src/manifest.rs"),
        ),
    ];
    let mut canonical = b"trustforge.nf2.linked-source.v1\0".to_vec();
    for (name, bytes) in SOURCES {
        canonical.extend_from_slice(&(name.len() as u32).to_be_bytes());
        canonical.extend_from_slice(name.as_bytes());
        canonical.extend_from_slice(&(bytes.len() as u64).to_be_bytes());
        canonical.extend_from_slice(bytes);
    }
    hex(&digest(&canonical, 1_000_000).expect("compiled NF2 sources are bounded"))
}

fn frame(output: &mut Vec<u8>, value: &[u8]) {
    output.extend_from_slice(
        &u32::try_from(value.len())
            .expect("foundation field is bounded")
            .to_be_bytes(),
    );
    output.extend_from_slice(value);
}

fn valid_lower_hex(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepted_binding_is_a_fixed_lowercase_digest() {
        assert!(accepted_receipts_bound());
        let identity = evidence_identity();
        let first = foundation_sha256(&identity);
        assert!(valid_lower_hex(&first));
        assert_eq!(
            first,
            "6001aad854eec3f9c353da8330ff72eb8e37aa8affafc449f777af5392c1e325"
        );
        assert_eq!(first, foundation_sha256(&identity));
    }

    #[test]
    fn every_allowed_profile_has_an_exact_distinct_golden() {
        assert!(accepted_receipts_bound());
        let linked = linked_nf2_source_sha256();
        let evidence = BuildIdentity {
            profile: "evidence",
            linked_source_sha256: linked.clone(),
            linked_rlib_sha256: NF2_LINKED_EVIDENCE_RLIB_SHA256,
            profile_receipt_sha256: NF2_EVIDENCE_PROFILE_RECEIPT_SHA256,
        };
        let release = BuildIdentity {
            profile: "release",
            linked_source_sha256: linked,
            linked_rlib_sha256: NF2_LINKED_RELEASE_RLIB_SHA256,
            profile_receipt_sha256: NF2_RELEASE_PROFILE_RECEIPT_SHA256,
        };
        assert_eq!(
            foundation_sha256(&evidence),
            "6001aad854eec3f9c353da8330ff72eb8e37aa8affafc449f777af5392c1e325"
        );
        assert_eq!(
            foundation_sha256(&release),
            "65ee3e0e03daacc81cd209c88c7e30e116432aadf42c7fb29f884be8c260f5c0"
        );
        assert_ne!(foundation_sha256(&evidence), foundation_sha256(&release));
    }

    #[test]
    fn source_tree_receipt_is_platform_independent_framing() {
        let mut canonical = b"trustforge.nf2.source-tree-receipt.v1\0".to_vec();
        for (name, value) in [
            (
                "git_subtree_oid_sha1",
                "ce3e20c5875e5fdc59e60472decbc256b9649484",
            ),
            (
                "linked_source_sha256",
                "f32c31eec9f594d72e274faba8daac34cc0df7cc677f187cf3a963e9fc626b1b",
            ),
        ] {
            frame(&mut canonical, name.as_bytes());
            frame(&mut canonical, value.as_bytes());
        }
        assert_eq!(
            hex(&digest(&canonical, 1024).unwrap()),
            NF2_SOURCE_TREE_RECEIPT_SHA256
        );
    }

    #[test]
    fn framing_and_domain_are_order_sensitive() {
        let identity = evidence_identity();
        let accepted = foundation_sha256(&identity);
        let mut reordered = DOMAIN.to_vec();
        let nf2_build = linked_nf2_build_sha256(&identity);
        let fields = [
            ("nf1_manifest_sha256", ACCEPTED_MANIFEST_SHA256),
            ("nf1_runtime_sha256", ACCEPTED_RUNTIME_SHA256),
            ("nf1_archive_sha256", ACCEPTED_ARCHIVE_SHA256),
            ("nf2_merge_sha256", NF2_MERGE_SHA256),
            ("nf2_build_sha256", nf2_build.as_str()),
        ];
        for (name, value) in fields.into_iter().rev() {
            frame(&mut reordered, name.as_bytes());
            frame(&mut reordered, value.as_bytes());
        }
        assert_ne!(
            accepted,
            hex(&digest(&reordered, 1024).expect("bounded test vector"))
        );

        let mut wrong_domain = b"trustforge.native-foundation-binding.v2\0".to_vec();
        for (name, value) in fields {
            frame(&mut wrong_domain, name.as_bytes());
            frame(&mut wrong_domain, value.as_bytes());
        }
        assert_ne!(
            accepted,
            hex(&digest(&wrong_domain, 1024).expect("bounded test vector"))
        );
    }

    fn evidence_identity() -> BuildIdentity {
        BuildIdentity {
            profile: "evidence",
            linked_source_sha256: linked_nf2_source_sha256(),
            linked_rlib_sha256: NF2_LINKED_EVIDENCE_RLIB_SHA256,
            profile_receipt_sha256: NF2_EVIDENCE_PROFILE_RECEIPT_SHA256,
        }
    }
}
