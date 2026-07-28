#!/usr/bin/env python3
"""Fail-closed hourly TrustForge release train."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "release-train"
def run(command: list[str], *, cwd: Path = ROOT, capture: bool = False) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, check=True, capture_output=capture)
    return result.stdout if capture else ""


def record(receipt: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = OUT / f"{receipt['run_id']}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return path


@contextmanager
def lease() -> Iterable[None]:
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = OUT / "lease"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        try:
            owner = int((lock / "pid").read_text(encoding="ascii").strip())
            os.kill(owner, 0)
        except (FileNotFoundError, ProcessLookupError, ValueError):
            shutil.rmtree(lock)
            try:
                lock.mkdir(mode=0o700)
            except FileExistsError as race:
                raise RuntimeError("another release train owns the lease") from race
        except PermissionError as denied:
            raise RuntimeError("cannot verify the existing release-train lease owner") from denied
        else:
            raise RuntimeError("another release train owns the lease") from exc
    try:
        (lock / "pid").write_text(f"{os.getpid()}\n", encoding="ascii")
        yield
    finally:
        shutil.rmtree(lock)


def require_clean_root() -> None:
    if run(["git", "status", "--porcelain"], capture=True).strip():
        raise RuntimeError("repository working tree is dirty")


def gate(worktree: Path) -> None:
    run([str(worktree / ".githooks" / "pre-push")], cwd=worktree)


def require_backup_receipt(command: str, run_id: str) -> Path:
    receipt = OUT / f"{run_id}-backup.json"
    env = dict(os.environ, TRUSTFORGE_BACKUP_RECEIPT=str(receipt))
    subprocess.run(["/bin/zsh", "-lc", command], cwd=ROOT, env=env, check=True)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    archive = Path(payload.get("archive", ""))
    if payload.get("restore_verified") is not True or not archive.is_file():
        raise RuntimeError("backup receipt lacks a verified restorable archive")
    return receipt


def execute(args: argparse.Namespace) -> Path:
    started = datetime.now(UTC)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    receipt = {"run_id": run_id, "started_at": started.isoformat(), "status": "running", "steps": []}
    try:
        with lease():
            require_clean_root()
            run(["git", "fetch", "--prune", "origin"])
            counts = run(
                ["git", "rev-list", "--left-right", "--count", "origin/main...origin/develop"],
                capture=True,
            ).strip().split()
            main_only, develop_only = (int(value) for value in counts)
            receipt["divergence"] = {"main_only": main_only, "develop_only": develop_only}
            if args.dry_run:
                receipt["status"] = "dry-run"
                receipt["finished_at"] = datetime.now(UTC).isoformat()
                return record(receipt)
            if develop_only == 0:
                receipt["status"] = "no-op"
                receipt["finished_at"] = datetime.now(UTC).isoformat()
                return record(receipt)
            backup_command = os.environ.get("TRUSTFORGE_RELEASE_BACKUP_CMD", "")
            deploy_command = os.environ.get("TRUSTFORGE_RELEASE_DEPLOY_CMD", "")
            if not backup_command or not deploy_command:
                raise RuntimeError("production backup and deploy commands must both be configured")
            with tempfile.TemporaryDirectory(prefix="trustforge-release-train-") as temporary:
                base = Path(temporary)
                develop_tree = base / "develop"
                main_tree = base / "main"
                run(["git", "worktree", "add", "--detach", str(develop_tree), "origin/develop"])
                try:
                    gate(develop_tree)
                    develop_sha = run(["git", "rev-parse", "HEAD"], cwd=develop_tree, capture=True).strip()
                    receipt["steps"].append({"develop": develop_sha})
                    run(["git", "worktree", "add", "--detach", str(main_tree), "origin/main"])
                    run(["git", "merge", "--no-edit", "--no-ff", develop_sha], cwd=main_tree)
                    gate(main_tree)
                    main_sha = run(["git", "rev-parse", "HEAD"], cwd=main_tree, capture=True).strip()
                    release_branch = f"release/auto-{run_id.lower()}-{main_sha[:8]}"
                    backup = require_backup_receipt(backup_command, run_id)
                    receipt["steps"].append({"backup_receipt": str(backup)})
                    run(
                        ["git", "push", "--atomic", "origin", f"{main_sha}:main", f"{main_sha}:{release_branch}"],
                        cwd=main_tree,
                    )
                    receipt["steps"].append({"main": main_sha, "release_branch": release_branch})
                    env = dict(os.environ, TRUSTFORGE_RELEASE_SHA=main_sha, TRUSTFORGE_RELEASE_BRANCH=release_branch)
                    subprocess.run(["/bin/zsh", "-lc", deploy_command], cwd=main_tree, env=env, check=True)
                    receipt["steps"].append({"production_deploy": "passed"})
                finally:
                    subprocess.run(["git", "worktree", "remove", "--force", str(main_tree)], cwd=ROOT)
                    subprocess.run(["git", "worktree", "remove", "--force", str(develop_tree)], cwd=ROOT)
            receipt["status"] = "completed"
    except Exception as exc:
        receipt["status"] = "failed"
        receipt["error"] = str(exc)
        receipt["finished_at"] = datetime.now(UTC).isoformat()
        record(receipt)
        raise
    receipt["finished_at"] = datetime.now(UTC).isoformat()
    return record(receipt)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="allow pushes and production deployment")
    args = parser.parse_args(argv)
    args.dry_run = not args.execute
    try:
        path = execute(args)
    except Exception as exc:
        print(f"release train failed: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
