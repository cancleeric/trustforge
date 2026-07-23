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


def install_bytes(destination: Path, content: bytes, mode: int = 0o600) -> None:
    """Atomically install a managed file without following symlinks."""
    if destination.parent.is_symlink() or destination.is_symlink():
        raise ValueError("destination or parent is a symlink")
    if not destination.parent.exists():
        raise ValueError("destination parent must already exist")
    if Path(os.path.abspath(destination.parent)) != destination.parent.resolve(strict=True):
        raise ValueError("destination parent is not canonical")
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        os.chmod(destination, mode)
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


LABELS = {
    "refresh": "com.hurricanesoft.trustforge-local-refresh",
    "analysis": "com.hurricanesoft.trustforge-analysis-flow",
    "web": "com.hurricanesoft.trustforge-local-web",
    "frontend": "com.hurricanesoft.trustforge-local-frontend",
    "sweep": "com.hurricanesoft.trustforge-ceo-sweep",
    "watchdog": "com.hurricanesoft.trustforge-ceo-health-watchdog",
}


def payload(
    kind: str,
    root: Path,
    python: Path,
    codex: Path | None,
    gh: Path | None = None,
    node: Path | None = None,
) -> dict:
    root = canonical_existing(root)
    python = canonical_existing(python)
    out = root / "out" / "ceo-cycle"
    if kind == "sweep":
        if codex is None or gh is None:
            raise ValueError("codex and gh paths are required for sweep")
        codex = canonical_existing(codex)
        gh = canonical_existing(gh)
        return {
            "ManagedBy": "TrustForge local scheduler v1",
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
    if kind == "watchdog":
        return {
            "ManagedBy": "TrustForge local scheduler v1",
            "Label": "com.hurricanesoft.trustforge-ceo-health-watchdog",
            "ProgramArguments": [str(python), str(root / "scripts/ceo_health_watchdog.py"), "--status", str(out / "status.json"), "--alert", str(out / "health-alert.json")],
            "StartInterval": 300, "RunAtLoad": True, "Umask": 0o77, "WorkingDirectory": str(root),
            "StandardOutPath": str(out / "health-watchdog.out.log"), "StandardErrorPath": str(out / "health-watchdog.err.log"),
        }
    local_out = root / "out" / "logs"
    environment = {
        "TRUSTFORGE_HOME": str(root),
        "TRUSTFORGE_PYTHON": str(python),
        "PYTHONPATH": str(root / "src"),
        "CACHE_BACKEND": "sqlite",
        "COST_LEDGER_BACKEND": "sqlite",
        "TRUSTFORGE_SQLITE_PATH": str(root / "out" / "trustforge.sqlite3"),
        "TRUSTFORGE_DISABLE_ADMIN_CONFIG": "1",
    }
    arguments: list[str]
    interval: int | None = None
    keep_alive = False
    working_directory = root
    if kind == "refresh":
        arguments = [str(root / "scripts" / "run_local_refresh.sh")]
        interval = 900
    elif kind == "analysis":
        arguments = [str(python), str(root / "scripts" / "run_analysis_flow.py"), "--daemon",
                     "--workers-per-stage", "4", "--poll-seconds", "2", "--schedule-seconds", "1800"]
        environment["TRUSTFORGE_HERMES_AUTONOMY_ENABLED"] = "1"
        keep_alive = True
    elif kind == "web":
        arguments = [str(python), "-m", "trustforge.web"]
        environment.update({
            "TRUSTFORGE_BIND_HOST": "127.0.0.1",
            "TRUSTFORGE_STATUS_RATE_MAX": "120",
            "TRUSTFORGE_WEB_MAX_ACTIVE_REQUESTS": "32",
            "TRUSTFORGE_ALLOW_INSECURE_LIVE_TOKEN": "1",
            "TRUSTFORGE_CORS_ALLOW_ORIGINS": "http://127.0.0.1:4174,http://localhost:4174",
            "PORT": "8799",
        })
        keep_alive = True
    elif kind == "frontend":
        if node is None:
            raise ValueError("node path is required for frontend")
        node = canonical_existing(node)
        vite = canonical_existing(root / "frontend" / "node_modules" / "vite" / "bin" / "vite.js")
        arguments = [str(node), str(vite), "--host", "127.0.0.1", "--port", "4174", "--strictPort"]
        environment = {"VITE_API_PROXY_TARGET": "http://127.0.0.1:8799"}
        working_directory = root / "frontend"
        keep_alive = True
    else:
        raise ValueError(f"unsupported launch agent kind: {kind}")
    result = {
        "ManagedBy": "TrustForge local scheduler v1",
        "Label": LABELS[kind],
        "ProgramArguments": arguments,
        "EnvironmentVariables": environment,
        "RunAtLoad": True,
        "Umask": 0o77,
        "WorkingDirectory": str(working_directory),
        "StandardOutPath": str(local_out / f"{LABELS[kind]}.out.log"),
        "StandardErrorPath": str(local_out / f"{LABELS[kind]}.err.log"),
    }
    if interval is not None:
        result["StartInterval"] = interval
    if keep_alive:
        result["KeepAlive"] = True
        result["ThrottleInterval"] = 5
    return result


MANAGED_MARKER = "# Managed-By: TrustForge local scheduler v1"


def _systemd_quote(value: str) -> str:
    if any(char in value for char in "\r\n\0"):
        raise ValueError("systemd value contains a control character")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def systemd_units(kind: str, root: Path, python: Path, node: Path | None = None) -> dict[str, bytes]:
    root = canonical_existing(root)
    python = canonical_existing(python)
    q = _systemd_quote
    header = f"{MANAGED_MARKER}\n"
    environment = f"Environment={q(f'TRUSTFORGE_HOME={root}')}\n"
    pythonpath = q(f"PYTHONPATH={root / 'src'}")
    local_environment = "".join(
        f"Environment={q(value)}\n"
        for value in (
            f"TRUSTFORGE_PYTHON={python}",
            "CACHE_BACKEND=sqlite",
            "COST_LEDGER_BACKEND=sqlite",
            f"TRUSTFORGE_SQLITE_PATH={root / 'out/trustforge.sqlite3'}",
            "TRUSTFORGE_DISABLE_ADMIN_CONFIG=1",
        )
    )
    if kind == "refresh":
        service = (
            f"{header}[Unit]\nDescription=TrustForge local cache refresh\n\n[Service]\n"
            f"Type=oneshot\nWorkingDirectory={q(str(root))}\n{environment}"
            f"Environment={q(f'TRUSTFORGE_PYTHON={python}')}\n"
            f"ExecStart={q(str(root / 'scripts/run_local_refresh.sh'))}\n"
        )
        timer = (
            f"{header}[Unit]\nDescription=Run TrustForge local refresh periodically\n\n"
            "[Timer]\nOnBootSec=2m\nOnUnitActiveSec=15m\nPersistent=true\n\n"
            "[Install]\nWantedBy=timers.target\n"
        )
        return {"trustforge-local-refresh.service": service.encode(), "trustforge-local-refresh.timer": timer.encode()}
    if kind == "analysis":
        command = " ".join(q(str(value)) for value in (
            python, root / "scripts/run_analysis_flow.py", "--daemon", "--workers-per-stage",
            "4", "--poll-seconds", "2", "--schedule-seconds", "1800",
        ))
        body = (
            f"{header}[Unit]\nDescription=TrustForge local analysis flow\n\n[Service]\n"
            f"WorkingDirectory={q(str(root))}\n{environment}Environment={pythonpath}\n{local_environment}"
            f"Environment={q('TRUSTFORGE_HERMES_AUTONOMY_ENABLED=1')}\nExecStart={command}\n"
            "Restart=on-failure\n\n[Install]\nWantedBy=default.target\n"
        )
        return {"trustforge-analysis-flow.service": body.encode()}
    if kind == "web":
        command = " ".join((q(str(python)), q("-m"), q("trustforge.web")))
        body = (
            f"{header}[Unit]\nDescription=TrustForge local web\n\n[Service]\n"
            f"WorkingDirectory={q(str(root))}\n{environment}Environment={pythonpath}\n{local_environment}"
            f"Environment={q('TRUSTFORGE_BIND_HOST=127.0.0.1')}\n"
            f"Environment={q('TRUSTFORGE_STATUS_RATE_MAX=120')}\n"
            f"Environment={q('TRUSTFORGE_WEB_MAX_ACTIVE_REQUESTS=32')}\n"
            f"Environment={q('TRUSTFORGE_ALLOW_INSECURE_LIVE_TOKEN=1')}\n"
            f"Environment={q('TRUSTFORGE_CORS_ALLOW_ORIGINS=http://127.0.0.1:4174,http://localhost:4174')}\n"
            f"Environment={q('PORT=8799')}\n"
            f"ExecStart={command}\nRestart=on-failure\n\n[Install]\nWantedBy=default.target\n"
        )
        return {"trustforge-local-web.service": body.encode()}
    if kind == "frontend":
        if node is None:
            raise ValueError("node path is required for frontend")
        node = canonical_existing(node)
        command = " ".join(q(str(value)) for value in (
            node, root / "frontend/node_modules/vite/bin/vite.js", "--host", "127.0.0.1",
            "--port", "4174", "--strictPort",
        ))
        body = (
            f"{header}[Unit]\nDescription=TrustForge local frontend\nAfter=trustforge-local-web.service\n\n"
            f"[Service]\nWorkingDirectory={q(str(root / 'frontend'))}\n"
            f"Environment={q('VITE_API_PROXY_TARGET=http://127.0.0.1:8799')}\n"
            f"ExecStart={command}\nRestart=on-failure\n\n[Install]\nWantedBy=default.target\n"
        )
        return {"trustforge-local-frontend.service": body.encode()}
    raise ValueError(f"unsupported systemd kind: {kind}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=tuple(LABELS), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--codex", type=Path)
    parser.add_argument("--gh", type=Path)
    parser.add_argument("--node", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--skip-log-prepare", action="store_true")
    parser.add_argument("--format", choices=("launchd", "systemd"), default="launchd")
    args = parser.parse_args()
    try:
        if args.format == "systemd":
            for name, content in systemd_units(args.kind, args.root, args.python, args.node).items():
                install_bytes(args.destination / name, content)
            return 0
        agent_payload = payload(args.kind, args.root, args.python, args.codex, args.gh, args.node)
        if not args.skip_log_prepare:
            prepare_logs(agent_payload)
        install_plist(args.destination, agent_payload)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
