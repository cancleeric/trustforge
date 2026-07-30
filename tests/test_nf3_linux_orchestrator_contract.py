from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from scripts import test_nf3_integrated_linux as orchestrator


ORCHESTRATOR = Path("scripts/test_nf3_integrated_linux.py")


def source() -> str:
    return ORCHESTRATOR.read_text()


def test_builds_use_nf2_verified_absolute_tool_handles():
    value = source()
    assert "harness.host_tool(\"cargo\")" in value
    assert '"RUSTC": rustc' in value
    assert 'f"-C linker={rust_lld} "' in value
    assert '"-C linker-flavor=ld.lld "' in value
    assert '["cargo", "build"' not in value
    assert '["cargo", "test"' not in value
    assert '["cargo", "check"' not in value


def test_toolchain_environment_is_isolated_and_offline():
    value = source()
    assert '"HOME": str(home)' in value
    assert '"CARGO_HOME": str(cargo_home)' in value
    assert '"RUSTUP_HOME": "/root/.rustup"' in value
    assert '"CARGO_NET_OFFLINE": "true"' in value
    assert '"RUSTDOC": FORBIDDEN_RUSTDOC' in value
    assert 'FORBIDDEN_RUSTDOC = "/nonexistent/trustforge-rustdoc-forbidden"' in value
    assert (
        'f"--remap-path-prefix={source_tree}={CANONICAL_SOURCE_ROOT}"' in value
    )
    assert "os.environ.copy()" not in value


def test_nf2_receipts_and_target_tree_are_reused():
    value = source()
    assert "harness.HOST_RECEIPT_SHA256" in value
    assert "harness.validate_host_receipt" in value
    assert "harness.load_target_receipt(repo)" in value
    assert value.count("verify_target_tree(harness, target_tree, target_entries)") == 4


def test_evidence_uses_verified_rust_tool_digests():
    value = source()
    assert 'if name in ("rustup", "cargo", "rustc", "rust-lld")' in value
    assert "name: tool.expected" in value


def test_native_receipts_bind_the_reviewed_canonical_view_probe():
    value = source()
    for receipt in (
        "1f3c09df97298013ae1d67b8618de6b66492267d0fd59b3053d9f71fa48872a4",
        "1db21394225521a2fb22ee81e73a35697a14d2e8275bc6008097684a026ecb93",
        "84eeca2087f46a12d71efb472ad31d27c1322ac769b2a9793d8e6c96a2bdc8f1",
        "db9f6e1f95d1aea350fe43d4a0c2392fd9f67c284a8c6207bc5d56b341798830",
    ):
        assert receipt in value
    assert 'source_a = copy_reviewed_build_inputs(repo, scratch / "source-a")' in value
    assert (
        'source_b = copy_reviewed_build_inputs('
        'repo, scratch / "different-source-b")' in value
    )
    assert "canonical_source = install_canonical_build_view(source_a)" in value
    assert "canonical_source = install_canonical_build_view(source_b)" in value
    assert 'CANONICAL_BUILD_PARENT = Path("/run/trustforge-nf3-build-input")' in value
    assert "stat.S_IMODE(parent.st_mode) != 0o700" in value
    assert "release_probe_hash != digest(release_probe_b)" in value
    assert "release_probe_hash != EXPECTED_RELEASE_PROBE_SHA256" in value
    assert "len(set(evidence_rlib_hashes)) != 1" in value
    assert "len(set(helper_hashes)) != 1" in value
    assert '"cross_host_substitution": "forbidden"' in value
    assert 'parser.add_argument("--probe-remapped-builds", action="store_true")' in value


def test_cargo_tests_exclude_doctests_and_all_targets_are_explicit():
    value = source()
    test_invocation = value[value.index('harness.host_tool("cargo"),\n                "test"') :]
    assert '"--lib"' in test_invocation
    assert '"--tests"' in test_invocation
    assert '"--bins"' in test_invocation


def test_missing_host_receipt_is_blocked_external_linux(tmp_path):
    harness = SimpleNamespace(
        HOST_RECEIPT=Path("missing-receipt.json"),
        HOST_RECEIPT_SHA256="0" * 64,
    )
    with (
        mock.patch.object(orchestrator, "load_nf2_harness", return_value=harness),
        pytest.raises(SystemExit) as failure,
    ):
        orchestrator.verified_rust_toolchain(tmp_path, tmp_path / "scratch")
    assert failure.value.code == orchestrator.BLOCKED


def test_host_receipt_digest_mismatch_is_blocked_external_linux(tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}")
    harness = SimpleNamespace(
        HOST_RECEIPT=receipt.relative_to(tmp_path),
        HOST_RECEIPT_SHA256="0" * 64,
    )
    with (
        mock.patch.object(orchestrator, "load_nf2_harness", return_value=harness),
        pytest.raises(SystemExit) as failure,
    ):
        orchestrator.verified_rust_toolchain(tmp_path, tmp_path / "scratch")
    assert failure.value.code == orchestrator.BLOCKED
