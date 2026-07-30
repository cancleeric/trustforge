import importlib.util
import inspect
import struct
import tempfile
import os
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/test_nf2_zero_capability_linux.py"
SPEC = importlib.util.spec_from_file_location("nf2_linux_harness", SCRIPT)
assert SPEC and SPEC.loader
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


def test_rejected_case_requires_broker_block_exit_and_diagnostic():
    completed = HARNESS.subprocess.CompletedProcess(
        args=["broker"], returncode=77, stdout="", stderr="BLOCKED_EXTERNAL_LINUX: host\n"
    )
    try:
        HARNESS.expect("blocked external is not broker block", completed, False)
    except RuntimeError:
        pass
    else:
        raise AssertionError("rc=77 must not count as a broker BLOCK")

    crashed = HARNESS.subprocess.CompletedProcess(
        args=["broker"], returncode=-11, stdout="", stderr=""
    )
    try:
        HARNESS.expect("crash is not broker block", crashed, False)
    except RuntimeError:
        pass
    else:
        raise AssertionError("crash must not count as a broker BLOCK")

    blocked = HARNESS.subprocess.CompletedProcess(
        args=["broker"], returncode=70, stdout="", stderr="BLOCK: install changed\n"
    )
    HARNESS.expect("exact broker block", blocked, False)


def test_broker_boundaries_close_verified_tool_descriptors():
    assert HARNESS.SYSTEM_SHELL == Path("/usr/bin/bash")

    class Tool:
        def __init__(self, fd):
            self.fd = fd

    original = HARNESS.VERIFIED_HOST_TOOLS.copy()
    try:
        HARNESS.VERIFIED_HOST_TOOLS.clear()
        HARNESS.VERIFIED_HOST_TOOLS.update({"one": Tool(101), "two": Tool(109)})
        assert HARNESS.close_verified_fds_shell() == "exec 101<&-; exec 109<&-;"
        assert all(fd >= 100 for fd in HARNESS.verified_pass_fds())
    finally:
        HARNESS.VERIFIED_HOST_TOOLS.clear()
        HARNESS.VERIFIED_HOST_TOOLS.update(original)


def test_toctou_evidence_is_scoped_to_private_harness_root():
    source = inspect.getsource(HARNESS.verify_openat2_toctou_blocks)
    assert 'evidence_root / "openat2-toctou.stdout"' in source
    assert 'evidence_root / "openat2-toctou.stderr"' in source
    assert '"/tmp/trustforge-nf2-toctou' not in source


def _elf(program_type, payload=b"", *, elf_type=2, header_size=64):
    program_offset = 64
    payload_offset = program_offset + 56
    header = struct.pack(
        "<16sHHIQQQIHHHHHH",
        b"\x7fELF\x02\x01\x01" + b"\0" * 9,
        elf_type,
        62,
        1,
        0,
        program_offset,
        0,
        0,
        header_size,
        56,
        1,
        0,
        0,
        0,
    )
    program = struct.pack(
        "<IIQQQQQQ", program_type, 0, payload_offset, 0, 0, len(payload), len(payload), 8
    )
    return header + program + payload


def test_elf_parser_rejects_interp_needed_and_malformed_headers():
    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "broker"
        overlapping_program_headers = bytearray(_elf(1))
        struct.pack_into("<Q", overlapping_program_headers, 32, 56)
        for payload in (
            _elf(3, b"/lib/ld-musl-x86_64.so.1\0"),
            _elf(2, struct.pack("<qQ", 1, 1) + struct.pack("<qQ", 0, 0)),
            _elf(2, struct.pack("<qQ", 7, 1)),
            _elf(1, elf_type=1),
            _elf(1, header_size=63),
            _elf(4),
            _elf(1)[:70],
            bytes(overlapping_program_headers),
            b"not-elf",
        ):
            path.write_bytes(payload)
            try:
                HARNESS.verify_static_x86_64_elf(path)
            except RuntimeError:
                pass
            else:
                raise AssertionError("unsafe or malformed ELF must be rejected")


def test_elf_parser_accepts_static_x86_64_without_dynamic_dependencies(tmp_path):
    path = tmp_path / "broker"
    path.write_bytes(_elf(1))
    HARNESS.verify_static_x86_64_elf(path)


def test_setuid_tool_mode_uses_canonical_four_digit_octal(tmp_path):
    tool = tmp_path / "mount"
    tool.write_bytes(b"fixture")
    tool.chmod(0o4755)
    assert HARNESS.canonical_mode(os.stat(tool)) == "4755"


def test_self_cgroup_container_marker_is_not_hidden_by_clean_pid1():
    assert not HARNESS.cgroup_has_container_marker("0::/init.scope")
    assert HARNESS.cgroup_has_container_marker(
        "0::/docker/0123456789abcdef"
    )


def test_pdeathsig_live_check_rejects_pid_reuse_and_accepts_terminal_state(monkeypatch):
    source = inspect.getsource(HARNESS.verify_broker_death_kills_child)
    assert 'TRUSTFORGE_NF2_TEST_MODE=\\"$5\\" \\"$3\\" & ' in source
    assert 'exec \\"$3\\" ) &' not in source

    monkeypatch.setattr(HARNESS, "process_state_and_starttime", lambda _pid: ("S", "10"))
    assert HARNESS.process_is_same_live(42, "10")
    assert not HARNESS.process_is_same_live(42, "11")
    monkeypatch.setattr(HARNESS, "process_state_and_starttime", lambda _pid: ("Z", "10"))
    assert not HARNESS.process_is_same_live(42, "10")
    monkeypatch.setattr(HARNESS, "process_state_and_starttime", lambda _pid: ("x", "10"))
    assert not HARNESS.process_is_same_live(42, "10")
    monkeypatch.setattr(HARNESS, "process_state_and_starttime", lambda _pid: None)
    assert not HARNESS.process_is_same_live(42, "10")


def test_uid_map_whitespace_is_canonicalized_before_receipt_comparison():
    assert HARNESS.normalize_uid_map("         0          0 4294967295\n") == (
        "0 0 4294967295"
    )


def test_rustup_retained_fd_preserves_multicall_argv0():
    source = inspect.getsource(HARNESS.build_reviewed_brokers)
    assert source.count('["rustup", "which", "--toolchain"') == 2
    assert source.count("executable=str(rustup)") == 2


def test_build_uses_only_receipt_pinned_retained_rust_lld():
    source = inspect.getsource(HARNESS.build_reviewed_brokers)
    assert 'VERIFIED_HOST_TOOLS["rust-lld"] = verify_tool(' in source
    assert '"RUSTFLAGS": f"-C linker={rust_lld} -C linker-flavor=ld.lld"' in source
    assert source.count('"--offline"') == 2
    assert source.count('"--frozen"') == 2
    assert source.count('"x86_64-unknown-linux-musl"') >= 2
    assert source.count("verify_target_tree(target_root, target_entries)") == 2
    assert source.count("verify_static_x86_64_elf(") == 3
    assert "verify_static_x86_64_elf(second_exec_fixture)" in source
    assert " cc" not in source
    assert HARNESS.APPROVED_TOOL_SHA256["rust-lld"] == (
        "d9e01686cf6c278090dd461f9f033e49252829e1388cfa88c9643579701d8c39"
    )


if __name__ == "__main__":
    test_rejected_case_requires_broker_block_exit_and_diagnostic()
    test_broker_boundaries_close_verified_tool_descriptors()
    test_toctou_evidence_is_scoped_to_private_harness_root()
    test_elf_parser_rejects_interp_needed_and_malformed_headers()
    test_elf_parser_accepts_static_x86_64_without_dynamic_dependencies(
        Path(tempfile.mkdtemp())
    )
