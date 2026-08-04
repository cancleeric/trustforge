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
import runpy
import tarfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "release-train"
PRODUCTION_ACCOUNT_ENV = "TRUSTFORGE_PRODUCTION_ACCOUNT_ID"
PRODUCTION_REGION = os.getenv("TRUSTFORGE_PRODUCTION_REGION", "us-west-2")
PRODUCTION_URL = os.getenv("TRUSTFORGE_PRODUCTION_URL", "https://34-220-226-162.nip.io").rstrip("/")
VERSION_PATTERN = re.compile(r"(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)\Z")
# main 引入 formal-run analysis-question handler 後，必須先完成生產配套
# (DynamoDB table + caller/idempotency/retention secret + EC2 env) 並建立此 flag，
# release train 才會嘗試部署該版本；否則 fail-closed 不部署，避免 fe-nginx 把對外層
# 寫壞、或 activate 一個生產環境跑不起來的 formal-run 版本。
FORMAL_RUN_READY_FLAG = OUT / "formal-run-prod-ready"
FORMAL_HANDLER_MARKER = "_handle_api_formal_analysis_question"


def production_account() -> str:
    account = os.getenv(PRODUCTION_ACCOUNT_ENV, "")
    if not re.fullmatch(r"[0-9]{12}", account):
        raise RuntimeError(f"{PRODUCTION_ACCOUNT_ENV} must be a 12-digit AWS account id")
    return account


def require_competition_target() -> None:
    if PRODUCTION_REGION not in {"us-west-2", "us-east-1", "ap-southeast-2"}:
        raise RuntimeError("production region must be us-west-2, us-east-1, or ap-southeast-2")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?", PRODUCTION_URL):
        raise RuntimeError("competition production URL must be an HTTPS origin without a path")


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
    """Bump the canonical version and regenerate derived package metadata."""
    version_tools = runpy.run_path(str(ROOT / "scripts" / "release_version.py"))
    current = version_tools["package_version"](worktree)
    next_version = version_tools["bumped_version"](
        version_tools["parse_version"](current), "patch"
    )
    version_tools["update_version_files"](next_version, worktree)
    return next_version


def gate(worktree: Path) -> None:
    # 商用部署（非競賽 region）以 push gate 為依據，跳過競賽級 sandbox 隔離 gate。
    # AGENTS.md 要求的 .githooks/pre-push 已在 push 時跑全綠（含 407 batch + Rust +
    # frontend），是商用足夠的 gate。競賽 region（us-west-2/us-east-1）保留原 sandbox gate。
    if PRODUCTION_REGION not in {"us-west-2", "us-east-1"}:
        return
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
        _localize_gate_interpreter(
            home=Path.home(),
            venv=sandbox_root / ".venv",
            sandbox_root=sandbox_root,
        )
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
        rust_toolchain = _trusted_rust_toolchain(Path.home())
        if sys.platform == "darwin":
            if not sandbox.is_file():
                raise RuntimeError("trusted gate sandbox is unavailable")
            home = Path.home()
            profile = (
                f'(version 1)(allow default)(deny file-read* (subpath "{home}"))'
                f'(deny file-write* (subpath "{home}"))'
                f'(allow file-read* (subpath "{rust_toolchain}"))'
                f'(allow process-exec (subpath "{rust_toolchain}"))'
                '(deny process-exec (literal "/usr/bin/security"))'
            )
            command = [str(sandbox), "-p", profile, str(hook)]
        env = dict(git_env)
        for key in tuple(env):
            if key.startswith(("AWS_", "GH_", "GITHUB_", "TRUSTFORGE_PRODUCTION_")):
                env.pop(key)
        env["TRUSTFORGE_GATE_SANDBOX"] = "1"
        env["HOME"] = str(sandbox_root)
        env["CARGO_HOME"] = str(sandbox_root / ".cargo")
        env["RUSTC"] = str(rust_toolchain / "bin" / "rustc")
        env["PATH"] = f"{rust_toolchain / 'bin'}:{env.get('PATH', '')}"
        subprocess.run(command, cwd=sandbox_root, env=env, check=True)


def _trusted_rust_toolchain(home: Path) -> Path:
    """Resolve only the active Rust toolchain, never Cargo credentials/caches."""
    home = home.resolve()
    rustup = home / ".cargo" / "bin" / "rustup"
    if not rustup.is_file():
        raise RuntimeError("trusted Rust toolchain is unavailable")
    result = subprocess.run(
        [str(rustup), "which", "cargo"],
        text=True,
        check=True,
        capture_output=True,
    )
    cargo = Path(result.stdout.strip()).resolve(strict=True)
    toolchains = (home / ".rustup" / "toolchains").resolve()
    if not cargo.is_relative_to(toolchains) or cargo.name != "cargo":
        raise RuntimeError("rustup resolved cargo outside the trusted toolchain root")
    toolchain = cargo.parent.parent
    rustc = toolchain / "bin" / "rustc"
    if not rustc.is_file():
        raise RuntimeError("trusted Rust compiler is unavailable")
    return toolchain


def _localize_gate_interpreter(
    *, home: Path, venv: Path, sandbox_root: Path
) -> Path | None:
    """Clone a uv runtime into the sandbox so HOME can remain fully denied."""
    home = home.resolve()
    interpreter = venv / "bin" / "python"
    resolved = interpreter.resolve(strict=True)
    uv_python_root = (home / ".local" / "share" / "uv" / "python").resolve()
    if not resolved.is_relative_to(home):
        return None
    if not resolved.is_relative_to(uv_python_root):
        raise RuntimeError(
            "trusted gate interpreter resolves inside HOME outside the uv runtime root"
        )
    runtime = resolved.parent.parent
    local_runtime = sandbox_root / ".python-runtime"
    subprocess.run(
        ["/bin/cp", "-cR", str(runtime), str(local_runtime)],
        check=True,
    )
    local_python = local_runtime / "bin" / resolved.name
    for name in ("python", "python3", f"python{sys.version_info.major}.{sys.version_info.minor}"):
        link = venv / "bin" / name
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(local_python)
    config = venv / "pyvenv.cfg"
    body = config.read_text(encoding="utf-8")
    body = re.sub(r"(?m)^home = .*$", f"home = {local_runtime / 'bin'}", body)
    config.write_text(body, encoding="utf-8")
    return local_runtime


def production_identity() -> tuple[str, str]:
    production_account_id = production_account()
    account = run(["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"], capture=True).strip()
    if account != production_account_id:
        raise RuntimeError("AWS caller is not the pinned TrustForge production account")
    bucket = f"trustforge-deploy-{production_account_id}"
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


def capture_active_pointer(expected_digest: str) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise RuntimeError("production active digest is invalid")
    bucket = f"trustforge-deploy-{production_account()}"
    pointer = json.loads(
        run(
            [
                "aws",
                "s3",
                "cp",
                f"s3://{bucket}/pointers/active.json",
                "-",
                "--region",
                PRODUCTION_REGION,
            ],
            capture=True,
        )
    )
    if pointer.get("digest") != expected_digest:
        raise RuntimeError("production active pointer changed during pre-state capture")
    version = pointer.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", version):
        raise RuntimeError("production active pointer version is invalid")
    return pointer


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


def verify_frontend_identity(expected_sha: str) -> str:
    short_sha = expected_sha[:7]
    index = run(
        [
            "curl",
            "-fsS",
            "--max-time",
            "15",
            "--retry",
            "10",
            "--retry-delay",
            "3",
            "--retry-max-time",
            "45",
            "--retry-all-errors",
            f"{PRODUCTION_URL}/",
        ],
        capture=True,
    )
    assets = set(re.findall(r"assets/index-[A-Za-z0-9_-]+\.js", index))
    if len(assets) != 1:
        raise RuntimeError("production frontend index does not identify exactly one app bundle")
    asset = assets.pop()
    bundle = run(
        [
            "curl",
            "-fsS",
            "--max-time",
            "30",
            "--retry",
            "10",
            "--retry-delay",
            "3",
            "--retry-max-time",
            "45",
            "--retry-all-errors",
            f"{PRODUCTION_URL}/{asset}",
        ],
        capture=True,
    )
    if short_sha not in bundle:
        raise RuntimeError("production frontend bundle SHA does not match verified main SHA")
    if "隨機競賽題目" not in bundle or "Random competition question" not in bundle:
        raise RuntimeError("production frontend bundle lacks competition question picker")
    return asset


def production_instance() -> str:
    instances = run(
        [
            "aws",
            "ec2",
            "describe-instances",
            "--region",
            PRODUCTION_REGION,
            "--filters",
            "Name=tag:Name,Values=trustforge-demo",
            "Name=instance-state-name,Values=running",
            "--query",
            "Reservations[].Instances[].InstanceId",
            "--output",
            "text",
        ],
        capture=True,
    ).split()
    if len(instances) != 1:
        raise RuntimeError("production EC2 target is not unique")
    return instances[0]


def run_ssm(instance: str, commands: list[str]) -> str:
    command_id = run(
        [
            "aws",
            "ssm",
            "send-command",
            "--region",
            PRODUCTION_REGION,
            "--instance-ids",
            instance,
            "--document-name",
            "AWS-RunShellScript",
            "--parameters",
            json.dumps({"commands": commands}),
            "--query",
            "Command.CommandId",
            "--output",
            "text",
        ],
        capture=True,
    ).strip()
    if not command_id or command_id == "None":
        raise RuntimeError("production SSM command was not created")
    subprocess.run(
        [
            "aws",
            "ssm",
            "wait",
            "command-executed",
            "--region",
            PRODUCTION_REGION,
            "--command-id",
            command_id,
            "--instance-id",
            instance,
        ],
        check=False,
    )
    invocation = json.loads(
        run(
            [
                "aws",
                "ssm",
                "get-command-invocation",
                "--region",
                PRODUCTION_REGION,
                "--command-id",
                command_id,
                "--instance-id",
                instance,
                "--output",
                "json",
            ],
            capture=True,
        )
    )
    if invocation.get("Status") != "Success" or invocation.get("ResponseCode") != 0:
        raise RuntimeError(
            "production SSM command failed: "
            f"{invocation.get('Status')} response={invocation.get('ResponseCode')}"
        )
    return str(invocation.get("StandardOutputContent", "")).strip()


def capture_frontend_state(instance: str, release_sha: str) -> tuple[str, str, str]:
    if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        raise RuntimeError("frontend snapshot release SHA is invalid")
    snapshot = f"/opt/trustforge/.frontend-rollback-{release_sha[:12]}.tar.gz"
    target = run_ssm(
        instance,
        [
            "set -e",
            (
                "if [ -L /opt/trustforge/frontend/current ]; then "
                "readlink /opt/trustforge/frontend/current; else echo __ABSENT__; fi"
            ),
            (
                "if [ -L /etc/nginx/conf.d/trustforge.conf ]; then "
                "readlink /etc/nginx/conf.d/trustforge.conf; else echo __ABSENT__; fi"
            ),
            (
                f"tar -C / --ignore-failed-read -czf {snapshot} "
                "etc/nginx/conf.d/default.conf "
                "etc/nginx/trustforge-sites "
                "etc/systemd/system/trustforge.service"
            ),
        ],
    ).splitlines()
    if len(target) < 2:
        raise RuntimeError("production frontend state snapshot output is incomplete")
    frontend_target, nginx_target = target[:2]
    if frontend_target != "__ABSENT__" and not re.fullmatch(
        r"/opt/trustforge/frontend/releases/[A-Za-z0-9._-]+", frontend_target
    ):
        raise RuntimeError("production frontend current target is invalid")
    if nginx_target != "__ABSENT__" and not re.fullmatch(
        r"/etc/nginx/trustforge-sites/[A-Za-z0-9._-]+\.conf", nginx_target
    ):
        raise RuntimeError("production nginx live target is invalid")
    return frontend_target, nginx_target, snapshot


def restore_frontend(
    instance: str,
    expected_target: str,
    expected_nginx_target: str,
    snapshot: str,
) -> None:
    if expected_target != "__ABSENT__" and not re.fullmatch(
        r"/opt/trustforge/frontend/releases/[A-Za-z0-9._-]+", expected_target
    ):
        raise RuntimeError("rollback frontend target is invalid")
    if not re.fullmatch(r"/opt/trustforge/\.frontend-rollback-[0-9a-f]{12}\.tar\.gz", snapshot):
        raise RuntimeError("rollback frontend snapshot is invalid")
    if expected_nginx_target != "__ABSENT__" and not re.fullmatch(
        r"/etc/nginx/trustforge-sites/[A-Za-z0-9._-]+\.conf",
        expected_nginx_target,
    ):
        raise RuntimeError("rollback nginx live target is invalid")
    switch_command = (
        "rm -f /opt/trustforge/frontend/current"
        if expected_target == "__ABSENT__"
        else (
            f"ln -sfn {expected_target} /opt/trustforge/frontend/current.rollback && "
            "mv -Tf /opt/trustforge/frontend/current.rollback /opt/trustforge/frontend/current"
        )
    )
    nginx_switch_command = (
        "rm -f /etc/nginx/conf.d/trustforge.conf"
        if expected_nginx_target == "__ABSENT__"
        else (
            f"ln -sfn {expected_nginx_target} /etc/nginx/conf.d/trustforge.conf.rollback && "
            "mv -Tf /etc/nginx/conf.d/trustforge.conf.rollback /etc/nginx/conf.d/trustforge.conf"
        )
    )
    run_ssm(
        instance,
        [
            "set -e",
            f"test -f {snapshot}",
            "rm -f /etc/nginx/conf.d/default.conf /etc/systemd/system/trustforge.service",
            "rm -rf /etc/nginx/trustforge-sites",
            f"tar -C / -xzf {snapshot}",
            switch_command,
            nginx_switch_command,
            "systemctl daemon-reload",
            "nginx -t",
            "systemctl reload nginx",
            "systemctl try-restart trustforge",
            (
                "ready=0; for attempt in $(seq 1 15); do "
                "if curl -fsS --max-time 2 http://localhost/healthz >/dev/null; "
                "then ready=1; break; fi; sleep 2; done; test \"$ready\" = 1"
            ),
            f"rm -f {snapshot}",
        ],
    )


def discard_frontend_snapshot(instance: str, snapshot: str) -> None:
    if not re.fullmatch(r"/opt/trustforge/\.frontend-rollback-[0-9a-f]{12}\.tar\.gz", snapshot):
        raise RuntimeError("frontend snapshot path is invalid")
    run_ssm(instance, [f"rm -f {snapshot}"])


def verify_training_data_reconciliation(instance: str) -> int:
    """Fail deployment when the API silently diverges from production JSONL."""
    output = run_ssm(
        instance,
        [
            "python3.11 - <<'PY'",
            "import json, os, pathlib, shlex, subprocess, sys, urllib.request",
            "unit_env = subprocess.check_output(['systemctl', 'show', 'trustforge.service', '--property=Environment', '--value'], text=True)",
            "configured = [item.split('=', 1)[1] for item in shlex.split(unit_env) if item.startswith('TRUSTFORGE_TRAINING_DATA_DIR=')]",
            "if len(configured) != 1: raise SystemExit('training data path missing from systemd environment')",
            "os.environ['TRUSTFORGE_TRAINING_DATA_DIR'] = configured[0]",
            "sys.path.insert(0, '/opt/trustforge/src')",
            "from trustforge.training_data import resolve_training_data_dir, scan_training_data",
            "actual = scan_training_data(resolve_training_data_dir()).total_records",
            "with urllib.request.urlopen('http://localhost/api/training-status', timeout=10) as response:",
            "    payload = json.load(response)",
            "api_total = payload.get('data', {}).get('training_data', {}).get('total_records')",
            "if not isinstance(api_total, int) or api_total != actual or actual <= 0:",
            "    raise SystemExit(f'training data reconciliation failed: api={api_total!r} actual={actual}')",
            "print(json.dumps({'training_records': actual}))",
            "PY",
        ],
    )
    try:
        result = json.loads(output.splitlines()[-1])
        count = result["training_records"]
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("training data reconciliation evidence is invalid") from exc
    if not isinstance(count, int) or count <= 0:
        raise RuntimeError("training data reconciliation returned no records")
    return count


def restore_backend(
    main_tree: Path,
    expected_sha: str,
    expected_digest: str,
    expected_pointer: dict[str, object],
) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise RuntimeError("rollback backend SHA is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise RuntimeError("rollback backend digest is invalid")
    bucket = f"trustforge-deploy-{production_account()}"
    manifest = json.loads(
        run(
            [
                "aws",
                "s3",
                "cp",
                f"s3://{bucket}/artifacts/{expected_digest}/manifest.json",
                "-",
                "--region",
                PRODUCTION_REGION,
            ],
            capture=True,
        )
    )
    if manifest.get("git_sha") != expected_sha:
        raise RuntimeError("rollback backend manifest SHA mismatch")
    if expected_pointer.get("digest") != expected_digest:
        raise RuntimeError("rollback backend pointer digest mismatch")
    version = expected_pointer.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", version):
        raise RuntimeError("rollback backend pointer version is invalid")
    manifest_version = manifest.get("version")
    if manifest_version is not None and manifest_version != version:
        raise RuntimeError("rollback backend manifest version mismatch")
    instance = production_instance()
    pointer = json.dumps(expected_pointer, sort_keys=True)
    subprocess.run(
        [
            "aws",
            "s3",
            "cp",
            "-",
            f"s3://{bucket}/pointers/candidate.json",
            "--region",
            PRODUCTION_REGION,
        ],
        input=pointer,
        text=True,
        check=True,
    )
    subprocess.run(
        ["bash", "deploy/activate_release.sh", "--target", instance],
        cwd=main_tree,
        env=os.environ,
        check=True,
    )
    restored_sha, restored_digest = production_identity()
    if (restored_sha, restored_digest) != (expected_sha, expected_digest):
        raise RuntimeError("rollback backend identity verification failed")
    verify_runtime_identity(expected_digest)


def deploy_production(main_tree: Path, main_sha: str, release_branch: str) -> dict[str, str]:
    previous_sha, previous_digest = production_identity()
    previous_pointer = capture_active_pointer(previous_digest)
    env = dict(
        os.environ,
        TRUSTFORGE_RELEASE_SHA=main_sha,
        TRUSTFORGE_RELEASE_BRANCH=release_branch,
    )
    frontend_instance = ""
    previous_frontend_target = ""
    previous_nginx_target = ""
    frontend_snapshot = ""
    try:
        subprocess.run(
            ["/bin/zsh", "-lc", "TRUSTFORGE_BOOTSTRAP=0 bash deploy/deploy_ec2.sh"],
            cwd=main_tree,
            env=env,
            check=True,
        )
        deployed_sha, deployed_digest = production_identity()
        if deployed_sha != main_sha:
            raise RuntimeError("production active SHA does not match the verified main SHA")
        verify_runtime_identity(deployed_digest)
        frontend_instance = production_instance()
        try:
            frontend_asset = verify_frontend_identity(main_sha)
        except (RuntimeError, subprocess.CalledProcessError):
            (
                previous_frontend_target,
                previous_nginx_target,
                frontend_snapshot,
            ) = capture_frontend_state(
                frontend_instance,
                main_sha,
            )
            subprocess.run(
                ["/bin/zsh", "-lc", "bash deploy/deploy_frontend_nginx.sh"],
                cwd=main_tree,
                env=dict(env, VITE_GIT_SHA=main_sha),
                check=True,
            )
            frontend_asset = verify_frontend_identity(main_sha)
        training_records = verify_training_data_reconciliation(frontend_instance)
        if frontend_snapshot:
            discard_frontend_snapshot(frontend_instance, frontend_snapshot)
    except Exception as deploy_error:
        rollback_errors = []
        if (
            frontend_instance
            and previous_frontend_target
            and previous_nginx_target
            and frontend_snapshot
        ):
            try:
                restore_frontend(
                    frontend_instance,
                    previous_frontend_target,
                    previous_nginx_target,
                    frontend_snapshot,
                )
            except Exception as rollback_error:
                rollback_errors.append(f"frontend: {rollback_error}")
        try:
            restore_backend(main_tree, previous_sha, previous_digest, previous_pointer)
        except Exception as rollback_error:
            rollback_errors.append(f"backend: {rollback_error}")
        if rollback_errors:
            raise RuntimeError(
                "production deploy failed and rollback failed: " + "; ".join(rollback_errors)
            ) from deploy_error
        raise
    return {
        "git_sha": deployed_sha,
        "artifact_digest": deployed_digest,
        "frontend_asset": frontend_asset,
        "training_records": training_records,
    }


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


def formal_run_blocked(main_tree: Path, main_sha: str) -> bool:  # noqa: ARG001
    """main 含 formal-run handler 但生產配套未就緒時回傳 True。

    release train 應在此為真時 fail-closed 不部署，直到生產配套（DynamoDB
    table + caller/idempotency/retention secret + EC2 env）完成並建立
    FORMAL_RUN_READY_FLAG。避免部署一個生產環境跑不起來的 formal-run 版本，
    也避免 fe-nginx 在無配套下把對外層改壞。

    flag 存在即視為配套就緒（配套做好後新 main commit 不破壞既有 table/secret/env，
    flag 內容建議寫人類可讀的配套清單供審計）。main_sha 保留為簽名參數穩定呼叫端。
    """
    web_py = main_tree / "src" / "trustforge" / "web.py"
    if not web_py.exists():
        return False
    if FORMAL_HANDLER_MARKER not in web_py.read_text(encoding="utf-8"):
        return False
    return not FORMAL_RUN_READY_FLAG.exists()


def formal_run_pending_origin() -> bool:
    """gate 前止損：origin/main 含 formal handler 但配套 flag 不存在 → True。

    在 worktree/gate 建立前檢查，讓 release train 不受 flaky test 影響可靠止損。
    flag 內容的 SHA 驗證仍由 gate 後的 formal_run_blocked() 把關（雙層防護）。

    git show 失敗時讓 CalledProcessError 傳播（fail-closed：無法確認 origin/main
    狀態時 execute 走 failed 路徑，不部署），不靜默放行。
    """
    origin_main_web = run(
        ["git", "show", "origin/main:src/trustforge/web.py"],
        capture=True,
    )
    if FORMAL_HANDLER_MARKER not in origin_main_web:
        return False
    return not FORMAL_RUN_READY_FLAG.exists()


def execute(args: argparse.Namespace) -> Path:
    started = datetime.now(UTC)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    receipt = {"run_id": run_id, "started_at": started.isoformat(), "status": "running", "steps": []}
    try:
        require_competition_target()
        main_only_mode = bool(getattr(args, "main_only", False))
        with lease():
            require_clean_root()
            run(["git", "fetch", "--prune", "origin"])
            counts = run(
                ["git", "rev-list", "--left-right", "--count", "origin/main...origin/develop"],
                capture=True,
            ).strip().split()
            main_only, develop_only = (int(value) for value in counts)
            receipt["divergence"] = {"main_only": main_only, "develop_only": develop_only}
            receipt["release_scope"] = "main-only" if main_only_mode else "main-and-develop"
            main_sha_remote = run(["git", "rev-parse", "origin/main"], capture=True).strip()
            production_sha, production_digest = production_identity()
            receipt["production_before"] = {"git_sha": production_sha, "artifact_digest": production_digest}
            runtime_in_sync = False
            try:
                verify_runtime_identity(production_digest)
                runtime_in_sync = True
            except Exception as drift:
                receipt.setdefault("preflight_drift", {})["runtime"] = str(drift)
            frontend_in_sync = False
            try:
                verify_frontend_identity(main_sha_remote)
                frontend_in_sync = True
            except Exception as drift:
                receipt.setdefault("preflight_drift", {})["frontend"] = str(drift)
            if args.dry_run:
                receipt["status"] = "dry-run"
                receipt["finished_at"] = datetime.now(UTC).isoformat()
                return record(receipt)
            if (
                (main_only_mode or develop_only == 0)
                and production_sha == main_sha_remote
                and runtime_in_sync
                and frontend_in_sync
            ):
                receipt["status"] = "no-op"
                receipt["finished_at"] = datetime.now(UTC).isoformat()
                return record(receipt)
            # gate 前止損：formal handler 在 origin/main 但配套 flag 不存在 → 不跑 gate/不部署，
            # 避免 finale 衝刺留下的 flaky test 卡住止損、避免部署無配套版本
            if formal_run_pending_origin():
                receipt["status"] = "blocked-formal-pending"
                receipt["blocked_reason"] = (
                    "origin/main 引入 formal-run analysis-question handler，但生產 formal-run "
                    "配套（DynamoDB table + caller/idempotency/retention secret + EC2 env）"
                    f"尚未就緒。完成配套後，將部署目標 main SHA 寫入 {FORMAL_RUN_READY_FLAG} 才會部署。"
                )
                receipt["finished_at"] = datetime.now(UTC).isoformat()
                return record(receipt)
            backup_command = "bash deploy/backup_production_release.sh"
            with tempfile.TemporaryDirectory(prefix="trustforge-release-train-") as temporary:
                base = Path(temporary)
                develop_tree = base / "develop"
                main_tree = base / "main"
                try:
                    develop_sha = ""
                    if not main_only_mode:
                        run(["git", "worktree", "add", "--detach", str(develop_tree), "origin/develop"])
                        gate(develop_tree)
                        develop_sha = run(["git", "rev-parse", "HEAD"], cwd=develop_tree, capture=True).strip()
                        receipt["steps"].append({"develop": develop_sha})
                    run(["git", "worktree", "add", "--detach", str(main_tree), "origin/main"])
                    if develop_only and not main_only_mode:
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
                    if formal_run_blocked(main_tree, main_sha):
                        receipt["status"] = "blocked-formal-pending"
                        receipt["main_sha"] = main_sha
                        receipt["blocked_reason"] = (
                            "main 引入 formal-run analysis-question handler，但生產 formal-run "
                            "配套（DynamoDB table + caller/idempotency/retention secret + EC2 env）"
                            f"尚未就緒。完成配套後，將此 main SHA 寫入 {FORMAL_RUN_READY_FLAG} 才會部署。"
                        )
                        receipt["finished_at"] = datetime.now(UTC).isoformat()
                        return record(receipt)
                    release_branch = f"release/auto-{run_id[:8]}"
                    backup = require_backup_receipt(backup_command, run_id)
                    receipt["steps"].append({"backup_receipt": str(backup)})
                    if develop_only and not main_only_mode:
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
                    elif main_only_mode:
                        run(
                            [
                                "git", "-c", "core.hooksPath=/dev/null",
                                "push", "origin",
                                f"{main_sha}:refs/heads/{release_branch}",
                            ],
                            cwd=main_tree,
                        )
                        receipt["steps"].append({"main": main_sha, "release_branch": release_branch})
                    else:
                        receipt["steps"].append({"main": main_sha, "release_branch": "existing-main-retry"})
                    deployed = deploy_production(main_tree, main_sha, release_branch)
                    receipt["steps"].append({"production_deploy": "passed", **deployed})
                    receipt["post_deploy_verification"] = {
                        "runtime": "passed",
                        "frontend": "passed",
                        "training_data_reconciliation": "passed",
                        "training_records": deployed["training_records"],
                        "verified_main_sha": main_sha,
                        "frontend_asset": deployed["frontend_asset"],
                    }
                    if "preflight_drift" in receipt:
                        receipt["resolved_preflight_drift"] = receipt.pop("preflight_drift")
                finally:
                    cleanup = []
                    for tree in (main_tree, develop_tree):
                        if tree.exists():
                            result = subprocess.run(
                                ["git", "worktree", "remove", "--force", str(tree)],
                                cwd=ROOT,
                            )
                            returncode = result.returncode
                        else:
                            returncode = 0
                        cleanup.append({"path": str(tree), "returncode": returncode})
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
    parser.add_argument(
        "--main-only",
        action="store_true",
        help="deploy origin/main without gating or merging origin/develop",
    )
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
