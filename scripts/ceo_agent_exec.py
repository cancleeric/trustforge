#!/usr/bin/env python3
"""Run one Codex lane with a bounded lifetime and minimal environment."""
from __future__ import annotations

import argparse
import os
import signal
import shutil
import subprocess
from pathlib import Path


def isolated_environment(*, path: str, codex_home: str | None, lane: str, issue: str, home: Path) -> dict[str, str]:
    environment = {
        "PATH": path,
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "TRUSTFORGE_CEO_LANE": lane,
        "TRUSTFORGE_CEO_ISSUE": issue,
    }
    if codex_home:
        environment["CODEX_HOME"] = codex_home
    return environment


def prepare_minimal_codex_home(source: Path | None, home: Path) -> Path | None:
    if source is None:
        return None
    auth = source / "auth.json"
    if not auth.is_file() or auth.is_symlink():
        return None
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = home / ".codex"
    destination.mkdir(mode=0o700)
    copied_auth = destination / "auth.json"
    shutil.copyfile(auth, copied_auth)
    os.chmod(copied_auth, 0o600)
    return destination


def remove_minimal_codex_home(path: Path | None) -> None:
    if path is None:
        return
    auth = path / "auth.json"
    if auth.exists() and not auth.is_symlink():
        auth.unlink()
    path.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument("--lane", required=True)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    args.home.mkdir(mode=0o700, parents=True, exist_ok=True)
    minimal_codex_home = prepare_minimal_codex_home(
        Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else None,
        args.home,
    )
    environment = isolated_environment(
        path=os.environ.get("PATH", "/usr/bin:/bin"),
        codex_home=str(minimal_codex_home) if minimal_codex_home else None,
        lane=args.lane,
        issue=args.issue,
        home=args.home,
    )
    command = [
        args.codex, "exec", "--ephemeral", "--ignore-user-config",
        "-c", 'approval_policy="never"',
        "-c", "sandbox_workspace_write.network_access=false",
        "--sandbox", "workspace-write", "-C", str(args.cwd), "-o", str(args.output), "-",
    ]
    try:
        with args.prompt.open("rb") as prompt:
            process = subprocess.Popen(command, stdin=prompt, env=environment, start_new_session=True)
            try:
                return process.wait(timeout=args.timeout_seconds)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                return 124
    finally:
        remove_minimal_codex_home(minimal_codex_home)


if __name__ == "__main__":
    raise SystemExit(main())
