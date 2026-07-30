#!/usr/bin/env python3
"""Dedicated real-Linux NF2 mechanism/adversarial harness.

This script refuses containers and non-root/non-x86-64 hosts. It mounts copied
accepted NF1 installs into the broker's fixed path inside private mount
namespaces; it never treats cross-compilation as evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import platform
import signal
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

FIXED = Path("/opt/trustforge/native-foundation/current")
BLOCKED_EXTERNAL = 77
ACCEPTED_MANIFEST = "5e2db7cf733482a0c43bbfe2a27e96c3b255c1a69dde32054db3181a92fd241c"
ACCEPTED_RUNTIME = "cf8c2165cb93b7a8712d848b653d51a977f4ce12f1a9dad7bd41e189ee694f86"
ACCEPTED_ARCHIVE = "808487c590a183a8df2e69cfc5257969e18ae88b15c4378da95d97add6c03c1b"
CRATE = Path("native/nf2-zero-capability-broker")
PINNED_RUST_RELEASE = "1.96.0"
PINNED_RUST_COMMIT = "ac68faa20c58cbccd01ee7208bf3b6e93a7d7f96"
PINNED_TOOLCHAIN = "1.96.0-x86_64-unknown-linux-gnu"
SYSTEM_GIT = Path("/usr/bin/git")
ROOT_RUSTUP = Path("/root/.cargo/bin/rustup")
SYSTEM_UNSHARE = Path("/usr/bin/unshare")
SYSTEM_MOUNT = Path("/usr/bin/mount")
SYSTEM_SHELL = Path("/bin/sh")
SYSTEM_SLEEP = Path("/usr/bin/sleep")
SYSTEM_MV = Path("/usr/bin/mv")
SYSTEM_LN = Path("/usr/bin/ln")
SYSTEM_TOUCH = Path("/usr/bin/touch")
# This allowlist is a trust anchor, not caller input. It must be populated by a
# reviewed commit from the dedicated-host provisioning receipt before Linux
# evidence can PASS.
APPROVED_TOOL_SHA256 = {
    "git": "UNPROVISIONED",
    "rustup": "UNPROVISIONED",
    "rustc": "UNPROVISIONED",
    "cargo": "UNPROVISIONED",
    "unshare": "UNPROVISIONED",
    "mount": "UNPROVISIONED",
    "shell": "UNPROVISIONED",
    "sleep": "UNPROVISIONED",
    "mv": "UNPROVISIONED",
    "ln": "UNPROVISIONED",
    "touch": "UNPROVISIONED",
}
VERIFIED_HOST_TOOLS: dict[str, Path] = {}


def blocked(reason: str) -> None:
    print(f"BLOCKED_EXTERNAL_LINUX: {reason}")
    raise SystemExit(BLOCKED_EXTERNAL)


def require_host() -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        blocked("requires Linux x86_64")
    if os.geteuid() != 0:
        blocked("requires root in a dedicated test host")
    cgroup = Path("/proc/1/cgroup").read_text(errors="replace")
    mountinfo = Path("/proc/self/mountinfo").read_text(errors="replace")
    systemd_container = Path("/run/systemd/container")
    uid_map = Path("/proc/self/uid_map").read_text(errors="replace").strip()
    if (
        Path("/.dockerenv").exists()
        or Path("/.containerenv").exists()
        or (systemd_container.exists() and systemd_container.read_text().strip())
        or any(marker in mountinfo for marker in (" overlay ", "/containers/", "kubepods"))
        or uid_map.split() != ["0", "0", "4294967295"]
        or any(
        marker in cgroup for marker in ("docker", "kubepods", "containerd", "lxc")
        )
    ):
        blocked("container evidence is not accepted")
    if not FIXED.is_dir():
        blocked(f"dedicated host must provision mountpoint {FIXED}")
    for path in (
        SYSTEM_UNSHARE,
        SYSTEM_MOUNT,
        SYSTEM_SHELL,
        SYSTEM_SLEEP,
        SYSTEM_MV,
        SYSTEM_LN,
        SYSTEM_TOUCH,
    ):
        if not path.exists():
            blocked(f"required host tool unavailable: {path}")


def checked_output(
    command: list[str], cwd: Path, environment: dict[str, str] | None = None
) -> str:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=True,
    ).stdout.strip()


def verify_tool(path: Path, expected_digest: str, label: str) -> Path:
    if len(expected_digest) != 64 or any(
        character not in "0123456789abcdef" for character in expected_digest
    ):
        blocked("approved Linux tool receipt is not provisioned in reviewed commit")
    resolved = path.resolve(strict=True)
    metadata = resolved.stat()
    if (
        not resolved.is_file()
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
        or digest(resolved) != expected_digest
    ):
        blocked(f"{label} differs from independently approved tool receipt")
    return resolved


def host_tool(label: str) -> Path:
    try:
        return VERIFIED_HOST_TOOLS[label]
    except KeyError:
        blocked(f"verified host tool unavailable: {label}")


def build_reviewed_brokers(
    source_tree: Path,
    reviewed_commit: str,
    target_root: Path,
) -> tuple[Path, Path, dict[str, str]]:
    tool_receipt = APPROVED_TOOL_SHA256
    git = verify_tool(SYSTEM_GIT, tool_receipt["git"], "git")
    rustup = verify_tool(ROOT_RUSTUP, tool_receipt["rustup"], "rustup")
    git_home = target_root / "git-home"
    git_home.mkdir(parents=True)
    git_environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(git_home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "LANG": "C",
        "LC_ALL": "C",
    }
    actual_commit = checked_output(
        [str(git), "rev-parse", "HEAD"], source_tree, git_environment
    )
    if actual_commit != reviewed_commit:
        raise RuntimeError(
            f"source HEAD {actual_commit} differs from reviewed commit {reviewed_commit}"
        )
    if checked_output(
        [str(git), "status", "--porcelain=v1"], source_tree, git_environment
    ):
        raise RuntimeError("reviewed source tree must be completely clean")
    if checked_output(
        [str(git), "for-each-ref", "--format=%(refname)", "refs/replace"],
        source_tree,
        git_environment,
    ):
        raise RuntimeError("reviewed repository must not contain replacement refs")
    attributes_path = Path(
        checked_output(
            [str(git), "rev-parse", "--git-path", "info/attributes"],
            source_tree,
            git_environment,
        )
    )
    if not attributes_path.is_absolute():
        attributes_path = source_tree / attributes_path
    if attributes_path.exists() and attributes_path.read_bytes():
        raise RuntimeError("repository-local archive attributes are forbidden")
    archive = subprocess.run(
        [str(git), "archive", "--format=tar", reviewed_commit],
        cwd=source_tree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=git_environment,
        check=True,
    ).stdout
    exported_source = target_root / "reviewed-source"
    exported_source.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
        stream.extractall(exported_source, filter="data")
    manifest = exported_source / CRATE / "Cargo.toml"
    normal_target = target_root / "normal"
    hook_target = target_root / "hooks"
    rustup_environment = {
        "HOME": "/root",
        "RUSTUP_HOME": "/root/.rustup",
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
    }
    cargo = verify_tool(
        Path(
            subprocess.run(
                [str(rustup), "which", "--toolchain", PINNED_TOOLCHAIN, "cargo"],
                env=rustup_environment,
                text=True,
                stdout=subprocess.PIPE,
                check=True,
            ).stdout.strip()
        ),
        tool_receipt["cargo"],
        "cargo",
    )
    rustc = verify_tool(
        Path(
            subprocess.run(
                [str(rustup), "which", "--toolchain", PINNED_TOOLCHAIN, "rustc"],
                env=rustup_environment,
                text=True,
                stdout=subprocess.PIPE,
                check=True,
            ).stdout.strip()
        ),
        tool_receipt["rustc"],
        "rustc",
    )
    clean_home = target_root / "home"
    cargo_home = target_root / "cargo-home"
    clean_home.mkdir()
    cargo_home.mkdir()
    build_environment = {
        "PATH": os.pathsep.join(
            dict.fromkeys(
                [str(cargo.parent), str(rustc.parent), "/usr/bin", "/bin"]
            )
        ),
        "HOME": str(clean_home),
        "CARGO_HOME": str(cargo_home),
        "RUSTC": str(rustc),
        "LANG": "C",
        "LC_ALL": "C",
    }
    rust_version = subprocess.run(
        [str(rustc), "-Vv"],
        env=build_environment,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    cargo_version = subprocess.run(
        [str(cargo), "-V"],
        env=build_environment,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    if (
        f"release: {PINNED_RUST_RELEASE}" not in rust_version
        or f"commit-hash: {PINNED_RUST_COMMIT}" not in rust_version
        or not cargo_version.startswith(f"cargo {PINNED_RUST_RELEASE} ")
    ):
        blocked("pinned Rust 1.96.0 toolchain is unavailable")
    subprocess.run(
        [
            str(cargo),
            "build",
            "--locked",
            "--release",
            "--manifest-path",
            str(manifest),
            "--target-dir",
            str(normal_target),
        ],
        cwd=exported_source,
        env=build_environment,
        check=True,
    )
    subprocess.run(
        [
            str(cargo),
            "build",
            "--locked",
            "--features",
            "adversarial-test-hooks",
            "--manifest-path",
            str(manifest),
            "--target-dir",
            str(hook_target),
        ],
        cwd=exported_source,
        env=build_environment,
        check=True,
    )
    return (
        normal_target / "release/trustforge-nf2-zero-capability-broker",
        hook_target / "debug/trustforge-nf2-zero-capability-broker",
        {
            "approved_tool_sha256": dict(sorted(tool_receipt.items())),
            "verified_host_tool_paths": {
                label: str(path)
                for label, path in sorted(VERIFIED_HOST_TOOLS.items())
            },
            "git_path": str(git),
            "rustup_path": str(rustup),
            "rustc_path": str(rustc),
            "cargo_path": str(cargo),
            "rustc": rust_version.replace("\n", "; "),
            "cargo": cargo_version,
        },
    )


def run_case(
    broker: Path, install: Path, *, test_mode: str | None = None
) -> subprocess.CompletedProcess[str]:
    inherited_fd = "exec 9</dev/null; " if test_mode == "extra-fd" else ""
    command = (
        f"{host_tool('mount')} --bind \"$1\" \"$2\" && "
        f"{host_tool('mount')} -o remount,bind,ro \"$2\" && "
        f"{inherited_fd}exec \"$3\""
    )
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
    if test_mode:
        environment["TRUSTFORGE_NF2_TEST_MODE"] = test_mode
    return subprocess.run(
        [
            str(host_tool("unshare")),
            "--mount",
            "--fork",
            str(host_tool("shell")),
            "-ceu",
            command,
            "nf2-case",
            str(install),
            str(FIXED),
            str(broker),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
        env=environment,
    )


def copy_install(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, symlinks=True)
    for path in destination.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(0o555)
        elif path.name == "trustforge-native-foundation":
            path.chmod(0o555)
        else:
            path.chmod(0o444)
        os.chown(path, 0, 0)
    os.chown(destination, 0, 0)
    destination.chmod(0o555)


def expect(label: str, result: subprocess.CompletedProcess[str], success: bool) -> None:
    passed = result.returncode == 0
    expected_block = result.returncode == 70 and result.stderr.startswith("BLOCK: ")
    if (success and not passed) or (not success and not expected_block):
        raise RuntimeError(
            f"{label}: expected {'success' if success else 'broker BLOCK'}, "
            f"rc={result.returncode}, "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}"
        )


def verify_broker_death_kills_child(broker: Path, install: Path, root: Path) -> None:
    pidfile = root / "broker.pid"
    command = (
        f"{host_tool('mount')} --bind \"$1\" \"$2\" && "
        f"{host_tool('mount')} -o remount,bind,ro \"$2\" && "
        "TRUSTFORGE_NF2_TEST_MODE=hang \"$3\" & "
        "broker=$!; echo \"$broker\" >\"$4\"; wait \"$broker\""
    )
    process = subprocess.Popen(
        [
            str(host_tool("unshare")),
            "--mount",
            "--fork",
            str(host_tool("shell")),
            "-ceu",
            command,
            "nf2-kill",
            str(install),
            str(FIXED),
            str(broker),
            str(pidfile),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )
    deadline = time.monotonic() + 3
    while not pidfile.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not pidfile.exists():
        process.kill()
        raise RuntimeError("broker-kill pidfile deadline exceeded")
    broker_pid = int(pidfile.read_text().strip())
    children_path = Path(f"/proc/{broker_pid}/task/{broker_pid}/children")
    while (
        (not children_path.exists() or not children_path.read_text().strip())
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    children = [int(value) for value in children_path.read_text().split()]
    if not children:
        process.kill()
        raise RuntimeError("broker-kill child was not observed")
    os.kill(broker_pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while any(Path(f"/proc/{pid}").exists() for pid in children) and time.monotonic() < deadline:
        time.sleep(0.01)
    process.wait(timeout=5)
    if any(Path(f"/proc/{pid}").exists() for pid in children):
        raise RuntimeError("PDEATHSIG did not remove broker child")


def verify_openat2_toctou_blocks(broker: Path, install: Path) -> None:
    command = r'''
__MOUNT__ --bind "$1" "$2"
TRUSTFORGE_NF2_SEALED_PAUSE=after-install "$3" &
broker=$!
base="/tmp/trustforge-nf2-${broker}-after-install"
deadline=500
while [ ! -f "${base}.ready" ] && [ "$deadline" -gt 0 ]; do
  deadline=$((deadline-1))
  __SLEEP__ 0.01
done
[ -f "${base}.ready" ]
__MV__ "$2/package" "$2/package.real"
__LN__ -s package.real "$2/package"
__TOUCH__ "${base}.continue"
if wait "$broker"; then
  echo "TOCTOU substitution unexpectedly passed" >&2
  exit 1
fi
'''
    for marker, label in (
        ("__MOUNT__", "mount"),
        ("__SLEEP__", "sleep"),
        ("__MV__", "mv"),
        ("__LN__", "ln"),
        ("__TOUCH__", "touch"),
    ):
        command = command.replace(marker, str(host_tool(label)))
    result = subprocess.run(
        [
            str(host_tool("unshare")),
            "--mount",
            "--fork",
            str(host_tool("shell")),
            "-ceu",
            command,
            "nf2-toctou",
            str(install),
            str(FIXED),
            str(broker),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"openat2 TOCTOU harness failed: {result.stdout!r} {result.stderr!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-tree", type=Path, required=True)
    parser.add_argument("--reviewed-commit", required=True)
    parser.add_argument("--accepted-install", type=Path, required=True)
    parser.add_argument("--accepted-archive", type=Path, required=True)
    arguments = parser.parse_args()
    require_host()
    for label, path in (
        ("unshare", SYSTEM_UNSHARE),
        ("mount", SYSTEM_MOUNT),
        ("shell", SYSTEM_SHELL),
        ("sleep", SYSTEM_SLEEP),
        ("mv", SYSTEM_MV),
        ("ln", SYSTEM_LN),
        ("touch", SYSTEM_TOUCH),
    ):
        VERIFIED_HOST_TOOLS[label] = verify_tool(
            path, APPROVED_TOOL_SHA256[label], label
        )
    source_tree = arguments.source_tree.resolve(strict=True)
    source = arguments.accepted_install.resolve(strict=True)
    accepted_archive = arguments.accepted_archive.resolve(strict=True)
    manifest_source = source / "native-hermetic-provenance.json"
    runtime_source = source / "package/bin/trustforge-native-foundation"
    if digest(manifest_source) != ACCEPTED_MANIFEST or digest(runtime_source) != ACCEPTED_RUNTIME:
        raise RuntimeError("input install differs from compile-time accepted NF1 receipt")
    if digest(accepted_archive) != ACCEPTED_ARCHIVE:
        raise RuntimeError("input archive differs from compile-time accepted NF1 receipt")
    with tempfile.TemporaryDirectory(prefix="trustforge-nf2-linux-") as raw:
        root = Path(raw)
        broker, hook_broker, toolchain = build_reviewed_brokers(
            source_tree,
            arguments.reviewed_commit,
            root / "cargo-target",
        )
        print(
            "EVIDENCE",
            {
                "reviewed_commit": arguments.reviewed_commit,
                "kernel": platform.release(),
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "broker_sha256": digest(broker),
                "hook_broker_sha256": digest(hook_broker),
                "toolchain": toolchain,
                "archive_sha256": digest(accepted_archive),
                "manifest_sha256": digest(manifest_source),
                "nf1_sha256": digest(runtime_source),
            },
        )
        positive = root / "positive"
        copy_install(source, positive)
        expect("positive", run_case(broker, positive), True)

        runtime_mutation = root / "runtime-mutation"
        copy_install(source, runtime_mutation)
        runtime = runtime_mutation / "package/bin/trustforge-native-foundation"
        runtime.chmod(0o755)
        runtime.write_bytes(runtime.read_bytes() + b"\0")
        runtime.chmod(0o555)
        expect("runtime mutation", run_case(broker, runtime_mutation), False)

        duplicate_manifest = root / "duplicate-manifest"
        copy_install(source, duplicate_manifest)
        manifest = duplicate_manifest / "native-hermetic-provenance.json"
        manifest.chmod(0o644)
        payload = manifest.read_bytes()
        manifest.write_bytes(payload.replace(b"{", b'{"schema":"duplicate",', 1))
        manifest.chmod(0o444)
        expect("duplicate manifest key", run_case(broker, duplicate_manifest), False)

        hardlink = root / "manifest-hardlink"
        copy_install(source, hardlink)
        manifest = hardlink / "native-hermetic-provenance.json"
        os.link(manifest, hardlink / "manifest-alias")
        expect("manifest hardlink", run_case(broker, hardlink), False)

        symlink = root / "manifest-symlink"
        copy_install(source, symlink)
        manifest = symlink / "native-hermetic-provenance.json"
        manifest.unlink()
        manifest.symlink_to("package/config/fixed-config.json")
        expect("manifest symlink", run_case(broker, symlink), False)

        expect("adversarial-hook baseline", run_case(hook_broker, positive), True)
        for mode, evidence_kind in (
            ("extra-fd", "runtime adversarial"),
            ("forbidden-syscall", "runtime adversarial"),
            ("wrong-output", "runtime adversarial"),
            ("stderr-output", "runtime adversarial"),
            ("partial-output", "runtime adversarial"),
            ("hang", "runtime adversarial"),
            ("exec-mismatch", "verifier fault injection"),
            ("pid-substitution", "verifier fault injection"),
            ("live-exec-substitution", "verifier fault injection"),
            ("live-map-substitution", "verifier fault injection"),
        ):
            expect(
                f"{evidence_kind}: {mode}",
                run_case(hook_broker, positive, test_mode=mode),
                False,
            )
        toctou = root / "openat2-toctou"
        copy_install(source, toctou)
        verify_openat2_toctou_blocks(hook_broker, toctou)
        verify_broker_death_kills_child(hook_broker, positive, root)
    print("PASS: NF2 real-Linux positive and adversarial install cases")
    return 0


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
