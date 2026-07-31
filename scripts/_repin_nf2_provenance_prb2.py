#!/usr/bin/env python3
"""Recompute NF2 provenance framing for PR-B2 (workspace + native-sys dedup).

Replicates the exact byte framing of foundation.rs:
  - linked_source  = linked-source.v1 framing over 12 SOURCES
  - source_tree    = source-tree-receipt.v1 framing over (git_oid, linked_source)
"""
import hashlib
import struct
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parent.parent / "native"
NF2 = NATIVE / "nf2-zero-capability-broker"

# (name, path) — order MUST match foundation.rs SOURCES exactly.
SOURCES = [
    ("Cargo.lock", NATIVE / "Cargo.lock"),
    ("Cargo.toml", NF2 / "Cargo.toml"),
    ("src/canonical_json.rs", NF2 / "src/canonical_json.rs"),
    ("src/capability.rs", NF2 / "src/capability.rs"),
    ("src/lib.rs", NF2 / "src/lib.rs"),
    ("src/linux.rs", NF2 / "src/linux.rs"),
    ("src/linux/live.rs", NF2 / "src/linux/live.rs"),
    ("src/linux/process.rs", NF2 / "src/linux/process.rs"),
    ("src/linux/sealed.rs", NF2 / "src/linux/sealed.rs"),
    ("src/main.rs", NF2 / "src/main.rs"),
    ("src/manifest.rs", NF2 / "src/manifest.rs"),
    ("src/native_sys.rs", NATIVE / "trustforge-native-sys/src/lib.rs"),
]

GIT_SUBTREE_OID = "c43e08d8ce5cded900282ca4ddda681fe148594a"

# Fixed foundation anchors (not changed by PR-B2 dedup).
DOMAIN = b"trustforge.native-foundation-binding.v1\0"
ACCEPTED_MANIFEST_SHA256 = "5e2db7cf733482a0c43bbfe2a27e96c3b255c1a69dde32054db3181a92fd241c"
ACCEPTED_RUNTIME_SHA256 = "cf8c2165cb93b7a8712d848b653d51a977f4ce12f1a9dad7bd41e189ee694f86"
ACCEPTED_ARCHIVE_SHA256 = "808487c590a183a8df2e69cfc5257969e18ae88b15c4378da95d97add6c03c1b"
NF2_MERGE_SHA256 = "d049ced955afca1ea3e426bdc19be0b449a1ab5ba130ac9dce386123dba38bab"
NF2_FIXED_TOOLCHAIN_RECEIPT_SHA256 = "3ddca04f9011db7eba5f0a85103ce62710f6be8d20aca02850aec5774301ee26"
# Interim rlib + profile-receipt anchors (need .83 musl build; unchanged in PR-B2).
NF2_LINKED_EVIDENCE_RLIB_SHA256 = "bada9d9e97d961c7660b55678c518e56d1b3867b36a489d18648e0b6f26aa22b"
NF2_LINKED_RELEASE_RLIB_SHA256 = "ef9e4d796488d40fce33188505abfcc8c610cb74ccd2592a410bfc1d3812ec38"
NF2_EVIDENCE_PROFILE_RECEIPT_SHA256 = "7f53b287a6944a5978b02dfcd35e50b5955be28107ac457369a70d22115f79a5"
NF2_RELEASE_PROFILE_RECEIPT_SHA256 = "5cc871f48193094c28b5df2691c63b2f3c6649686b3573243de5daed90e6e070"


def linked_source_sha256() -> str:
    canonical = b"trustforge.nf2.linked-source.v1\0"
    for name, path in SOURCES:
        data = path.read_bytes()
        canonical += struct.pack(">I", len(name))
        canonical += name.encode()
        canonical += struct.pack(">Q", len(data))
        canonical += data
    return hashlib.sha256(canonical).hexdigest()


def source_tree_receipt(linked: str) -> str:
    # Mirrors foundation.rs `source_tree_receipt_is_platform_independent_framing`:
    # frame() is applied to BOTH the field name and the value.
    canonical = bytearray(b"trustforge.nf2.source-tree-receipt.v1\0")
    for name, value in [
        ("git_subtree_oid_sha1", GIT_SUBTREE_OID),
        ("linked_source_sha256", linked),
    ]:
        _frame(canonical, name)
        _frame(canonical, value)
    return hashlib.sha256(canonical).hexdigest()


def _frame(out: bytearray, value: str) -> None:
    vb = value.encode()
    out += struct.pack(">I", len(vb))
    out += vb


def linked_nf2_build_sha256(linked: str, receipt: str, profile_receipt: str, rlib: str) -> str:
    canonical = bytearray(b"trustforge.nf2.linked-build.v1\0")
    for name, value in [
        ("linked_source_sha256", linked),
        ("fixed_toolchain_receipt_sha256", NF2_FIXED_TOOLCHAIN_RECEIPT_SHA256),
        ("source_tree_receipt_sha256", receipt),
        ("profile_receipt_sha256", profile_receipt),
        ("linked_profile_rlib_sha256", rlib),
    ]:
        _frame(canonical, name)
        _frame(canonical, value)
    return hashlib.sha256(canonical).hexdigest()


def foundation_sha256(linked: str, receipt: str, profile_receipt: str, rlib: str) -> str:
    nf2_build = linked_nf2_build_sha256(linked, receipt, profile_receipt, rlib)
    canonical = bytearray(DOMAIN)
    for name, value in [
        ("nf1_manifest_sha256", ACCEPTED_MANIFEST_SHA256),
        ("nf1_runtime_sha256", ACCEPTED_RUNTIME_SHA256),
        ("nf1_archive_sha256", ACCEPTED_ARCHIVE_SHA256),
        ("nf2_merge_sha256", NF2_MERGE_SHA256),
        ("nf2_build_sha256", nf2_build),
    ]:
        _frame(canonical, name)
        _frame(canonical, value)
    return hashlib.sha256(canonical).hexdigest()


def main() -> int:
    missing = [(n, str(p)) for n, p in SOURCES if not p.exists()]
    if missing:
        print("MISSING:", missing, file=sys.stderr)
        return 1
    linked = linked_source_sha256()
    receipt = source_tree_receipt(linked)
    evidence = foundation_sha256(
        linked, receipt, NF2_EVIDENCE_PROFILE_RECEIPT_SHA256, NF2_LINKED_EVIDENCE_RLIB_SHA256
    )
    release = foundation_sha256(
        linked, receipt, NF2_RELEASE_PROFILE_RECEIPT_SHA256, NF2_LINKED_RELEASE_RLIB_SHA256
    )
    print(f"linked_source_sha256   = {linked}")
    print(f"source_tree_receipt    = {receipt}")
    print(f"foundation evidence    = {evidence}")
    print(f"foundation release     = {release}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
