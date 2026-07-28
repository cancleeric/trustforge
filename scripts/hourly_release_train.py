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
import hashlib
import secrets
import re
import io
import tarfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "release-train"
PRODUCTION_ACCOUNT = "795930814369"
PRODUCTION_REGION = "ap-southeast-2"
PRODUCTION_URL = "https://trustforge.hurricanesoft.com.tw"
VERSION_PATTERN = re.compile(r"(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)\Z")


def run(command: list[str], *, cwd: Path = ROOT, capture: bool = False) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, check=True, capture_output=capture)
    return result.stdout if capture else ""


def process_birth(pid: int) -> str:
    if os.environ.get("TRUSTFORGE_GATE_SANDBOX") == "1":
        return f"sandbox:{pid}"
    return run(["ps", "-o", "lstart=", "-p", str(pid)], capture=True).strip()


def record(receipt: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = OUT / f"{receipt['run_id']}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    status = OUT / "last-status.json"
    shutil.copyfile(path, status)
    os.chmod(status, 0o600)
    history = sorted(
        (item for item in OUT.glob("20*.json") if not item.name.endswith("-backup.json")),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale in history[100:]:
        stale.unlink()
    return path


@contextmanager
def lease() -> Iterable[None]:
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = OUT / "lease"
    token = secrets.token_hex(16)
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        try:
            owner_data = json.loads((lock / "owner.json").read_text(encoding="utf-8"))
            owner = int(owner_data["pid"])
            os.kill(owner, 0)
            birth = process_birth(owner)
            if birth != owner_data.get("birth"):
                raise ProcessLookupError
        except (FileNotFoundError, ProcessLookupError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            shutil.rmtree(lock, ignore_errors=True)
            try:
                lock.mkdir(mode=0o700)
            except FileExistsError as race:
                raise RuntimeError("another release train owns the lease") from race
        except PermissionError as denied:
            raise RuntimeError("cannot verify the existing release-train lease owner") from denied
        else:
            raise RuntimeError("another release train owns the lease") from exc
    try:
        birth = process_birth(os.getpid())
        (lock / "owner.json").write_text(
            json.dumps({"pid": os.getpid(), "birth": birth, "token": token}) + "\n",
            encoding="utf-8",
        )
        yield
    finally:
        try:
            owner_data = json.loads((lock / "owner.json").read_text(encoding="utf-8"))
            if owner_data.get("token") == token:
                shutil.rmtree(lock)
        except FileNotFoundError:
            pass


def require_clean_root() -> None:
    if run(["git", "status", "--porcelain"], capture=True).strip():
        raise RuntimeError("repository working tree is dirty")


def bump_patch_version(worktree: Path) -> str:
    """Synchronize backend and frontend package metadata to the next patch."""
    pyproject = worktree / "pyproject.toml"
    match = re.search(
        r'(?m)^version = "(?P<version>[^"]+)"$',
        pyproject.read_text(encoding="utf-8"),
    )
    if not match or not VERSION_PATTERN.fullmatch(match.group("version")):
        raise RuntimeError("pyproject release version is not strict SemVer")
    current = match.group("version")
    parsed = VERSION_PATTERN.fullmatch(current)
    assert parsed is not None
    next_version = (
        f"{parsed.group('major')}.{parsed.group('minor')}."
        f"{int(parsed.group('patch')) + 1}"
    )
    replacements = {
        "pyproject.toml": (f'version = "{current}"', f'version = "{next_version}"', 1),
        "src/trustforge/__init__.py": (
            f'__version__ = "{current}"',
            f'__version__ = "{next_version}"',
            1,
        ),
        "src/trustforge/_version.py": (
            f'VERSION = "{current}"',
            f'VERSION = "{next_version}"',
            1,
        ),
        "frontend/package.json": (
            f'"version": "{current}"',
            f'"version": "{next_version}"',
            1,
        ),
        "frontend/package-lock.json": (
            f'"version": "{current}"',
            f'"version": "{next_version}"',
            2,
        ),
    }
    for relative, (old, new, expected) in replacements.items():
        target = worktree / relative
        body = target.read_text(encoding="utf-8")
        if body.count(old) != expected:
            raise RuntimeError(f"{relative} is not synchronized to {current}")
        target.write_text(body.replace(old, new), encoding="utf-8")
    return next_version


def gate(worktree: Path) -> None:
    trusted_venv = ROOT / ".venv"
    trusted_modules = ROOT / "frontend" / "node_modules"
    if not trusted_venv.is_dir() or not trusted_modules.is_dir():
        raise RuntimeError("trusted gate dependencies are not installed")
    trusted_hook = run(["git", "show", "origin/main:.githooks/pre-push"], capture=True)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="trustforge-gate-") as temporary:
        sandbox_root = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            bundle.extractall(sandbox_root, filter="data")
        subprocess.run(["/bin/cp", "-cR", str(trusted_venv), str(sandbox_root / ".venv")], check=True)
        subprocess.run(
            ["/bin/cp", "-cR", str(trusted_modules), str(sandbox_root / "frontend" / "node_modules")],
            check=True,
        )
        git_env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        subprocess.run(["git", "init", "-q"], cwd=sandbox_root, env=git_env, check=True)
        subprocess.run(["git", "add", "-A"], cwd=sandbox_root, env=git_env, check=True)
        subprocess.run(
            ["git", "-c", "user.name=TrustForge Gate", "-c", "user.email=gate@localhost", "commit", "-qm", "candidate"],
            cwd=sandbox_root,
            env=git_env,
            check=True,
        )
        hook = sandbox_root / ".git-trusted-pre-push"
        hook.write_text(trusted_hook, encoding="utf-8")
        hook.chmod(0o500)
        command = [str(hook)]
        sandbox = Path("/usr/bin/sandbox-exec")
        if sys.platform == "darwin":
            if not sandbox.is_file():
                raise RuntimeError("trusted gate sandbox is unavailable")
            home = Path.home()
            profile = (
                f'(version 1)(allow default)(deny file-read* (subpath "{home}"))'
                f'(deny file-write* (subpath "{home}"))'
                '(deny process-exec (literal "/usr/bin/security"))'
            )
            command = [str(sandbox), "-p", profile, str(hook)]
        env = dict(git_env)
        for key in tuple(env):
            if key.startswith(("AWS_", "GH_", "GITHUB_")):
                env.pop(key)
        env["TRUSTFORGE_GATE_SANDBOX"] = "1"
        env["HOME"] = str(sandbox_root)
        subprocess.run(command, cwd=sandbox_root, env=env, check=True)


def production_identity() -> tuple[str, str]:
    account = run(["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"], capture=True).strip()
    if account != PRODUCTION_ACCOUNT:
        raise RuntimeError("AWS caller is not the pinned TrustForge production account")
    bucket = f"trustforge-deploy-{PRODUCTION_ACCOUNT}"
    pointer = json.loads(run(["aws", "s3", "cp", f"s3://{bucket}/pointers/active.json", "-", "--region", PRODUCTION_REGION], capture=True))
    digest = str(pointer["digest"])
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise RuntimeError("production active digest is invalid")
    manifest = json.loads(run(["aws", "s3", "cp", f"s3://{bucket}/artifacts/{digest}/manifest.json", "-", "--region", PRODUCTION_REGION], capture=True))
    sha = str(manifest["git_sha"])
    if sha == "unknown":
        legacy = re.search(r"-g([0-9a-f]{7,40})(?:$|-)", str(pointer.get("version", "")))
        if not legacy:
            raise RuntimeError("legacy production pointer does not contain a git SHA")
        sha = run(["git", "rev-parse", legacy.group(1)], capture=True).strip()
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise RuntimeError("production manifest git SHA is invalid")
    return sha, digest


def verify_runtime_identity(expected_digest: str) -> None:
    health = run(["curl", "-fsS", "--max-time", "15", f"{PRODUCTION_URL}/healthz"], capture=True).strip()
    if health != "ok":
        raise RuntimeError("production health endpoint did not return ok")
    payload = json.loads(
        run(
            ["curl", "-fsS", "--max-time", "15", f"{PRODUCTION_URL}/api/.well-known/trustforge-release-manifest"],
            capture=True,
        )
    )
    if payload.get("artifact_digest") != f"sha256:{expected_digest}":
        raise RuntimeError("production serving endpoint artifact digest mismatch")


def require_backup_receipt(command: str, run_id: str) -> Path:
    receipt = OUT / f"{run_id}-backup.json"
    if receipt.exists():
        raise RuntimeError("backup receipt already exists")
    env = dict(os.environ, TRUSTFORGE_BACKUP_RECEIPT=str(receipt), TRUSTFORGE_RELEASE_RUN_ID=run_id)
    subprocess.run(["/bin/zsh", "-lc", command], cwd=ROOT, env=env, check=True)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    archive = Path(payload.get("archive", ""))
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest() if archive.is_file() else ""
    if (
        payload.get("schema") != "trustforge.production-backup/v1"
        or payload.get("run_id") != run_id
        or payload.get("restore_verified") is not True
        or payload.get("archive_sha256") != archive_hash
    ):
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
            main_sha_remote = run(["git", "rev-parse", "origin/main"], capture=True).strip()
            production_sha, production_digest = production_identity()
            receipt["production_before"] = {"git_sha": production_sha, "artifact_digest": production_digest}
            runtime_in_sync = False
            try:
                verify_runtime_identity(production_digest)
                runtime_in_sync = True
            except Exception as drift:
                receipt["runtime_drift"] = str(drift)
            if args.dry_run:
                receipt["status"] = "dry-run"
                receipt["finished_at"] = datetime.now(UTC).isoformat()
                return record(receipt)
            if develop_only == 0 and production_sha == main_sha_remote and runtime_in_sync:
                receipt["status"] = "no-op"
                receipt["finished_at"] = datetime.now(UTC).isoformat()
                return record(receipt)
            backup_command = "bash deploy/backup_production_release.sh"
            deploy_command = "TRUSTFORGE_BOOTSTRAP=0 bash deploy/deploy_ec2.sh"
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
                    if develop_only:
                        run(["git", "merge", "--no-edit", "--no-ff", develop_sha], cwd=main_tree)
                        release_version = bump_patch_version(main_tree)
                        run(
                            [
                                "git", "add",
                                "pyproject.toml",
                                "src/trustforge/__init__.py",
                                "src/trustforge/_version.py",
                                "frontend/package.json",
                                "frontend/package-lock.json",
                            ],
                            cwd=main_tree,
                        )
                        run(
                            ["git", "commit", "-m", f"release: bump version to {release_version}"],
                            cwd=main_tree,
                        )
                        receipt["steps"].append({"release_version": release_version})
                    gate(main_tree)
                    main_sha = run(["git", "rev-parse", "HEAD"], cwd=main_tree, capture=True).strip()
                    release_branch = f"release/auto-{run_id[:8]}"
                    backup = require_backup_receipt(backup_command, run_id)
                    receipt["steps"].append({"backup_receipt": str(backup)})
                    if develop_only:
                        run(
                            [
                                # The exact develop and merged-main candidates already passed
                                # gate() above. Avoid a third, unisolated hook invocation here.
                                "git", "-c", "core.hooksPath=/dev/null",
                                "push", "--atomic", "origin",
                                f"{main_sha}:refs/heads/main",
                                f"{main_sha}:refs/heads/{release_branch}",
                            ],
                            cwd=main_tree,
                        )
                        receipt["steps"].append({"main": main_sha, "release_branch": release_branch})
                    else:
                        receipt["steps"].append({"main": main_sha, "release_branch": "existing-main-retry"})
                    env = dict(os.environ, TRUSTFORGE_RELEASE_SHA=main_sha, TRUSTFORGE_RELEASE_BRANCH=release_branch)
                    subprocess.run(["/bin/zsh", "-lc", deploy_command], cwd=main_tree, env=env, check=True)
                    deployed_sha, deployed_digest = production_identity()
                    if deployed_sha != main_sha:
                        raise RuntimeError("production active SHA does not match the verified main SHA")
                    verify_runtime_identity(deployed_digest)
                    receipt["steps"].append({"production_deploy": "passed", "git_sha": deployed_sha, "artifact_digest": deployed_digest})
                finally:
                    cleanup = []
                    for tree in (main_tree, develop_tree):
                        result = subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=ROOT)
                        cleanup.append({"path": str(tree), "returncode": result.returncode})
                    prune = subprocess.run(["git", "worktree", "prune"], cwd=ROOT)
                    receipt["cleanup"] = cleanup + [{"worktree_prune": prune.returncode}]
                    if any(item.get("returncode", item.get("worktree_prune", 0)) for item in receipt["cleanup"]):
                        raise RuntimeError("release worktree cleanup failed")
            receipt["status"] = "completed"
    except Exception as exc:
        receipt["status"] = "failed"
        receipt["error"] = str(exc)
        receipt["finished_at"] = datetime.now(UTC).isoformat()
        record(receipt)
        if sys.platform == "darwin":
            subprocess.run(
                ["/usr/bin/osascript", "-e", 'display notification "TrustForge release train failed; inspect last-status.json" with title "TrustForge production"'],
                check=False,
            )
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
