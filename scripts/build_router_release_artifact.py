#!/usr/bin/env python3
"""Build a deterministic, provenance-complete release-router runtime bundle."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record_hash(path: Path) -> str:
    return "sha256=" + base64.urlsafe_b64encode(
        bytes.fromhex(digest(path))
    ).decode().rstrip("=")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    interpreter = parser.add_mutually_exclusive_group(required=True)
    interpreter.add_argument(
        "--python",
        type=Path,
        help="exact Python interpreter whose runtime and packages are bundled",
    )
    interpreter.add_argument(
        "--venv",
        type=Path,
        help="virtualenv root (legacy shorthand for <venv>/bin/python)",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir
    stage = output / ".router-runtime-stage"
    if stage.exists():
        raise SystemExit("router runtime staging path already exists")
    stage.mkdir(parents=True, mode=0o700)
    try:
        python = args.python if args.python is not None else args.venv / "bin/python"
        if not python.is_file():
            raise SystemExit(f"python interpreter does not exist: {python}")
        version = subprocess.check_output(
            [
                str(python),
                "-I",
                "-c",
                "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')",
            ],
            text=True,
        ).strip()
        site = stage / f".venv/lib/python{version}/site-packages"
        site.mkdir(parents=True)
        (stage / ".venv/bin").mkdir(parents=True)
        (stage / "scripts").mkdir()
        resolved_python = python.resolve()
        shutil.copy2(resolved_python, stage / ".venv/bin/python")
        for runtime_library in (resolved_python.parent.parent / "lib").glob(
            "libpython*"
        ):
            if runtime_library.is_file():
                shutil.copy2(
                    runtime_library, stage / ".venv/lib" / runtime_library.name
                )
        (stage / ".venv/pyvenv.cfg").write_text(
            f"home = {resolved_python.parent}\n"
            f"version = {version}\n"
            "include-system-site-packages = false\n"
        )
        shutil.copy2(
            args.source_root / "scripts/release_router_service.py",
            stage / "scripts/release_router_service.py",
        )
        source_site = Path(
            subprocess.check_output(
                [
                    str(python),
                    "-I",
                    "-c",
                    "import sysconfig;print(sysconfig.get_path('purelib'))",
                ],
                text=True,
            ).strip()
        )
        distributions: dict[str, dict[str, str]] = {}
        package_metadata = (args.source_root / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        package_match = re.search(
            r'(?m)^version = "([^"]+)"$', package_metadata
        )
        if not package_match:
            raise SystemExit("cannot resolve trustforge package version")
        package_version = package_match.group(1)
        for name in ("trustforge", "cryptography", "cffi"):
            candidates = sorted(
                source_site.glob(name.replace("-", "_") + "-*.dist-info")
            )
            if name == "trustforge":
                shutil.copytree(
                    args.source_root / "src/trustforge", site / "trustforge"
                )
                shutil.copytree(
                    args.source_root / "src/trustforge_core", site / "trustforge_core"
                )
                dist = site / f"trustforge-{package_version}.dist-info"
                dist.mkdir()
                (dist / "METADATA").write_text(
                    "Metadata-Version: 2.1\nName: trustforge\n"
                    f"Version: {package_version}\n"
                )
            else:
                if len(candidates) != 1:
                    raise SystemExit(f"cannot resolve distribution: {name}")
                package_root = source_site / name.replace("-", "_")
                shutil.copytree(package_root, site / package_root.name)
                if name == "cffi":
                    backends = sorted(source_site.glob("_cffi_backend*"))
                    if len(backends) != 1:
                        raise SystemExit("cannot resolve cffi native backend")
                    shutil.copy2(backends[0], site / backends[0].name)
                dist = site / candidates[0].name
                shutil.copytree(candidates[0], dist)
            record = dist / "RECORD"
            rows = []
            roots = [site / name.replace("-", "_"), dist]
            if name == "trustforge":
                roots.append(site / "trustforge_core")
            if name == "cffi":
                roots.extend(site.glob("_cffi_backend*"))
            for root in roots:
                paths = [root] if root.is_file() else sorted(root.rglob("*"))
                for path in paths:
                    if (
                        path.is_file()
                        and path != record
                        and "__pycache__" not in path.parts
                    ):
                        relative = path.relative_to(site).as_posix()
                        rows.append(
                            (relative, record_hash(path), str(path.stat().st_size))
                        )
            rows.append((record.relative_to(site).as_posix(), "", ""))
            with record.open("w", newline="") as stream:
                csv.writer(stream, lineterminator="\n").writerows(rows)
            metadata = dist / "METADATA"
            version_value = next(
                line[9:]
                for line in metadata.read_text().splitlines()
                if line.startswith("Version: ")
            )
            distributions[name] = {
                "version": version_value,
                "dist_info": dist.name,
                "metadata_sha256": digest(metadata),
                "record_sha256": digest(record),
            }
        # Remove interpreter caches and make the complete tree immutable in the manifest.
        for cache in stage.rglob("__pycache__"):
            shutil.rmtree(cache)
        entries = []
        for directory in sorted(
            (p for p in stage.rglob("*") if p.is_dir()), key=lambda p: p.as_posix()
        ):
            entries.append(
                {
                    "path": directory.relative_to(stage).as_posix(),
                    "type": "directory",
                    "mode": "0555",
                }
            )
        for path in sorted(
            (p for p in stage.rglob("*") if p.is_file()), key=lambda p: p.as_posix()
        ):
            mode = "0555" if path == stage / ".venv/bin/python" else "0444"
            entries.append(
                {
                    "path": path.relative_to(stage).as_posix(),
                    "type": "file",
                    "mode": mode,
                    "sha256": digest(path),
                }
            )
        manifest = {"schema": "trustforge.router-tree-manifest/v1", "entries": entries}
        output.mkdir(parents=True, exist_ok=True)
        manifest_path = output / "router-tree-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
        )
        lock = {
            "schema": "trustforge.router-runtime-lock/v2",
            "tree_manifest_sha256": digest(manifest_path),
            "distributions": distributions,
        }
        (output / "runtime-lock.json").write_text(
            json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n"
        )
        with tarfile.open(
            output / "router-runtime.tar", "w", format=tarfile.PAX_FORMAT
        ) as archive:
            for entry in entries:
                path = stage / entry["path"]
                info = archive.gettarinfo(str(path), arcname=entry["path"])
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                info.mode = int(entry["mode"], 8)
                if entry["type"] == "directory":
                    archive.addfile(info)
                else:
                    with path.open("rb") as stream:
                        archive.addfile(info, stream)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
