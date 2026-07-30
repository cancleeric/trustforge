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
    assert 'harness.host_tool("cargo")' in value
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
    assert 'f"--remap-path-prefix={source_tree}={CANONICAL_SOURCE_ROOT}"' in value
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
        "e567a05c349321f68d01aaa114d665e2e6bdf6381af211bda90dd2107eb971dc",
        "84eeca2087f46a12d71efb472ad31d27c1322ac769b2a9793d8e6c96a2bdc8f1",
        "2cc766af9791160ded10a1bf487cc7f19e3ba8838107c6d55f90876b9ad14617",
    ):
        assert receipt in value
    assert 'source_a = copy_reviewed_build_inputs(repo, scratch / "source-a")' in value
    assert (
        "source_b = copy_reviewed_build_inputs("
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
    assert (
        'parser.add_argument("--probe-remapped-builds", action="store_true")' in value
    )


def test_nested_units_use_only_secure_host_visible_handoff_artifacts():
    value = source()
    assert 'HANDOFF_ROOT = Path("/var/lib/trustforge-nf3-handoff")' in value
    assert "O_EXCL | os.O_NOFOLLOW" in value
    assert "os.fsync(handoff_fd)" in value
    assert "handoff cleanup generation mismatch" in value
    assert "StateDirectory contains stale or unknown state" in value
    assert "ACTIVE_TO_TERMINAL_CLEAN" in value
    nested = value[value.index("release_profile_line = run(") :]
    nested = nested[: nested.index("evidence = {")]
    assert "BindReadOnlyPaths={release_receipt}" not in nested
    assert "BindReadOnlyPaths={release_probe_a}" not in nested
    assert "BindReadOnlyPaths={evidence_receipt}" not in nested
    assert "BindReadOnlyPaths={helper_a}" not in nested
    assert "BindReadOnlyPaths={evidence_rlib_a}" not in nested
    assert "BindReadOnlyPaths={repo}" not in nested
    assert nested.count("BindReadOnlyPaths={handoff_path /") == 6


def test_handoff_mount_identity_accepts_executable_local_mount():
    mountinfo = (
        "25 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw\n"
        "26 25 8:2 / /var rw,nodev - xfs /dev/sda2 rw\n"
    )
    identity = orchestrator.handoff_mount_identity(mountinfo)
    assert identity["mount_id"] == "26"
    assert identity["filesystem_type"] == "xfs"
    assert identity["mountpoint"] == "/var"


@pytest.mark.parametrize(
    "mountinfo",
    [
        "25 1 8:1 / / rw,noexec - ext4 /dev/sda1 rw",
        "25 1 0:42 / / rw - nfs server:/export rw",
    ],
)
def test_handoff_mount_identity_rejects_noexec_and_nonlocal(mountinfo):
    with pytest.raises(SystemExit) as failure:
        orchestrator.handoff_mount_identity(mountinfo)
    assert failure.value.code == orchestrator.BLOCKED


def test_probe_and_pin_gates_precede_persistent_handoff_creation():
    value = source()
    assert value.index("if arguments.probe_remapped_builds:") < value.index(
        "with handoff_session(head)"
    )
    assert value.index("release_probe_hash != EXPECTED_RELEASE_PROBE_SHA256") < (
        value.index("with handoff_session(head)")
    )


def test_handoff_session_finalizes_on_exception():
    opened = (-1, -2, "commit-random", {"mount_id": "1"})
    with (
        mock.patch.object(
            orchestrator, "_validate_handoff_directory", return_value=opened
        ),
        mock.patch.object(orchestrator, "finalize_handoff_generation") as finalize,
        pytest.raises(RuntimeError, match="controlled failure"),
    ):
        with orchestrator.handoff_session("commit"):
            raise RuntimeError("controlled failure")
    finalize.assert_called_once_with(-1, -2, "commit-random", {})


def test_handoff_rejects_non_plain_destination(tmp_path):
    with pytest.raises(ValueError, match="one plain filename"):
        orchestrator.stage_handoff_file(
            -1,
            tmp_path / "unused",
            "../escape",
            expected_sha256="0" * 64,
        )


def test_handoff_source_generation_binds_all_security_metadata():
    metadata = SimpleNamespace(
        st_mode=0o100755,
        st_uid=0,
        st_gid=0,
        st_nlink=2,
        st_size=4096,
        st_mtime_ns=11,
        st_ctime_ns=12,
        st_dev=13,
        st_ino=14,
    )
    assert orchestrator._file_generation(metadata) == (
        0o100755,
        0,
        0,
        2,
        4096,
        11,
        12,
        13,
        14,
    )


def test_handoff_source_allows_cargo_hardlinks_but_destination_requires_one_link():
    value = source()
    staging = value[value.index("def stage_handoff_file(") :]
    staging = staging[: staging.index("def cleanup_handoff_file(")]
    assert "before.st_nlink < 1" in staging
    assert "before.st_nlink != 1" not in staging
    assert "staged.st_nlink != 1" in staging


def test_nested_execstart_uses_existing_fixed_wrappers_without_shell_strings():
    value = source()
    assert 'SYSTEMD_EXEC_WRAPPER = "/usr/bin/env"' in value
    assert 'SYSTEMD_SCRIPT_WRAPPER = "/bin/bash"' in value
    release = value[value.index("release_profile_line = run(") :]
    release = release[: release.index("expected_release_fields =")]
    assert '"/run/trustforge-nf3-release-probe"' in release
    assert release.index("SYSTEMD_EXEC_WRAPPER") < release.index(
        '"/run/trustforge-nf3-release-probe"'
    )
    integrated = value[value.index('command = ["systemd-run"') :]
    integrated = integrated[: integrated.index("run(command, cwd=repo)")]
    assert '"/run/trustforge-nf3-run-integrated-linux"' in integrated
    assert integrated.index("SYSTEMD_SCRIPT_WRAPPER") < integrated.index(
        '"/run/trustforge-nf3-run-integrated-linux"'
    )
    assert '"/bin/bash -c"' not in value
    assert '"sh", "-c"' not in value


def test_nonexecutable_nf2_python_harness_uses_fixed_interpreter_argv():
    value = source()
    assert 'SYSTEM_PYTHON = "/usr/bin/python3"' in value
    invocation = value[
        value.index('str(repo / "scripts/test_nf2_zero_capability_linux.py")') :
    ]
    invocation = invocation[: invocation.index("cwd=repo")]
    assert value.rfind("SYSTEM_PYTHON", 0, value.index(invocation)) >= 0
    assert '"python3 -c"' not in value
    assert "SYSTEM_PYTHON,\n                    str(repo /" in value


def test_nested_integrated_writes_are_scoped_to_exact_cases_root():
    value = source()
    integrated = value[value.index('unit = f"trustforge-nf3-b-') :]
    integrated = integrated[: integrated.index("evidence = {")]
    assert 'os.mkdir("cases", 0o700, dir_fd=handoff_fd)' in integrated
    assert 'cases_root = handoff_path / "cases"' in integrated
    assert 'dir="/var/tmp"' not in integrated
    assert '"ReadWritePaths=/root"' not in integrated
    assert 'f"ReadWritePaths={cases_root}"' in integrated
    command = integrated[integrated.index("command.extend(") :]
    assert command.count("str(cases_root)") == 2
    assert '"/root",\n                    str(cases_root)' not in command
    assert 'cases_root.glob("trustforge-nf3-integrated-*")' in integrated
    assert 'cases_root.glob("trustforge-nf3-witness-*")' in integrated
    assert "cleanup_cases_tree(" in integrated
    cleanup = value[value.index("def cleanup_cases_tree(") :]
    cleanup = cleanup[: cleanup.index("def finalize_handoff_generation(")]
    assert "os.O_DIRECTORY | os.O_NOFOLLOW" in cleanup
    assert "metadata.st_nlink != 1" in cleanup
    assert "contains unknown or missing entries" in cleanup
    assert 'os.rmdir("cases", dir_fd=generation_fd)' in cleanup


def test_nf1_nested_bind_uses_atomic_closed_set_staging_not_raw_install():
    value = source()
    staging = value[value.index("def stage_nf1_install(") :]
    staging = staging[: staging.index("def cleanup_nf1_install(")]
    assert "actual_names != set(expected)" in staging
    assert "member.uid != 0 or member.gid != 0" in staging
    assert "member.mode != 0o555" in staging
    assert "metadata.st_nlink != 1" in staging
    assert "accepted install changed after staging" in staging
    assert 'os.rename(\n            temporary, "nf1-install",' in staging
    assert "os.O_NOFOLLOW" in staging
    integrated = value[value.index('unit = f"trustforge-nf3-b-') :]
    integrated = integrated[: integrated.index("evidence = {")]
    assert "BindReadOnlyPaths={install}" not in integrated
    assert "BindReadOnlyPaths={staged_nf1_install}" in integrated
    assert "cleanup_nf1_install(handoff_fd, staged_nf1_expected)" in integrated


def test_cargo_tests_exclude_doctests_and_all_targets_are_explicit():
    value = source()
    normalized = "".join(value.split())
    test_invocation = normalized[
        normalized.index('harness.host_tool("cargo"),"test"') :
    ]
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
