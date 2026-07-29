from __future__ import annotations

import importlib.util
import json
import shutil
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
