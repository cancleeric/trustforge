from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"


def test_actual_router_runtime_is_provenance_complete_and_importable(tmp_path):
    output = tmp_path / "build"
    subprocess.run(
        [
            str(PYTHON),
            str(ROOT / "scripts/build_router_release_artifact.py"),
            "--source-root",
            str(ROOT),
            "--venv",
            str(ROOT / ".venv"),
            "--output-dir",
            str(output),
        ],
        check=True,
    )
    releases = tmp_path / "releases"
    install_command = [
        str(PYTHON),
        str(ROOT / "scripts/install_router_release_artifact.py"),
        "--archive",
        str(output / "router-runtime.tar"),
        "--tree-manifest",
        str(output / "router-tree-manifest.json"),
        "--runtime-lock",
        str(output / "runtime-lock.json"),
        "--releases-root",
        str(releases),
    ]
    runtime_lock_path = output / "runtime-lock.json"
    valid_runtime_lock = runtime_lock_path.read_text()
    invalid_runtime_lock = json.loads(valid_runtime_lock)
    invalid_runtime_lock["distributions"]["trustforge"]["metadata_sha256"] = "0" * 64
    runtime_lock_path.write_text(
        json.dumps(invalid_runtime_lock, sort_keys=True, separators=(",", ":")) + "\n"
    )
    rejected = subprocess.run(install_command, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert "runtime distribution metadata mismatch" in rejected.stderr
    assert not any(releases.iterdir())

    runtime_lock_path.write_text(valid_runtime_lock)
    installed = subprocess.run(
        install_command,
        check=True,
        capture_output=True,
        text=True,
    )
    release = Path(installed.stdout.strip())
    imported = subprocess.run(
        [
            str(release / ".venv/bin/python"),
            "-I",
            "-c",
            (
                "import trustforge.release_router;"
                "import trustforge.release_router_runtime;"
                "import cryptography;print('imports-ok')"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert imported.stdout == "imports-ok\n"

    manifest = json.loads((output / "router-tree-manifest.json").read_text())
    directory_paths = {
        entry["path"] for entry in manifest["entries"] if entry["type"] == "directory"
    }
    assert any(path.endswith("/site-packages") for path in directory_paths)
    assert all(
        entry["mode"] == "0555"
        for entry in manifest["entries"]
        if entry["type"] == "directory"
    )

    site_packages = next(release.glob(".venv/lib/python*/site-packages"))
    site_packages.chmod(0o755)
    extra = site_packages / "unclaimed.py"
    extra.write_text("bad = True\n")
    site_packages.chmod(0o555)
    repeated = subprocess.run(
        [
            str(PYTHON),
            str(ROOT / "scripts/install_router_release_artifact.py"),
            "--archive",
            str(output / "router-runtime.tar"),
            "--tree-manifest",
            str(output / "router-tree-manifest.json"),
            "--runtime-lock",
            str(output / "runtime-lock.json"),
            "--releases-root",
            str(releases),
        ],
        capture_output=True,
        text=True,
    )
    assert repeated.returncode != 0
    assert "unlisted file" in repeated.stderr
