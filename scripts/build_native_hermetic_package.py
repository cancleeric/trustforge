#!/usr/bin/env python3
"""Build the authority-neutral NF1 package with complete hermetic provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
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
FORBIDDEN_AUTHORITY_VALUE = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:actor|key|key_id|private_key|raw_key|"
    r"raw_public_key|signer|trust[-_ ]?anchor|verdict|pass|eligibility|eligible|"
    r"release_eligibility|publication_authority)(?:[^a-z0-9]|$)"
)
FORBIDDEN_AUTHORITY_ALIASES = frozenset(
    {
        "actor",
        "key",
        "key_id",
        "private_key",
        "raw_key",
        "raw_public_key",
        "signer",
        "trust_anchor",
        "verdict",
        "pass",
        "eligibility",
        "eligible",
        "release_eligibility",
        "publication_authority",
    }
)
ALLOWED_MANIFEST_KEYS = frozenset(
    {
        "schema",
        "vcs",
        "commit",
        "tree",
        "sources",
        "path",
        "mode",
        "size",
        "sha256",
        "cargo_resolution",
        "packages",
        "third_party_dependencies",
        "vendor_entries",
        "name",
        "version",
        "source",
        "checksum",
        "generated",
        "recipe",
        "toolchain",
        "target",
        "rustc",
        "cargo",
        "rustup",
        "linker",
        "target_libdir_entries",
        "host_sysroot_entries",
        "host_platform",
        "builder_runtime",
        "python_entries",
        "dynamic_dependencies",
        "dyld_cache_map",
        "dyld_cache",
        "dyld_subcache",
        "uuid",
        "code_directory_sha256",
        "os_build",
        "kernel",
        "environment",
        "build",
        "argv",
        "offline",
        "locked",
        "frozen",
        "rustflags",
        "runtime_closure",
        "method",
        "pt_interp",
        "dt_needed",
        "package_entries",
        "type",
        "PATH",
        "HOME",
        "CARGO_HOME",
        "RUSTC",
        "SOURCE_DATE_EPOCH",
        "TZ",
        "LC_ALL",
        "LANG",
        "CARGO_INCREMENTAL",
        "CARGO_NET_OFFLINE",
        "CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER",
        "RUSTUP_TOOLCHAIN",
        "RUSTFLAGS",
        "WRAPPER_AND_AMBIENT_KNOBS",
    }
)
_DYLD_CACHE_DIGEST_CACHE: dict[tuple[str, int, int, int], str] = {}
_SEALED_EXEC_TEST_HOOK: Any = None


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


def _run_sealed(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    sealed_paths: list[Path],
) -> str:
    def identity(path: Path, *, include_digest: bool) -> tuple[Any, ...]:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o222:
            raise BuildBlocked(f"sealed tool is not readonly regular file: {path}")
        base = (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
        return (*base, _digest(path)) if include_digest else base

    before = {path: identity(path, include_digest=True) for path in sealed_paths}
    directory_before = {
        path: (path.stat().st_ino, path.stat().st_mtime_ns, path.stat().st_ctime_ns)
        for path in {item.parent for item in sealed_paths}
    }
    if _SEALED_EXEC_TEST_HOOK is not None:
        _SEALED_EXEC_TEST_HOOK(sealed_paths)
    changed = threading.Event()

    def monitor() -> None:
        while not changed.is_set():
            try:
                for path, expected in before.items():
                    if identity(path, include_digest=False) != expected[:-1]:
                        changed.set()
                        return
                for path, expected in directory_before.items():
                    info = path.stat()
                    if (info.st_ino, info.st_mtime_ns, info.st_ctime_ns) != expected:
                        changed.set()
                        return
            except (OSError, BuildBlocked):
                changed.set()
                return
            time.sleep(0.001)

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    finally:
        stop_was_change = changed.is_set()
        changed.set()
        watcher.join()
    if stop_was_change or any(
        identity(path, include_digest=True) != expected
        for path, expected in before.items()
    ):
        raise BuildBlocked("sealed tool pathname changed during execution")
    if result.returncode:
        raise BuildBlocked(f"sealed build command failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode()


def _reject_authority_metadata(value: Any) -> None:
    def aliases(text: str) -> bool:
        snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
        snake = re.sub(r"[^a-zA-Z0-9]+", "_", snake).casefold().strip("_")
        padded = f"_{snake}_"
        return any(f"_{term}_" in padded for term in FORBIDDEN_AUTHORITY_ALIASES)

    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            if (
                key not in ALLOWED_MANIFEST_KEYS
                or normalized
                in {
                    re.sub(r"[^a-z0-9]", "", item) for item in FORBIDDEN_AUTHORITY_NAMES
                }
                or aliases(key)
            ):
                raise BuildBlocked(f"authority metadata is forbidden: {key}")
            _reject_authority_metadata(child)
    elif isinstance(value, list):
        for child in value:
            _reject_authority_metadata(child)
    elif isinstance(value, str) and (
        FORBIDDEN_AUTHORITY_VALUE.search(value) or aliases(value)
    ):
        raise BuildBlocked("authority metadata value is forbidden")


def _validate_manifest_shape(value: dict[str, Any]) -> None:
    def exact(record: Any, keys: set[str], label: str) -> dict[str, Any]:
        if not isinstance(record, dict) or set(record) != keys:
            raise BuildBlocked(f"{label} schema is not exact")
        return record

    def digest_entries(
        entries: Any, label: str, *, allow_absolute: bool = False
    ) -> None:
        if not isinstance(entries, list) or not entries:
            raise BuildBlocked(f"{label} cardinality is invalid")
        for entry in entries:
            exact(entry, {"path", "mode", "size", "sha256"}, label)
            if (
                not isinstance(entry["path"], str)
                or (entry["path"].startswith("/") and not allow_absolute)
                or ".." in Path(entry["path"]).parts
                or not re.fullmatch(r"[0-7]{4}", entry["mode"])
                or not isinstance(entry["size"], int)
                or entry["size"] < 0
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
            ):
                raise BuildBlocked(f"{label} field type is invalid")

    if set(value) != {
        "schema",
        "vcs",
        "sources",
        "cargo_resolution",
        "generated",
        "toolchain",
        "builder_runtime",
        "environment",
        "build",
        "runtime_closure",
        "package_entries",
    }:
        raise BuildBlocked("provenance top-level schema is not exact")
    if value["schema"] != SCHEMA:
        raise BuildBlocked("provenance schema enum is invalid")
    if set(value["vcs"]) != {"commit", "tree"}:
        raise BuildBlocked("VCS schema is not exact")
    if not all(
        isinstance(value["vcs"][field], str)
        and re.fullmatch(r"[0-9a-f]{40}", value["vcs"][field])
        for field in ("commit", "tree")
    ):
        raise BuildBlocked("VCS field type is invalid")
    digest_entries(value["sources"], "source entry")
    cargo_resolution = exact(
        value["cargo_resolution"],
        {"packages", "third_party_dependencies", "vendor_entries"},
        "Cargo resolution",
    )
    if (
        not isinstance(cargo_resolution["packages"], list)
        or len(cargo_resolution["packages"]) != 1
    ):
        raise BuildBlocked("Cargo package cardinality is invalid")
    package = exact(
        cargo_resolution["packages"][0], {"name", "version"}, "Cargo package"
    )
    if package != {
        "name": "trustforge-native-foundation",
        "version": "0.1.0",
    }:
        raise BuildBlocked("Cargo package enum is invalid")
    if not isinstance(cargo_resolution["third_party_dependencies"], list):
        raise BuildBlocked("third-party Cargo dependency type is invalid")
    for dependency in cargo_resolution["third_party_dependencies"]:
        exact(
            dependency,
            {"name", "version", "source", "checksum"},
            "third-party Cargo dependency",
        )
    if not isinstance(cargo_resolution["vendor_entries"], list):
        raise BuildBlocked("vendor entry type is invalid")
    if cargo_resolution["vendor_entries"]:
        digest_entries(cargo_resolution["vendor_entries"], "vendor entry")
    if cargo_resolution["third_party_dependencies"] != []:
        if cargo_resolution["vendor_entries"] == []:
            raise BuildBlocked("third-party Cargo closure lacks vendor entries")
    generated = exact(
        value["generated"], {"recipe", "path", "sha256", "size"}, "generated"
    )
    if not isinstance(generated["size"], int) or not re.fullmatch(
        r"[0-9a-f]{64}", generated["sha256"]
    ):
        raise BuildBlocked("generated field type is invalid")
    if generated["path"] != "generated/source_epoch.rs" or generated["size"] <= 0:
        raise BuildBlocked("generated field enum is invalid")
    toolchain = value["toolchain"]
    exact(
        toolchain,
        {
            "target",
            "rustc",
            "cargo",
            "linker",
            "rustup",
            "target_libdir_entries",
            "host_sysroot_entries",
            "host_platform",
        },
        "toolchain",
    )
    if toolchain["target"] != TARGET:
        raise BuildBlocked("toolchain target enum is invalid")
    for name in ("rustc", "cargo", "linker", "rustup"):
        record = exact(toolchain[name], {"name", "size", "sha256", "version"}, name)
        if record["name"] != ("rust-lld" if name == "linker" else name):
            raise BuildBlocked(f"{name} enum is invalid")
        if (
            not isinstance(record["size"], int)
            or record["size"] <= 0
            or not isinstance(record["version"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
        ):
            raise BuildBlocked(f"{name} field format is invalid")
    digest_entries(toolchain["target_libdir_entries"], "target sysroot")
    digest_entries(toolchain["host_sysroot_entries"], "host sysroot")
    exact(toolchain["host_platform"], {"os_build", "kernel"}, "host platform")
    runtime = exact(
        value["builder_runtime"],
        {
            "python_entries",
            "dynamic_dependencies",
            "dyld_cache_map",
            "dyld_cache",
            "dyld_subcache",
        },
        "builder runtime",
    )
    digest_entries(runtime["python_entries"], "Python runtime", allow_absolute=True)
    if not isinstance(runtime["dynamic_dependencies"], list) or not all(
        isinstance(item, str) for item in runtime["dynamic_dependencies"]
    ):
        raise BuildBlocked("dynamic dependency schema is invalid")
    map_record = exact(
        runtime["dyld_cache_map"],
        {"path", "mode", "size", "sha256"},
        "dyld cache map",
    )
    cache_record = exact(
        runtime["dyld_cache"],
        {"path", "mode", "size", "sha256", "uuid", "code_directory_sha256"},
        "dyld cache",
    )
    subcache_record = exact(
        runtime["dyld_subcache"],
        {"path", "mode", "size", "sha256", "code_directory_sha256"},
        "dyld subcache",
    )
    for label, record in (
        ("dyld cache map", map_record),
        ("dyld cache", cache_record),
        ("dyld subcache", subcache_record),
    ):
        if (
            not isinstance(record["path"], str)
            or record["mode"] != "0755"
            or not isinstance(record["size"], int)
            or record["size"] <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
        ):
            raise BuildBlocked(f"{label} field format is invalid")
    if not re.fullmatch(r"[0-9a-f]{32}", cache_record["uuid"]):
        raise BuildBlocked("dyld cache UUID format is invalid")
    for record in (cache_record, subcache_record):
        if not re.fullmatch(r"[0-9a-f]{64}", record["code_directory_sha256"]):
            raise BuildBlocked("dyld cache CDHash format is invalid")
    if set(value["build"]) != {
        "argv",
        "offline",
        "locked",
        "frozen",
        "target",
        "rustflags",
    } or (
        value["build"]["offline"],
        value["build"]["locked"],
        value["build"]["frozen"],
        value["build"]["target"],
    ) != (True, True, True, TARGET):
        raise BuildBlocked("build schema or enum is invalid")
    if (
        not isinstance(value["build"]["argv"], list)
        or value["build"]["argv"]
        != [
            "cargo",
            "build",
            "--manifest-path",
            "/build-input/Cargo.toml",
            "--release",
            "--target",
            TARGET,
            "--target-dir",
            "/build-output",
            "--locked",
            "--offline",
            "--frozen",
        ]
        or not isinstance(value["build"]["rustflags"], str)
    ):
        raise BuildBlocked("build argv/rustflags schema is invalid")
    if set(value["runtime_closure"]) != {"method", "pt_interp", "dt_needed"}:
        raise BuildBlocked("runtime closure schema is not exact")
    if value["runtime_closure"] != {
        "method": "bounds-checked-elf64-parser/v1",
        "pt_interp": False,
        "dt_needed": [],
    }:
        raise BuildBlocked("runtime closure enum is invalid")
    expected_environment = {
        "PATH",
        "HOME",
        "CARGO_HOME",
        "RUSTC",
        "SOURCE_DATE_EPOCH",
        "TZ",
        "LC_ALL",
        "LANG",
        "CARGO_INCREMENTAL",
        "CARGO_NET_OFFLINE",
        "CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER",
        "RUSTUP_TOOLCHAIN",
        "RUSTFLAGS",
        "WRAPPER_AND_AMBIENT_KNOBS",
    }
    if set(value["environment"]) != expected_environment:
        raise BuildBlocked("environment allowlist schema is not exact")
    expected_values = {
        "PATH": "toolchain:bin-only",
        "HOME": "isolated:non-user-empty-home",
        "CARGO_HOME": "isolated:fresh-empty-cargo-home",
        "RUSTC": "toolchain:locked-rustc",
        "SOURCE_DATE_EPOCH": str(EPOCH),
        "TZ": "UTC",
        "LC_ALL": "C",
        "LANG": "C",
        "CARGO_INCREMENTAL": "0",
        "CARGO_NET_OFFLINE": "true",
        "CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER": ("toolchain:locked-rust-lld"),
        "RUSTUP_TOOLCHAIN": "1.96.0",
        "WRAPPER_AND_AMBIENT_KNOBS": "rejected:not-in-subprocess-environment",
    }
    for key, expected in expected_values.items():
        if value["environment"][key] != expected:
            raise BuildBlocked(f"environment enum is invalid: {key}")
    if not isinstance(value["environment"]["RUSTFLAGS"], str):
        raise BuildBlocked("RUSTFLAGS type is invalid")
    if value["environment"]["RUSTFLAGS"] != value["build"]["rustflags"]:
        raise BuildBlocked("RUSTFLAGS binding is inconsistent")
    if (
        not isinstance(value["package_entries"], list)
        or len(value["package_entries"]) != 5
    ):
        raise BuildBlocked("package entry cardinality is invalid")
    for entry in value["package_entries"]:
        expected = (
            {"path", "type", "mode"}
            if entry.get("type") == "directory"
            else {"path", "type", "mode", "size", "sha256"}
        )
        exact(entry, expected, "package entry")
        if (
            not isinstance(entry["path"], str)
            or entry["type"] not in {"directory", "file"}
            or entry["mode"] not in {"0444", "0555"}
        ):
            raise BuildBlocked("package entry field format is invalid")
        if entry["type"] == "file" and (
            not isinstance(entry["size"], int)
            or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        ):
            raise BuildBlocked("package file field format is invalid")
    if {entry["path"] for entry in value["package_entries"]} != {
        "bin",
        "config",
        "bin/trustforge-native-foundation",
        "config/fixed-config.json",
        "config/public-metadata-format.json",
    }:
        raise BuildBlocked("package path enum is invalid")


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
            if path.stat().st_nlink != 1:
                raise BuildBlocked(f"multiply-linked file is forbidden: {path}")
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
    if not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
        raise BuildBlocked(f"tool is not a singly-linked regular file: {path}")
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
    host_sysroot_files = [
        _entry(path, sysroot) for path in _regular_files(sysroot / "lib")
    ]
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
        "rustup": _tool_record(
            _resolve_tool("rustup"),
            _run([str(_resolve_tool("rustup")), "--version"], cwd=source_root),
            "rustup",
        ),
        "target_libdir_entries": sysroot_files,
        "host_sysroot_entries": host_sysroot_files,
        "host_platform": {
            "os_build": _run(["/usr/bin/sw_vers", "-buildVersion"], cwd=source_root),
            "kernel": _run(["/usr/bin/uname", "-mrs"], cwd=source_root),
        },
    }


def _builder_runtime(source_root: Path) -> dict[str, Any]:
    python = Path(sys.executable).resolve()
    probe = (
        "import argparse,hashlib,json,os,re,shutil,stat,struct,subprocess,"
        "tarfile,tempfile,tomllib,pathlib,typing,sys;"
        "print(json.dumps(sorted({m.__file__ for m in sys.modules.values() "
        "if getattr(m,'__file__',None)})))"
    )
    module_paths = json.loads(
        _run([str(python), "-I", "-S", "-c", probe], cwd=source_root)
    )
    paths = [python]
    for raw in module_paths:
        path = Path(raw).resolve()
        if path.is_file() and path not in paths:
            paths.append(path)
    otool = Path("/usr/bin/otool")
    dependencies: set[str] = set()
    queue = list(paths)
    inspected: set[Path] = set()
    while queue:
        path = queue.pop()
        if path in inspected:
            continue
        inspected.add(path)
        if (
            path == python
            or path.suffix in {".so", ".dylib"}
            or os.access(path, os.X_OK)
        ):
            output = _run([str(otool), "-L", str(path)], cwd=source_root)
            for line in output.splitlines()[1:]:
                dependency = line.strip().split(" (", 1)[0]
                if dependency:
                    dependencies.add(dependency)
                    dependency_path = Path(dependency)
                    if dependency_path.is_absolute() and dependency_path.is_file():
                        resolved = dependency_path.resolve()
                        if resolved not in paths:
                            paths.append(resolved)
                            queue.append(resolved)
    entries = []
    for path in sorted(paths, key=lambda item: str(item)):
        if path.stat().st_nlink != 1:
            raise BuildBlocked(f"Python runtime file is multiply linked: {path}")
        entries.append(
            {
                "path": str(path),
                "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
                "size": path.stat().st_size,
                "sha256": _digest(path),
            }
        )
    dyld_map = Path(
        "/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld/"
        "dyld_shared_cache_arm64e.map"
    )
    if not dyld_map.is_file():
        raise BuildBlocked("host dyld shared-cache map is unavailable")
    dyld_cache = dyld_map.with_name("dyld_shared_cache_arm64e")
    dyld_subcache = dyld_map.with_name("dyld_shared_cache_arm64e.01")

    def sealed_cache_record(path: Path) -> dict[str, Any]:
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise BuildBlocked("dyld shared-cache file is not root-owned immutable")
        cache_key = (str(path), info.st_ino, info.st_size, info.st_mtime_ns)
        digest = _DYLD_CACHE_DIGEST_CACHE.get(cache_key)
        if digest is None:
            digest = _digest(path)
            _DYLD_CACHE_DIGEST_CACHE[cache_key] = digest
        signature_result = subprocess.run(
            ["/usr/bin/codesign", "-d", "--verbose=4", str(path)],
            cwd=source_root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if signature_result.returncode:
            raise BuildBlocked("dyld shared-cache code signature is unverifiable")
        signature = signature_result.stdout + signature_result.stderr
        match = re.search(r"CandidateCDHashFull sha256=([0-9a-f]{64})", signature)
        if match is None:
            raise BuildBlocked("dyld shared-cache code-directory digest is absent")
        record = _entry(path, path.parent)
        record["code_directory_sha256"] = match.group(1)
        return record

    main_cache = sealed_cache_record(dyld_cache)
    with dyld_cache.open("rb") as stream:
        stream.seek(88)
        cache_uuid = stream.read(16)
    if len(cache_uuid) != 16:
        raise BuildBlocked("dyld shared-cache UUID is unavailable")
    main_cache["uuid"] = cache_uuid.hex()
    return {
        "python_entries": entries,
        "dynamic_dependencies": sorted(dependencies),
        "dyld_cache_map": _entry(dyld_map, dyld_map.parent),
        "dyld_cache": main_cache,
        "dyld_subcache": sealed_cache_record(dyld_subcache),
    }


def _elf_static_assertions(binary: Path) -> dict[str, Any]:
    data = binary.read_bytes()
    if len(data) < 64 or data[:4] != b"\x7fELF":
        raise BuildBlocked("runtime is not a bounds-valid ELF64 file")
    if data[4] != 2 or data[5] not in (1, 2):
        raise BuildBlocked("only ELF64 with known endianness is supported")
    endian = "<" if data[5] == 1 else ">"
    header = struct.unpack_from(endian + "HHIQQQIHHHHHH", data, 16)
    if header[1] != 62:
        raise BuildBlocked("ELF machine is not x86_64")
    phoff, shoff = header[4], header[5]
    phentsize, phnum = header[8], header[9]
    shentsize, shnum = header[10], header[11]
    if phentsize < 56 or shentsize < 64:
        raise BuildBlocked("ELF table entry size is invalid")
    if phoff + phentsize * phnum > len(data) or shoff + shentsize * shnum > len(data):
        raise BuildBlocked("ELF table exceeds file bounds")
    for index in range(phnum):
        offset = phoff + index * phentsize
        p_type = struct.unpack_from(endian + "I", data, offset)[0]
        p_offset, p_filesz, p_memsz = (
            struct.unpack_from(endian + "Q", data, offset + 8)[0],
            struct.unpack_from(endian + "Q", data, offset + 32)[0],
            struct.unpack_from(endian + "Q", data, offset + 40)[0],
        )
        if p_filesz > p_memsz or p_offset + p_filesz > len(data):
            raise BuildBlocked("ELF program segment exceeds file bounds")
        if p_type == 3:
            raise BuildBlocked("runtime contains PT_INTERP")
        if p_type == 2:
            if p_filesz % 16:
                raise BuildBlocked("PT_DYNAMIC entry table is malformed")
            for position in range(p_offset, p_offset + p_filesz, 16):
                tag = struct.unpack_from(endian + "q", data, position)[0]
                if tag == 1:
                    raise BuildBlocked("runtime contains DT_NEEDED in PT_DYNAMIC")
            raise BuildBlocked("runtime contains PT_DYNAMIC")
    needed = 0
    for index in range(shnum):
        offset = shoff + index * shentsize
        sh_type = struct.unpack_from(endian + "I", data, offset + 4)[0]
        sh_offset, sh_size, sh_entsize = (
            struct.unpack_from(endian + "QQ", data, offset + 24)[0],
            struct.unpack_from(endian + "Q", data, offset + 32)[0],
            struct.unpack_from(endian + "Q", data, offset + 56)[0],
        )
        if sh_type != 8 and sh_offset + sh_size > len(data):
            raise BuildBlocked("ELF section exceeds file bounds")
        if sh_type == 6:
            entry_size = sh_entsize or 16
            if entry_size < 16 or sh_size % entry_size:
                raise BuildBlocked("ELF dynamic section is malformed")
            for position in range(sh_offset, sh_offset + sh_size, entry_size):
                tag = struct.unpack_from(endian + "q", data, position)[0]
                if tag == 1:
                    needed += 1
    if needed:
        raise BuildBlocked("runtime contains DT_NEEDED")
    return {
        "method": "bounds-checked-elf64-parser/v1",
        "pt_interp": False,
        "dt_needed": [],
    }


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


def _validate_toolchain_lock(
    crate: Path, toolchain: dict[str, Any], builder_runtime: dict[str, Any]
) -> None:
    lock_path = crate / "toolchain-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    expected_keys = {
        "schema",
        "target",
        "cargo_sha256",
        "rustc_sha256",
        "rust_lld_sha256",
        "rustup_sha256",
        "host_sysroot_tree_sha256",
        "host_os_build",
        "host_kernel",
        "target_sysroot_tree_sha256",
        "builder_python_entries_sha256",
        "builder_dynamic_dependencies_sha256",
        "builder_dyld_cache_map_sha256",
        "builder_dyld_cache_sha256",
        "builder_dyld_cache_subcache_sha256",
        "builder_dyld_cache_uuid",
        "builder_dyld_cache_cdhash",
        "builder_dyld_cache_subcache_cdhash",
    }
    if set(lock) != expected_keys:
        raise BuildBlocked("toolchain lock schema is not exact")
    target_tree = hashlib.sha256(
        _canonical_json(toolchain["target_libdir_entries"])
    ).hexdigest()
    host_tree = hashlib.sha256(
        _canonical_json(toolchain["host_sysroot_entries"])
    ).hexdigest()
    observed = {
        "schema": "trustforge.native-toolchain-lock/v1",
        "target": TARGET,
        "cargo_sha256": toolchain["cargo"]["sha256"],
        "rustc_sha256": toolchain["rustc"]["sha256"],
        "rust_lld_sha256": toolchain["linker"]["sha256"],
        "rustup_sha256": toolchain["rustup"]["sha256"],
        "host_sysroot_tree_sha256": host_tree,
        "host_os_build": toolchain["host_platform"]["os_build"],
        "host_kernel": toolchain["host_platform"]["kernel"],
        "target_sysroot_tree_sha256": target_tree,
        "builder_python_entries_sha256": hashlib.sha256(
            _canonical_json(builder_runtime["python_entries"])
        ).hexdigest(),
        "builder_dynamic_dependencies_sha256": hashlib.sha256(
            _canonical_json(builder_runtime["dynamic_dependencies"])
        ).hexdigest(),
        "builder_dyld_cache_map_sha256": builder_runtime["dyld_cache_map"]["sha256"],
        "builder_dyld_cache_sha256": builder_runtime["dyld_cache"]["sha256"],
        "builder_dyld_cache_subcache_sha256": builder_runtime["dyld_subcache"][
            "sha256"
        ],
        "builder_dyld_cache_uuid": builder_runtime["dyld_cache"]["uuid"],
        "builder_dyld_cache_cdhash": builder_runtime["dyld_cache"][
            "code_directory_sha256"
        ],
        "builder_dyld_cache_subcache_cdhash": builder_runtime["dyld_subcache"][
            "code_directory_sha256"
        ],
    }
    if lock != observed:
        raise BuildBlocked(
            "toolchain or host/target sysroot differs from repository lock"
        )


def _reject_ancestor_cargo_config(path: Path) -> None:
    for ancestor in (path, *path.parents):
        cargo_dir = ancestor / ".cargo"
        for name in ("config", "config.toml"):
            if (cargo_dir / name).exists():
                raise BuildBlocked(
                    f"ambient ancestor Cargo config is forbidden: {cargo_dir / name}"
                )


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
        Path("toolchain-lock.json"),
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
    if (
        not builder_path.is_file()
        or builder_path.is_symlink()
        or builder_path.stat().st_nlink != 1
    ):
        raise BuildBlocked("canonical builder source is missing or unsafe")
    builder_entry = _entry(builder_path, source_root)
    builder_entry["path"] = builder_path.relative_to(source_root).as_posix()
    source_entries.append(builder_entry)
    expected_generated = f'pub const SOURCE_EPOCH: &str = "{EPOCH}";\n'.encode()

    cargo = _resolve_tool("cargo")
    rustc = _resolve_tool("rustc")
    toolchain = _toolchain(cargo, rustc, source_root)
    builder_runtime = _builder_runtime(source_root)
    _validate_toolchain_lock(crate, toolchain, builder_runtime)
    linker = _resolve_tool(
        "rust-lld",
        sysroot=Path(_run([str(rustc), "--print", "sysroot"], cwd=source_root)),
    )
    rustup = _resolve_tool("rustup")
    output_dir.mkdir(parents=True, exist_ok=False)
    _reject_ancestor_cargo_config(output_dir)
    original_sysroot = Path(
        _run([str(rustc), "--print", "sysroot"], cwd=source_root)
    ).resolve()
    tool_snapshot = output_dir / ".tool-input"
    (tool_snapshot / "bin").mkdir(parents=True)
    shutil.copy2(cargo, tool_snapshot / "bin/cargo")
    shutil.copy2(rustc, tool_snapshot / "bin/rustc")
    shutil.copy2(rustup, tool_snapshot / "bin/rustup")
    shutil.copytree(original_sysroot / "lib", tool_snapshot / "lib")
    snapshot_host_entries = [
        _entry(path, tool_snapshot) for path in _regular_files(tool_snapshot / "lib")
    ]
    if snapshot_host_entries != toolchain["host_sysroot_entries"]:
        raise BuildBlocked("sealed toolchain snapshot differs from locked host closure")
    cargo = tool_snapshot / "bin/cargo"
    rustc = tool_snapshot / "bin/rustc"
    linker = tool_snapshot / "lib/rustlib/aarch64-apple-darwin/bin/rust-lld"
    for logical, path in (
        ("cargo", cargo),
        ("rustc", rustc),
        ("rustup", tool_snapshot / "bin/rustup"),
        ("linker", linker),
    ):
        if _digest(path) != toolchain[logical]["sha256"]:
            raise BuildBlocked(f"sealed {logical} snapshot digest mismatch")
    for path in _regular_files(tool_snapshot):
        os.chmod(
            path,
            0o555
            if path in {cargo, rustc, linker, tool_snapshot / "bin/rustup"}
            else 0o444,
        )
    for path in sorted(
        (item for item in tool_snapshot.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        os.chmod(path, 0o555)
    os.chmod(tool_snapshot, 0o555)
    build_crate = output_dir / ".build-input"
    shutil.copytree(crate, build_crate)
    builder_snapshot = output_dir / ".builder-input.py"
    shutil.copy2(builder_path, builder_snapshot)
    if _digest(builder_snapshot) != builder_entry["sha256"]:
        raise BuildBlocked("builder snapshot does not match pinned builder identity")
    snapshot_entries = [
        _entry(path, build_crate)
        for path in _regular_files(build_crate)
        if "target" not in path.relative_to(build_crate).parts
    ]
    if [
        {**item, "path": item["path"]}
        for item in source_entries
        if item["path"] != "scripts/build_native_hermetic_package.py"
    ] != snapshot_entries:
        raise BuildBlocked("source snapshot does not match pinned source identities")
    snapshot_builder_entry = _entry(builder_snapshot, output_dir)
    snapshot_builder_entry["path"] = "scripts/build_native_hermetic_package.py"
    pinned_source_entries = [*snapshot_entries, snapshot_builder_entry]
    generated_input = build_crate / "generated/source_epoch.rs"
    if generated_input.read_bytes() != expected_generated:
        raise BuildBlocked("generated source does not match canonical recipe")
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
                "-C relocation-model=static -C link-arg=--build-id=none "
                "-C link-arg=-no-pie"
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
        _run_sealed(
            command,
            cwd=build_crate,
            env=environment,
            sealed_paths=[cargo, rustc, linker, tool_snapshot / "bin/rustup"],
        )

    runtime = target_dir / TARGET / "release/trustforge-native-foundation"
    stage = output_dir / ".stage"
    (stage / "bin").mkdir(parents=True)
    (stage / "config").mkdir()
    shutil.copyfile(runtime, stage / "bin/trustforge-native-foundation")
    os.chmod(stage / "bin/trustforge-native-foundation", 0o555)
    shutil.copyfile(
        build_crate / "package/fixed-config.json", stage / "config/fixed-config.json"
    )
    shutil.copyfile(
        build_crate / "package/public-metadata-format.json",
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
        "sources": pinned_source_entries,
        "cargo_resolution": _cargo_resolution(build_crate),
        "generated": {
            "recipe": "scripts/build_native_hermetic_package.py:EPOCH",
            "path": "generated/source_epoch.rs",
            "sha256": _digest(generated_input),
            "size": generated_input.stat().st_size,
        },
        "toolchain": toolchain,
        "builder_runtime": builder_runtime,
        "environment": {
            "PATH": "toolchain:bin-only",
            "HOME": "isolated:non-user-empty-home",
            "CARGO_HOME": "isolated:fresh-empty-cargo-home",
            "RUSTC": "toolchain:locked-rustc",
            "SOURCE_DATE_EPOCH": environment["SOURCE_DATE_EPOCH"],
            "TZ": environment["TZ"],
            "LC_ALL": environment["LC_ALL"],
            "LANG": environment["LANG"],
            "CARGO_INCREMENTAL": environment["CARGO_INCREMENTAL"],
            "CARGO_NET_OFFLINE": environment["CARGO_NET_OFFLINE"],
            "CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER": (
                "toolchain:locked-rust-lld"
            ),
            "RUSTUP_TOOLCHAIN": environment["RUSTUP_TOOLCHAIN"],
            "RUSTFLAGS": environment["RUSTFLAGS"].replace(
                str(build_crate), "/build-input"
            ),
            "WRAPPER_AND_AMBIENT_KNOBS": "rejected:not-in-subprocess-environment",
        },
        "build": {
            "argv": [
                "cargo",
                "build",
                "--manifest-path",
                "/build-input/Cargo.toml",
                "--release",
                "--target",
                TARGET,
                "--target-dir",
                "/build-output",
                "--locked",
                "--offline",
                "--frozen",
            ],
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
    _validate_manifest_shape(provenance)
    _reject_authority_metadata(provenance)
    if _git_identity(source_root) != identity:
        raise BuildBlocked("source VCS identity changed during build")
    current_entries = [_entry(crate / path, crate) for path in sorted(found)]
    if current_entries != source_entries[:-1]:
        raise BuildBlocked("source inputs changed during build")
    end_toolchain = _toolchain(cargo, rustc, source_root)
    for field in ("host_sysroot_entries", "target_libdir_entries"):
        initial_modes = {item["path"]: item["mode"] for item in toolchain[field]}
        for item in end_toolchain[field]:
            if item["path"] in initial_modes:
                item["mode"] = initial_modes[item["path"]]
    end_builder_runtime = _builder_runtime(source_root)
    _validate_toolchain_lock(crate, end_toolchain, end_builder_runtime)
    if end_toolchain != toolchain:
        raise BuildBlocked("toolchain closure changed during build")
    if end_builder_runtime != builder_runtime:
        raise BuildBlocked("Python builder runtime closure changed during build")
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
    builder_snapshot.unlink()
    for path in (tool_snapshot, *tool_snapshot.rglob("*")):
        if path.is_dir():
            os.chmod(path, 0o755)
        elif path.is_file():
            os.chmod(path, 0o644)
    shutil.rmtree(tool_snapshot)
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
