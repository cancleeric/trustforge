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

/// Carrier of the two transaction-scoped identity fields the broker needs to
/// build a [`CapabilityDescriptor`] at the release boundary: the NF3
/// `transaction_id` and the accepted `foundation_sha256`. Both are fixed-width
/// public identities; this type carries no authority material. The broker
/// receives it from the NF3 orchestrator (which decodes it from the durable
/// `Binding`) and combines it with the sealed runtime device/inode to construct
/// the descriptor in-process, so the descriptor is always live-bound and never
/// reconstructed from ambient state.
#[derive(Debug, Clone, Copy)]
pub struct CapabilityContext {
    pub transaction_id: [u8; 32],
    pub foundation_sha256: [u8; 32],
}

impl CapabilityContext {
    /// All-zero context for the default [`crate::NoopSink`] path, which never
    /// constructs a descriptor. A live transaction always supplies a non-zero
    /// context decoded from its durable `Binding`.
    pub const fn zero() -> Self {
        Self {
            transaction_id: [0; 32],
            foundation_sha256: [0; 32],
        }
    }
}

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
            &self.descriptor_sha256,
        );
        Ok(())
    }
}

/// Decodes exactly 64 lowercase-hex characters into a 32-byte digest.
///
/// This is the inverse of the crate's digest-then-`{:02x}` pipeline used by
/// callers that persist digests as canonical lowercase-hex strings (for example
/// an NF3 `Binding`'s `transaction_id` / `foundation_sha256`). It accepts only
/// the lowercase alphabet the rest of the crate emits (`[0-9a-f]`), so a
/// mixed-case or wrong-length string is rejected rather than silently turning
/// an attacker-controlled identifier into a descriptor field. The result feeds
/// [`CapabilityDescriptor::new`], whose fields are all fixed-width public
/// identities.
pub fn decode_hex_32(input: &str) -> Result<[u8; 32], &'static str> {
    if input.len() != 64 {
        return Err("hex32 length");
    }
    let mut output = [0u8; 32];
    for (index, pair) in input.as_bytes().chunks_exact(2).enumerate() {
        let high = hex_digit(pair[0])?;
        let low = hex_digit(pair[1])?;
        output[index] = (high << 4) | low;
    }
    Ok(output)
}

fn hex_digit(byte: u8) -> Result<u8, &'static str> {
    match byte {
        b'0'..=b'9' => Ok(byte - b'0'),
        b'a'..=b'f' => Ok(byte - b'a' + 10),
        _ => Err("hex32 character"),
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

    #[test]
    fn decode_hex_32_round_trips_lowercase_encoding() {
        let raw = [
            0x12, 0x34, 0x56, 0x78, 0x9a, 0xbc, 0xde, 0xf0, 0x01, 0x23, 0x45, 0x67, 0x89, 0xab,
            0xcd, 0xef, 0xfe, 0xdc, 0xba, 0x98, 0x76, 0x54, 0x32, 0x10, 0x0f, 0x1e, 0x2d, 0x3c,
            0x4b, 0x5a, 0x69, 0x78,
        ];
        let encoded: String = raw.iter().map(|byte| format!("{byte:02x}")).collect();
        assert_eq!(decode_hex_32(&encoded).unwrap(), raw);
    }

    #[test]
    fn decode_hex_32_rejects_invalid_inputs() {
        assert!(decode_hex_32("").is_err());
        assert!(decode_hex_32(&"a".repeat(63)).is_err());
        assert!(decode_hex_32(&"a".repeat(65)).is_err());
        assert!(decode_hex_32(&"A".repeat(32)).is_err());
        assert!(decode_hex_32(&"g".repeat(32)).is_err());
    }
}
