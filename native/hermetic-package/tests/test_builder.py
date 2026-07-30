from __future__ import annotations

import importlib.util
import json
import copy
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
SCRIPT = ROOT / "scripts/build_native_hermetic_package.py"
SPEC = importlib.util.spec_from_file_location("native_hermetic", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def _source_repo(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    shutil.copytree(
        ROOT / "native/hermetic-package",
        source / "native/hermetic-package",
    )
    (source / "scripts").mkdir()
    shutil.copy2(SCRIPT, source / "scripts/build_native_hermetic_package.py")
    _git("init", "-q", cwd=source)
    _git("config", "user.email", "nf1@example.invalid", cwd=source)
    _git("config", "user.name", "NF1 Test", cwd=source)
    _git("add", ".", cwd=source)
    _git("commit", "-qm", "fixture", cwd=source)
    return source


def test_authority_metadata_is_rejected() -> None:
    for key in (
        "actor_id",
        "key-id",
        "raw_public_key",
        "signer",
        "verdict",
        "PASS",
        "eligibility",
        "publication_authority",
        "actorId",
        "release_eligibility",
    ):
        with pytest.raises(MODULE.BuildBlocked, match="authority metadata"):
            MODULE._reject_authority_metadata({"nested": [{key: "forbidden"}]})


def test_dirty_source_is_rejected(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    (source / "native/hermetic-package/src/main.rs").write_text("fn main() {}\n")
    with pytest.raises(MODULE.BuildBlocked, match="dirty"):
        MODULE.build(source, tmp_path / "output")


def test_symlink_input_is_rejected(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    link = source / "native/hermetic-package/src/alias.rs"
    link.symlink_to("main.rs")
    _git("add", ".", cwd=source)
    _git("commit", "-qm", "add symlink", cwd=source)
    with pytest.raises(MODULE.BuildBlocked, match="symlink"):
        MODULE.build(source, tmp_path / "output")


def test_hardlinked_input_is_rejected(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    original = source / "native/hermetic-package/src/main.rs"
    original.with_name("alias.rs").hardlink_to(original)
    _git("add", ".", cwd=source)
    _git("commit", "-qm", "add hardlink", cwd=source)
    with pytest.raises(MODULE.BuildBlocked, match="multiply-linked"):
        MODULE.build(source, tmp_path / "output")


def test_hostile_output_ancestor_cargo_config_is_rejected(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    hostile = tmp_path / "hostile"
    (hostile / ".cargo").mkdir(parents=True)
    (hostile / ".cargo/config.toml").write_text("[build]\nrustflags=['bad']\n")
    with pytest.raises(MODULE.BuildBlocked, match="ancestor Cargo config"):
        MODULE.build(source, hostile / "output")


def _elf(
    *, interp: bool = False, needed: bool = False, dynamic_program: bool = False
) -> bytes:
    phnum = 1 if interp or dynamic_program else 0
    shnum = 1 if needed else 0
    phoff = 64
    shoff = phoff + phnum * 56
    dynamic_offset = shoff + shnum * 64
    dynamic_size = 16 if needed or dynamic_program else 0
    data = bytearray(dynamic_offset + dynamic_size)
    data[:16] = b"\x7fELF" + bytes([2, 1, 1]) + bytes(9)
    struct.pack_into(
        "<HHIQQQIHHHHHH",
        data,
        16,
        2,
        62,
        1,
        0,
        phoff,
        shoff,
        0,
        64,
        56,
        phnum,
        64,
        shnum,
        0,
    )
    if interp:
        struct.pack_into("<I", data, phoff, 3)
    if dynamic_program:
        struct.pack_into("<I", data, phoff, 2)
        struct.pack_into("<Q", data, phoff + 8, dynamic_offset)
        struct.pack_into("<QQ", data, phoff + 32, 16, 16)
        struct.pack_into("<qQ", data, dynamic_offset, 1, 0)
    if needed:
        struct.pack_into("<I", data, shoff + 4, 6)
        struct.pack_into("<QQ", data, shoff + 24, dynamic_offset, 16)
        struct.pack_into("<Q", data, shoff + 56, 16)
        struct.pack_into("<qQ", data, dynamic_offset, 1, 0)
    return bytes(data)


def test_bounds_checked_elf_parser_rejects_false_and_dynamic_elf(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime"
    for payload, match in (
        (b"\x7fELFfake libc.so", "bounds-valid"),
        (_elf(interp=True), "PT_INTERP"),
        (_elf(needed=True), "DT_NEEDED"),
        (_elf(dynamic_program=True), "DT_NEEDED in PT_DYNAMIC"),
    ):
        path.write_bytes(payload)
        with pytest.raises(MODULE.BuildBlocked, match=match):
            MODULE._elf_static_assertions(path)
    path.write_bytes(_elf())
    assert MODULE._elf_static_assertions(path)["pt_interp"] is False


def test_authority_value_is_rejected() -> None:
    for value in (
        "actor",
        "key",
        "private_key",
        "key_id",
        "raw_key",
        "raw_public_key",
        "use signer capability",
        "release PASS",
        "eligibility",
        "release_eligibility",
        "privateKey",
        "keyId",
        "rawPublicKey",
        "releaseEligibility",
        "trust-anchor",
    ):
        with pytest.raises(MODULE.BuildBlocked, match="value"):
            MODULE._reject_authority_metadata({"schema": value})


@pytest.mark.parametrize("tool_name", ["cargo", "rustc", "rust-lld"])
def test_toolchain_or_linker_mutation_changes_provenance(
    tmp_path: Path, tool_name: str
) -> None:
    if tool_name == "rust-lld":
        rustc = MODULE._resolve_tool("rustc")
        sysroot = Path(
            subprocess.check_output(
                [str(rustc), "--print", "sysroot"], text=True
            ).strip()
        )
        original = MODULE._resolve_tool(tool_name, sysroot=sysroot)
    else:
        original = MODULE._resolve_tool(tool_name)
    mutated = tmp_path / tool_name
    shutil.copy2(original, mutated)
    mutated.write_bytes(mutated.read_bytes() + b"NF1 mutation")
    before = MODULE._tool_record(original, "pinned")
    after = MODULE._tool_record(mutated, "pinned")
    assert before["sha256"] != after["sha256"]
    assert before["size"] != after["size"]


@pytest.mark.parametrize("tool_name", ["cargo", "rust-lld"])
def test_mutated_tool_is_injected_into_builder_and_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    source = _source_repo(tmp_path)
    original_resolve = MODULE._resolve_tool
    rustc = original_resolve("rustc")
    sysroot = Path(
        subprocess.check_output([str(rustc), "--print", "sysroot"], text=True).strip()
    )
    original = original_resolve(tool_name, sysroot=sysroot)
    mutated = tmp_path / tool_name
    shutil.copy2(original, mutated)
    mutated.write_bytes(mutated.read_bytes() + b"NF1 injected mutation")

    def resolve(name: str, *, sysroot=None):
        if name == tool_name:
            return mutated
        return original_resolve(name, sysroot=sysroot)

    monkeypatch.setattr(MODULE, "_resolve_tool", resolve)
    with pytest.raises(MODULE.BuildBlocked, match="repository lock"):
        MODULE.build(source, tmp_path / "output")


def test_ambient_cargo_configuration_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path)
    hostile_home = tmp_path / "hostile-home"
    hostile_cargo = hostile_home / ".cargo"
    hostile_cargo.mkdir(parents=True)
    (hostile_cargo / "config.toml").write_text(
        '[build]\nrustflags = ["--definitely-not-a-rustc-flag"]\n'
    )
    monkeypatch.setenv("CARGO_HOME", str(hostile_cargo))
    result = MODULE.build(source, tmp_path / "output")
    assert len(result["runtime_sha256"]) == 64


def test_two_independent_clean_clones_are_byte_identical(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    clones = []
    for name in ("clone-a", "clone-b"):
        clone = tmp_path / name
        subprocess.run(["git", "clone", "-q", str(source), str(clone)], check=True)
        clones.append(clone)
    first = MODULE.build(clones[0], tmp_path / "out-a")
    second = MODULE.build(clones[1], tmp_path / "out-b")
    assert first == second
    assert (tmp_path / "out-a/native-hermetic-provenance.json").read_bytes() == (
        tmp_path / "out-b/native-hermetic-provenance.json"
    ).read_bytes()
    assert (tmp_path / "out-a/native-hermetic-package.tar").read_bytes() == (
        tmp_path / "out-b/native-hermetic-package.tar"
    ).read_bytes()

    manifest = json.loads(
        (tmp_path / "out-a/native-hermetic-provenance.json").read_text()
    )
    source_paths = {entry["path"] for entry in manifest["sources"]}
    assert {
        "Cargo.toml",
        "Cargo.lock",
        "rust-toolchain.toml",
        "toolchain-lock.json",
        ".cargo/config.toml",
        "generated/source_epoch.rs",
        "src/main.rs",
        "package/fixed-config.json",
        "package/public-metadata-format.json",
    } <= source_paths
    assert manifest["toolchain"]["target_libdir_entries"]
    assert manifest["cargo_resolution"]["third_party_dependencies"] == []
    assert manifest["cargo_resolution"]["vendor_entries"] == []
    assert manifest["runtime_closure"]["pt_interp"] is False
    assert manifest["runtime_closure"]["dt_needed"] == []
    assert manifest["build"]["argv"][0] == "cargo"
    assert manifest["environment"]["HOME"] == "isolated:non-user-empty-home"
    malformed = copy.deepcopy(manifest)
    malformed["toolchain"]["cargo"].pop("sha256")
    with pytest.raises(MODULE.BuildBlocked, match="cargo schema"):
        MODULE._validate_manifest_shape(malformed)
    malformed = copy.deepcopy(manifest)
    malformed["package_entries"].append(malformed["package_entries"][0])
    with pytest.raises(MODULE.BuildBlocked, match="cardinality"):
        MODULE._validate_manifest_shape(malformed)


def test_concurrent_source_change_blocks_at_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path)
    original_run = MODULE._run
    changed = False

    def run_and_mutate(argv, *, cwd, env=None):
        nonlocal changed
        result = original_run(argv, cwd=cwd, env=env)
        if len(argv) > 1 and argv[1] == "build" and not changed:
            changed = True
            path = source / "native/hermetic-package/src/main.rs"
            path.write_bytes(path.read_bytes() + b"\n// concurrent mutation\n")
        return result

    monkeypatch.setattr(MODULE, "_run", run_and_mutate)
    with pytest.raises(MODULE.BuildBlocked, match="dirty|VCS identity|source inputs"):
        MODULE.build(source, tmp_path / "output")


def test_concurrent_tool_change_blocks_end_reverification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path)
    original_toolchain = MODULE._toolchain
    calls = 0

    def changed_at_end(cargo, rustc, source_root):
        nonlocal calls
        calls += 1
        observed = original_toolchain(cargo, rustc, source_root)
        if calls > 1:
            observed = copy.deepcopy(observed)
            observed["linker"]["sha256"] = "0" * 64
        return observed

    monkeypatch.setattr(MODULE, "_toolchain", changed_at_end)
    with pytest.raises(MODULE.BuildBlocked, match="repository lock|changed"):
        MODULE.build(source, tmp_path / "output")


def test_aba_mutation_of_original_inputs_cannot_change_snapshot_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path)
    baseline = MODULE.build(source, tmp_path / "baseline")
    original_run = MODULE._run
    mutated = False
    relative_paths = (
        "package/fixed-config.json",
        "Cargo.lock",
        "generated/source_epoch.rs",
    )

    def aba_after_compile(argv, *, cwd, env=None):
        nonlocal mutated
        result = original_run(argv, cwd=cwd, env=env)
        if len(argv) > 1 and argv[1] == "build" and not mutated:
            mutated = True
            for relative in relative_paths:
                path = source / "native/hermetic-package" / relative
                original = path.read_bytes()
                path.write_bytes(b"attacker ABA bytes")
                path.write_bytes(original)
        return result

    monkeypatch.setattr(MODULE, "_run", aba_after_compile)
    assert MODULE.build(source, tmp_path / "aba") == baseline


def test_tool_path_swap_restore_cannot_change_sealed_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path)
    original_resolve = MODULE._resolve_tool
    discovered_cargo = tmp_path / "discovered-cargo"
    shutil.copy2(original_resolve("cargo"), discovered_cargo)

    def resolve(name: str, *, sysroot=None):
        if name == "cargo":
            return discovered_cargo
        return original_resolve(name, sysroot=sysroot)

    original_run = MODULE._run
    swapped = False

    def swap_restore(argv, *, cwd, env=None):
        nonlocal swapped
        if len(argv) > 1 and argv[1] == "build" and not swapped:
            swapped = True
            original = discovered_cargo.read_bytes()
            discovered_cargo.write_bytes(b"attacker transient tool")
            try:
                return original_run(argv, cwd=cwd, env=env)
            finally:
                discovered_cargo.write_bytes(original)
                discovered_cargo.chmod(0o755)
        return original_run(argv, cwd=cwd, env=env)

    monkeypatch.setattr(MODULE, "_resolve_tool", resolve)
    monkeypatch.setattr(MODULE, "_run", swap_restore)
    result = MODULE.build(source, tmp_path / "output")
    assert len(result["runtime_sha256"]) == 64


@pytest.mark.parametrize(
    ("relative", "mutation"),
    [
        ("src/main.rs", b"\n// source mutation\n"),
        ("Cargo.lock", b"\n# lock mutation\n"),
        ("rust-toolchain.toml", b"\n# toolchain mutation\n"),
        (".cargo/config.toml", b"\n# flag mutation\n"),
        ("generated/source_epoch.rs", b"\n// generated input mutation\n"),
    ],
)
def test_input_mutation_changes_provenance_or_blocks(
    tmp_path: Path, relative: str, mutation: bytes
) -> None:
    source = _source_repo(tmp_path)
    baseline = MODULE.build(source, tmp_path / "baseline")
    path = source / "native/hermetic-package" / relative
    path.write_bytes(path.read_bytes() + mutation)
    _git("add", ".", cwd=source)
    _git("commit", "-qm", f"mutate {relative}", cwd=source)
    try:
        changed = MODULE.build(source, tmp_path / "changed")
    except MODULE.BuildBlocked:
        return
    assert changed["manifest_sha256"] != baseline["manifest_sha256"]
