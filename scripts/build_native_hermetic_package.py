#!/usr/bin/env python3
"""Build the authority-neutral NF1 package with complete hermetic provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path
from typing import Any

SCHEMA = "trustforge.native-hermetic-provenance/v1"
TARGET = "x86_64-unknown-linux-musl"
EPOCH = 1_700_000_000
CRATE_PATH = Path("native/hermetic-package")
FORBIDDEN_AUTHORITY_NAMES = frozenset(
    {
        "actor",
        "actor_id",
        "key",
        "key_id",
        "raw_key",
        "raw_public_key",
        "signer",
        "signer_id",
        "trust_anchor",
        "verdict",
        "pass",
        "eligibility",
        "eligible",
        "publication",
        "publication_authority",
    }
)


class BuildBlocked(RuntimeError):
    """An input or invariant was not safe enough to build."""


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> str:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise BuildBlocked(f"command failed: {argv[0]}: {detail.strip()}") from exc
    return result.stdout.strip()


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode()


def _reject_authority_metadata(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.casefold().replace("-", "_")
            if normalized in FORBIDDEN_AUTHORITY_NAMES:
                raise BuildBlocked(f"authority metadata is forbidden: {key}")
            _reject_authority_metadata(child)
    elif isinstance(value, list):
        for child in value:
            _reject_authority_metadata(child)


def _git_identity(source_root: Path) -> dict[str, str]:
    inside = _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=source_root)
    if inside != "true":
        raise BuildBlocked("source root is not a Git worktree")
    status = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=source_root,
    )
    if status:
        raise BuildBlocked("source Git worktree is dirty")
    return {
        "commit": _run(["git", "rev-parse", "HEAD"], cwd=source_root),
        "tree": _run(["git", "rev-parse", "HEAD^{tree}"], cwd=source_root),
    }


def _regular_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise BuildBlocked(f"symlink is forbidden: {path}")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise BuildBlocked(f"special file is forbidden: {path}")
    return files


def _entry(path: Path, base: Path) -> dict[str, Any]:
    relative = path.relative_to(base).as_posix()
    mode = stat.S_IMODE(path.stat().st_mode)
    return {
        "path": relative,
        "mode": f"{mode:04o}",
        "size": path.stat().st_size,
        "sha256": _digest(path),
    }


def _package_entries(root: Path) -> list[dict[str, Any]]:
    entries = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        mode = stat.S_IMODE(path.stat().st_mode)
        record: dict[str, Any] = {
            "path": path.relative_to(root).as_posix(),
            "type": "directory" if path.is_dir() else "file",
            "mode": f"{mode:04o}",
        }
        if path.is_file():
            record.update({"size": path.stat().st_size, "sha256": _digest(path)})
        entries.append(record)
    return entries


def _resolve_tool(name: str, *, sysroot: Path | None = None) -> Path:
    candidate = shutil.which(name)
    if candidate:
        candidate_path = Path(candidate)
        if candidate_path.resolve().name == "rustup" and name in {"cargo", "rustc"}:
            rustup = shutil.which("rustup")
            if rustup is None:
                raise BuildBlocked("rustup proxy cannot resolve pinned toolchain")
            resolved = subprocess.run(
                [rustup, "which", "--toolchain", "1.96.0", name],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if resolved.returncode != 0:
                raise BuildBlocked(
                    f"pinned toolchain cannot resolve {name}: {resolved.stderr.strip()}"
                )
            return Path(resolved.stdout.strip()).resolve()
        return candidate_path.resolve()
    if sysroot is not None:
        candidates = sorted(sysroot.rglob(name))
        if len(candidates) == 1:
            return candidates[0].resolve()
    raise BuildBlocked(f"required tool is unavailable: {name}")


def _tool_record(
    path: Path, version: str, logical_name: str | None = None
) -> dict[str, Any]:
    return {
        "name": logical_name or path.name,
        "size": path.stat().st_size,
        "sha256": _digest(path),
        "version": version,
    }


def _toolchain(cargo: Path, rustc: Path, source_root: Path) -> dict[str, Any]:
    rustc_verbose = _run([str(rustc), "--version", "--verbose"], cwd=source_root)
    if "release: 1.96.0" not in rustc_verbose:
        raise BuildBlocked("rustc does not match pinned 1.96.0 toolchain")
    cargo_verbose = _run([str(cargo), "--version", "--verbose"], cwd=source_root)
    sysroot = Path(_run([str(rustc), "--print", "sysroot"], cwd=source_root)).resolve()
    target_libdir = Path(
        _run(
            [str(rustc), "--print", "target-libdir", "--target", TARGET],
            cwd=source_root,
        )
    ).resolve()
    if not target_libdir.is_dir():
        raise BuildBlocked(f"target sysroot is unavailable: {TARGET}")
    linker = _resolve_tool("rust-lld", sysroot=sysroot)
    sysroot_files = [_entry(path, sysroot) for path in _regular_files(target_libdir)]
    if not any("libc-" in item["path"] for item in sysroot_files):
        raise BuildBlocked("target libc closure is absent")
    return {
        "target": TARGET,
        "rustc": _tool_record(rustc, rustc_verbose, "rustc"),
        "cargo": _tool_record(cargo, cargo_verbose, "cargo"),
        "linker": _tool_record(
            linker,
            _run([str(linker), "-flavor", "gnu", "--version"], cwd=source_root),
            "rust-lld",
        ),
        "target_libdir_entries": sysroot_files,
    }


def _elf_static_assertions(binary: Path) -> dict[str, Any]:
    data = binary.read_bytes()
    if data[:4] != b"\x7fELF":
        raise BuildBlocked("runtime is not ELF")
    readelf = shutil.which("readelf") or shutil.which("llvm-readelf")
    if readelf is None:
        # Rust's musl target is self-contained. Conservatively scan the ELF
        # string table as well as recording the target sysroot closure.
        if b"libc.so" in data or b"ld-linux" in data or b"/lib/ld-" in data:
            raise BuildBlocked("runtime contains a dynamic loader reference")
        return {
            "method": "elf-byte-static-closure",
            "pt_interp": False,
            "dt_needed": [],
        }
    program = _run([readelf, "-l", str(binary)], cwd=binary.parent)
    dynamic = _run([readelf, "-d", str(binary)], cwd=binary.parent)
    if "INTERP" in program or "(NEEDED)" in dynamic:
        raise BuildBlocked("runtime has an ambient dynamic dependency")
    return {"method": readelf, "pt_interp": False, "dt_needed": []}


def _cargo_resolution(crate: Path) -> dict[str, Any]:
    lock = tomllib.loads((crate / "Cargo.lock").read_text(encoding="utf-8"))
    packages = lock.get("package")
    if not isinstance(packages, list) or not packages:
        raise BuildBlocked("Cargo.lock has no package closure")
    normalized = []
    third_party = []
    for package in packages:
        if not isinstance(package, dict):
            raise BuildBlocked("Cargo.lock package entry is malformed")
        record = {
            key: package[key]
            for key in ("name", "version", "source", "checksum")
            if key in package
        }
        normalized.append(record)
        if package.get("name") != "trustforge-native-foundation":
            third_party.append(record)
    vendor = crate / "vendor"
    vendor_entries = (
        [_entry(path, vendor) for path in _regular_files(vendor)]
        if vendor.exists()
        else []
    )
    if third_party and not vendor_entries:
        raise BuildBlocked(
            "third-party Cargo dependencies require a pinned vendor tree"
        )
    return {
        "packages": normalized,
        "third_party_dependencies": third_party,
        "vendor_entries": vendor_entries,
    }


def _write_archive(stage: Path, archive_path: Path) -> None:
    with tarfile.open(archive_path, "w", format=tarfile.USTAR_FORMAT) as archive:
        for path in sorted(stage.rglob("*"), key=lambda item: item.as_posix()):
            relative = path.relative_to(stage).as_posix()
            info = archive.gettarinfo(str(path), arcname=relative)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = EPOCH
            info.mode = 0o555 if path.is_dir() or os.access(path, os.X_OK) else 0o444
            if path.is_dir():
                archive.addfile(info)
            else:
                with path.open("rb") as stream:
                    archive.addfile(info, stream)


def build(source_root: Path, output_dir: Path) -> dict[str, Any]:
    source_root = source_root.resolve()
    crate = source_root / CRATE_PATH
    identity = _git_identity(source_root)
    required = {
        Path("Cargo.toml"),
        Path("Cargo.lock"),
        Path("rust-toolchain.toml"),
        Path(".cargo/config.toml"),
        Path("generated/source_epoch.rs"),
        Path("src/main.rs"),
        Path("package/fixed-config.json"),
        Path("package/public-metadata-format.json"),
    }
    found = {
        path.relative_to(crate)
        for path in _regular_files(crate)
        if "target" not in path.relative_to(crate).parts
    }
    if not required.issubset(found):
        raise BuildBlocked(
            f"required source inputs missing: {sorted(required - found)}"
        )
    source_entries = [_entry(crate / path, crate) for path in sorted(found)]
    builder_path = source_root / "scripts/build_native_hermetic_package.py"
    if not builder_path.is_file() or builder_path.is_symlink():
        raise BuildBlocked("canonical builder source is missing or unsafe")
    builder_entry = _entry(builder_path, source_root)
    builder_entry["path"] = builder_path.relative_to(source_root).as_posix()
    source_entries.append(builder_entry)
    generated_input = crate / "generated/source_epoch.rs"
    expected_generated = f'pub const SOURCE_EPOCH: &str = "{EPOCH}";\n'.encode()
    if generated_input.read_bytes() != expected_generated:
        raise BuildBlocked("generated source does not match canonical recipe")

    cargo = _resolve_tool("cargo")
    rustc = _resolve_tool("rustc")
    toolchain = _toolchain(cargo, rustc, source_root)
    linker = _resolve_tool(
        "rust-lld",
        sysroot=Path(_run([str(rustc), "--print", "sysroot"], cwd=source_root)),
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    build_crate = output_dir / ".build-input"
    shutil.copytree(crate, build_crate)
    with tempfile.TemporaryDirectory(prefix="nf1-cargo-home-") as cargo_home:
        target_dir = output_dir / ".target"
        environment = {
            "PATH": str(Path(cargo).parent),
            "HOME": str(output_dir / ".no-home"),
            "CARGO_HOME": cargo_home,
            "RUSTC": str(rustc),
            "RUSTUP_TOOLCHAIN": "1.96.0",
            "SOURCE_DATE_EPOCH": str(EPOCH),
            "TZ": "UTC",
            "LC_ALL": "C",
            "LANG": "C",
            "CARGO_INCREMENTAL": "0",
            "CARGO_NET_OFFLINE": "true",
            "CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER": str(linker),
            "RUSTFLAGS": (
                f"--remap-path-prefix={build_crate}=/workspace/native/hermetic-package "
                "-C link-arg=--build-id=none"
            ),
        }
        command = [
            str(cargo),
            "build",
            "--manifest-path",
            str(build_crate / "Cargo.toml"),
            "--release",
            "--target",
            TARGET,
            "--target-dir",
            str(target_dir),
            "--locked",
            "--offline",
            "--frozen",
        ]
        _run(command, cwd=build_crate, env=environment)

    runtime = target_dir / TARGET / "release/trustforge-native-foundation"
    stage = output_dir / ".stage"
    (stage / "bin").mkdir(parents=True)
    (stage / "config").mkdir()
    shutil.copyfile(runtime, stage / "bin/trustforge-native-foundation")
    os.chmod(stage / "bin/trustforge-native-foundation", 0o555)
    shutil.copyfile(
        crate / "package/fixed-config.json", stage / "config/fixed-config.json"
    )
    shutil.copyfile(
        crate / "package/public-metadata-format.json",
        stage / "config/public-metadata-format.json",
    )
    for path in (stage / "config").iterdir():
        os.chmod(path, 0o444)
    os.chmod(stage / "bin", 0o555)
    os.chmod(stage / "config", 0o555)

    package_entries = _package_entries(stage)
    provenance: dict[str, Any] = {
        "schema": SCHEMA,
        "vcs": identity,
        "sources": source_entries,
        "cargo_resolution": _cargo_resolution(crate),
        "generated": {
            "recipe": "scripts/build_native_hermetic_package.py:EPOCH",
            "path": "generated/source_epoch.rs",
            "sha256": _digest(generated_input),
            "size": generated_input.stat().st_size,
        },
        "toolchain": toolchain,
        "environment": {
            key: (
                "toolchain:rust-lld"
                if key == "CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER"
                else environment[key]
            )
            for key in (
                "SOURCE_DATE_EPOCH",
                "TZ",
                "LC_ALL",
                "LANG",
                "CARGO_INCREMENTAL",
                "CARGO_NET_OFFLINE",
                "CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER",
                "RUSTUP_TOOLCHAIN",
            )
        },
        "build": {
            "offline": True,
            "locked": True,
            "frozen": True,
            "target": TARGET,
            "rustflags": environment["RUSTFLAGS"].replace(
                str(build_crate), "/build-input"
            ),
        },
        "runtime_closure": _elf_static_assertions(
            stage / "bin/trustforge-native-foundation"
        ),
        "package_entries": package_entries,
    }
    _reject_authority_metadata(provenance)
    manifest = output_dir / "native-hermetic-provenance.json"
    manifest.write_bytes(_canonical_json(provenance))
    archive = output_dir / "native-hermetic-package.tar"
    _write_archive(stage, archive)
    os.chmod(stage / "bin", 0o755)
    os.chmod(stage / "config", 0o755)
    shutil.rmtree(stage)
    shutil.rmtree(target_dir)
    result = {
        "manifest_sha256": _digest(manifest),
        "archive_sha256": _digest(archive),
        "runtime_sha256": next(
            item["sha256"]
            for item in package_entries
            if item["path"] == "bin/trustforge-native-foundation"
        ),
    }
    shutil.rmtree(build_crate)
    (output_dir / "native-hermetic-digests.json").write_bytes(_canonical_json(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(args.source_root, args.output_dir)
    except BuildBlocked as exc:
        raise SystemExit(f"BLOCK: {exc}") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
