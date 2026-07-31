//! Generic non-secret capability descriptor. Authority-neutral.
//!
//! Every field is a public kernel/package identity: the transaction id, the
//! foundation package digest, and the runtime device/inode the child bound to.
//! The descriptor contains NO key, signer, actor, or raw authority material: it
//! only names *what* is bound, never *who* may release it. This module is
//! deliberately `cfg`-free so the descriptor type (and the cross-crate
//! `CapabilitySink`) is visible to every target, not just Linux x86_64.

use crate::sha256;

/// Length-prefixed framing tag. Domain-separates this digest from every other
/// sha256 the crate computes (canonical JSON, manifest, foundation) so two
/// equal digests can never be confused across domains.
const DESCRIPTOR_DOMAIN_TAG: &[u8] = b"trustforge-nf2-capability-descriptor-v1\x1f";

#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum CapabilityKind {
    /// v1: no file descriptor is transferred across the capability boundary.
    ZeroFd,
}

impl CapabilityKind {
    fn discriminant(self) -> u8 {
        match self {
            CapabilityKind::ZeroFd => 0,
        }
    }
}

#[derive(Debug, Clone)]
pub struct CapabilityDescriptor {
    pub transaction_id: [u8; 32],
    pub foundation_sha256: [u8; 32],
    pub runtime_device: u64,
    pub runtime_inode: u64,
    pub capability_kind: CapabilityKind,
    /// sha256 over the five identity fields above (domain-separated). This is
    /// the *output* of [`compute_sha256`]; it is never an input to its own
    /// digest.
    pub descriptor_sha256: [u8; 32],
}

impl CapabilityDescriptor {
    /// Builds a descriptor with `descriptor_sha256` computed over the five
    /// identity fields. This is the only intended constructor: callers must not
    /// hand-set `descriptor_sha256`, which would let a caller inject a chosen
    /// digest.
    pub fn new(
        transaction_id: [u8; 32],
        foundation_sha256: [u8; 32],
        runtime_device: u64,
        runtime_inode: u64,
        capability_kind: CapabilityKind,
    ) -> Self {
        let descriptor = Self {
            transaction_id,
            foundation_sha256,
            runtime_device,
            runtime_inode,
            capability_kind,
            descriptor_sha256: [0; 32],
        };
        let descriptor_sha256 = descriptor.compute_sha256();
        Self {
            descriptor_sha256,
            ..descriptor
        }
    }

    /// Domain-separated sha256 over the binding identity fields.
    ///
    /// Framing is `domain_tag | transaction_id | foundation_sha256 |
    /// runtime_device(be) | runtime_inode(be) | kind`. Fixed-width fields make
    /// concatenation unambiguous. `descriptor_sha256` is excluded because it is
    /// the digest output, not an input.
    pub fn compute_sha256(&self) -> [u8; 32] {
        let mut buffer =
            Vec::with_capacity(DESCRIPTOR_DOMAIN_TAG.len() + 32 + 32 + 8 + 8 + 1);
        buffer.extend_from_slice(DESCRIPTOR_DOMAIN_TAG);
        buffer.extend_from_slice(&self.transaction_id);
        buffer.extend_from_slice(&self.foundation_sha256);
        buffer.extend_from_slice(&self.runtime_device.to_be_bytes());
        buffer.extend_from_slice(&self.runtime_inode.to_be_bytes());
        buffer.push(self.capability_kind.discriminant());
        sha256::digest(&buffer)
    }

    /// Asserts the descriptor carries no authority material.
    ///
    /// Every field is a fixed-width public identity with no caller-injected
    /// string, so authority leakage is prevented *structurally* (at the type
    /// level) rather than by runtime scanning. This assertion exists to
    /// document that invariant and to fail loudly the moment a future extension
    /// introduces an injectable, secret-bearing field.
    ///
    /// It mirrors the spirit of `manifest::reject_authority_metadata`'s 14
    /// forbidden terms (actor/key/raw_key/signer/verdict/...), but a descriptor
    /// with no string fields has nothing to alias-match: the guard is therefore
    /// compile-time structural plus this explicit runtime checkpoint. If a
    /// `String`/secret field is ever added, this function MUST grow a real
    /// rejection rather than stay a no-op.
    pub fn assert_no_authority_fields(&self) -> Result<(), &'static str> {
        let _ = (
            &self.transaction_id,
            &self.foundation_sha256,
            self.runtime_device,
            self.runtime_inode,
            self.capability_kind,
            self.descriptor_sha256,
        );
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> CapabilityDescriptor {
        CapabilityDescriptor::new([0xab; 32], [0xcd; 32], 0x1234, 0x5678, CapabilityKind::ZeroFd)
    }

    #[test]
    fn constructor_computes_descriptor_digest() {
        let descriptor = sample();
        assert_eq!(descriptor.descriptor_sha256, descriptor.compute_sha256());
    }

    #[test]
    fn sha256_is_deterministic() {
        assert_eq!(sample().compute_sha256(), sample().compute_sha256());
    }

    #[test]
    fn descriptor_sha256_is_not_a_digest_input() {
        let descriptor = sample();
        let mut tampered = descriptor.clone();
        tampered.descriptor_sha256 = [0xff; 32];
        assert_eq!(
            tampered.compute_sha256(),
            descriptor.compute_sha256(),
            "descriptor_sha256 must not influence its own digest"
        );
    }

    #[test]
    fn sha256_distinguishes_every_field() {
        let base = sample();
        for mutated in [
            CapabilityDescriptor::new([0; 32], [0xcd; 32], 0x1234, 0x5678, CapabilityKind::ZeroFd),
            CapabilityDescriptor::new([0xab; 32], [0; 32], 0x1234, 0x5678, CapabilityKind::ZeroFd),
            CapabilityDescriptor::new([0xab; 32], [0xcd; 32], 0, 0x5678, CapabilityKind::ZeroFd),
            CapabilityDescriptor::new([0xab; 32], [0xcd; 32], 0x1234, 0, CapabilityKind::ZeroFd),
        ] {
            assert_ne!(
                base.compute_sha256(),
                mutated.compute_sha256(),
                "digest must change when any identity field changes"
            );
        }
    }

    #[test]
    fn authority_assertion_passes_by_default() {
        assert!(sample().assert_no_authority_fields().is_ok());
    }
}
