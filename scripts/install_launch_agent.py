#!/usr/bin/env python3
"""Atomically generate TrustForge LaunchAgent plists without text templating."""
from __future__ import annotations

import argparse
import os
import plistlib
import tempfile
from pathlib import Path


def canonical_existing(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    resolved = path.resolve(strict=True)
    if absolute != resolved:
        raise ValueError(f"path is not canonical: {path}")
    return resolved


def install_plist(destination: Path, payload: dict) -> None:
    if destination.parent.exists():
        absolute_parent = Path(os.path.abspath(destination.parent))
        if destination.parent.is_symlink() or absolute_parent != destination.parent.resolve(strict=True):
            raise ValueError("plist destination parent is not canonical")
    else:
        ancestor = destination.parent.parent
        if ancestor.is_symlink() or Path(os.path.abspath(ancestor)) != ancestor.resolve(strict=True):
            raise ValueError("plist destination ancestor is not canonical")
        destination.parent.mkdir(mode=0o700)
    absolute_parent = Path(os.path.abspath(destination.parent))
    if destination.parent.is_symlink() or destination.is_symlink() or absolute_parent != destination.parent.resolve(strict=True):
        raise ValueError("plist destination or parent is a symlink")
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            plistlib.dump(payload, handle, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_logs(payload: dict) -> None:
    for key in ("StandardOutPath", "StandardErrorPath"):
        path = Path(payload[key])
        if path.is_symlink():
            raise ValueError("launch log path is a symlink")
        _secure_directory(path.parent)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)


def _secure_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("launch log directory is a symlink")
    if path.exists():
        if Path(os.path.abspath(path)) != path.resolve(strict=True):
            raise ValueError("launch log directory is not canonical")
    else:
        _secure_directory(path.parent)
        path.mkdir(mode=0o700)
    os.chmod(path, 0o700)


def payload(kind: str, root: Path, python: Path, codex: Path | None, gh: Path | None = None) -> dict:
    root = canonical_existing(root)
    python = canonical_existing(python)
    out = root / "out" / "ceo-cycle"
    if kind == "sweep":
        if codex is None or gh is None:
            raise ValueError("codex and gh paths are required for sweep")
        codex = canonical_existing(codex)
        gh = canonical_existing(gh)
        return {
            "Label": "com.hurricanesoft.trustforge-ceo-sweep",
            "ProgramArguments": ["/bin/zsh", str(root / "scripts/run_ceo_cycle.sh")],
            "EnvironmentVariables": {
                "TRUSTFORGE_HOME": str(root), "TRUSTFORGE_PYTHON": str(python),
                "TRUSTFORGE_CODEX": str(codex), "TRUSTFORGE_GH": str(gh),
                "PATH": f"{codex.parent}:{gh.parent}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            },
            "StartInterval": 1800, "RunAtLoad": False, "Umask": 0o77, "WorkingDirectory": str(root),
            "StandardOutPath": str(out / "launchd.out.log"), "StandardErrorPath": str(out / "launchd.err.log"),
        }
    return {
        "Label": "com.hurricanesoft.trustforge-ceo-health-watchdog",
        "ProgramArguments": [str(python), str(root / "scripts/ceo_health_watchdog.py"), "--status", str(out / "status.json"), "--alert", str(out / "health-alert.json")],
        "StartInterval": 300, "RunAtLoad": True, "Umask": 0o77, "WorkingDirectory": str(root),
        "StandardOutPath": str(out / "health-watchdog.out.log"), "StandardErrorPath": str(out / "health-watchdog.err.log"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("sweep", "watchdog"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--codex", type=Path)
    parser.add_argument("--gh", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        agent_payload = payload(args.kind, args.root, args.python, args.codex, args.gh)
        prepare_logs(agent_payload)
        install_plist(args.destination, agent_payload)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
