#!/usr/bin/env python3
"""Dedicated real-Linux NF2 mechanism/adversarial harness.

This script refuses containers and non-root/non-x86-64 hosts. It mounts copied
accepted NF1 installs into the broker's fixed path inside private mount
namespaces; it never treats cross-compilation as evidence.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import os
import platform
import signal
import shutil
import stat
import struct
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
HOST_RECEIPT = Path("docs/evidence/nf2-linux-host-receipt.json")
HOST_RECEIPT_SHA256 = "24fda3acdee74e1756ee7f0b1ee2aa18d085e0148278d6ab48047bafe8db7547"
TARGET_RECEIPT = Path("docs/evidence/rust-1.96.0-x86_64-unknown-linux-musl-tree.json")
TARGET_RECEIPT_SHA256 = "49c92219312e619b6b49b9355425fa84c21da02fb38828819ba41ecc3b3489d1"
TARGET_ENTRIES_SHA256 = "738cd55ce0397d85b911f5171ef68c48b320465f58a8e2fd65e4067a2668979a"
CRATE = Path("native/nf2-zero-capability-broker")
PINNED_RUST_RELEASE = "1.96.0"
PINNED_RUST_COMMIT = "ac68faa20c58cbccd01ee7208bf3b6e93a7d7f96"
PINNED_TOOLCHAIN = "1.96.0-x86_64-unknown-linux-gnu"
SYSTEM_GIT = Path("/usr/bin/git")
ROOT_RUSTUP = Path("/root/.cargo/bin/rustup")
ROOT_RUST_LLD = Path(
    "/root/.rustup/toolchains/1.96.0-x86_64-unknown-linux-gnu/"
    "lib/rustlib/x86_64-unknown-linux-gnu/bin/rust-lld"
)
SYSTEM_UNSHARE = Path("/usr/bin/unshare")
SYSTEM_MOUNT = Path("/usr/bin/mount")
SYSTEM_SHELL = Path("/usr/bin/bash")
SYSTEM_SLEEP = Path("/usr/bin/sleep")
SYSTEM_MV = Path("/usr/bin/mv")
SYSTEM_LN = Path("/usr/bin/ln")
SYSTEM_TOUCH = Path("/usr/bin/touch")
# This allowlist is a trust anchor, not caller input. It must be populated by a
# reviewed commit from the dedicated-host provisioning receipt before Linux
# evidence can PASS.
APPROVED_TOOL_SHA256 = {
    "git": "2a8c18fbf43da9f692d75474c72bea9dfd796c260b0f3dfe456376abc3bbd668",
    "rustup": "4acc9acc76d5079515b46346a485974457b5a79893cfb01112423c89aeb5aa10",
    "rustc": "ba4b837efb6612dfa8d941c5a72b8a50d1d03a0f36216743b173949aa8d9eb75",
    "cargo": "f30f9fd1b1d0b8fd10dc33219eb4cd4bec3543f40e434ac71f5a03fd0359063f",
    "rust-lld": "d9e01686cf6c278090dd461f9f033e49252829e1388cfa88c9643579701d8c39",
    "unshare": "51bcc77ba5db162c80028f861f0a2770d728c1de80773816d863f28d7a817adb",
    "mount": "ac5aa68d34add5a33ae81ac3a971aea677c4032d768aab5a3c4c2707f728885e",
    "shell": "bc5945feb8bd26203ebfafea5ce1878bb2e32cb8fb50ab7ae395cfb1e1aaaef1",
    "sleep": "06d3927480c7554337818dbf5d91d78689bc8321237280e3d452028d5d1c3f43",
    "mv": "31be03602835c3a6d9b0b11ab1fe52e99bfe91638a3f3b151a8bb0d9d1998b41",
    "ln": "d9140e7c9ef59c2396e188da35f74872fb670d5dd0c2894a679d6a606f66c5ac",
    "touch": "f0959a197fb4b1cb944948ee05bd7858304df6ca7a9afebc34dbb00f108a3cd1",
}
VERIFIED_HOST_TOOLS: dict[str, "VerifiedTool"] = {}
RECEIPT_TOOL_RECORDS: dict[str, dict[str, object]] = {}


class VerifiedTool:
    """Retained handle for the exact executable inode covered by a receipt."""

    def __init__(self, configured: Path, resolved: Path, stream, expected: str):
        self.configured = configured
        self.resolved = resolved
        self.stream = stream
        self.expected = expected
        self.identity = os.fstat(stream.fileno())

    @property
    def fd(self) -> int:
        return self.stream.fileno()

    def exec_path(self) -> str:
        current = os.fstat(self.fd)

        def fields(value):
            return (
                value.st_dev,
                value.st_ino,
                value.st_mode,
                value.st_uid,
                value.st_gid,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )

        if fields(current) != fields(self.identity) or digest_fd(self.fd) != self.expected:
            blocked(f"retained tool changed after verification: {self.configured}")
        return f"/proc/self/fd/{self.fd}"


def verified_pass_fds() -> tuple[int, ...]:
    return tuple(sorted(tool.fd for tool in VERIFIED_HOST_TOOLS.values()))


def canonical_mode(metadata: os.stat_result) -> str:
    return f"{stat.S_IMODE(metadata.st_mode):04o}"


def close_verified_fds_shell() -> str:
    return " ".join(f"exec {fd}<&-;" for fd in verified_pass_fds())


def blocked(reason: str) -> None:
    print(f"BLOCKED_EXTERNAL_LINUX: {reason}")
    raise SystemExit(BLOCKED_EXTERNAL)


CONTAINER_CGROUP_MARKERS = (
    "docker",
    "kubepods",
    "containerd",
    "lxc",
    "libpod",
    "podman",
)


def cgroup_has_container_marker(value: str) -> bool:
    return any(marker in value for marker in CONTAINER_CGROUP_MARKERS)


def normalize_uid_map(value: str) -> str:
    return " ".join(value.split())


def normalized_uid_map() -> str:
    return normalize_uid_map(
        Path("/proc/self/uid_map").read_text(errors="replace")
    )


def namespace_inodes(pid: str) -> dict[str, int]:
    return {
        name: os.stat(f"/proc/{pid}/ns/{name}").st_ino
        for name in ("mnt", "pid", "cgroup", "user")
    }


def native_namespace_evidence() -> tuple[bool, dict[str, int]]:
    mountinfo_lines = Path("/proc/self/mountinfo").read_text(errors="replace").splitlines()
    root_filesystems = []
    for line in mountinfo_lines:
        fields = line.split()
        if len(fields) > 6 and fields[4] == "/" and "-" in fields:
            separator = fields.index("-")
            if separator + 1 < len(fields):
                root_filesystems.append(fields[separator + 1])
    self_namespaces = namespace_inodes("self")
    pid1_namespaces = namespace_inodes("1")
    if self_namespaces != pid1_namespaces:
        return root_filesystems == ["ext4"], {}
    return root_filesystems == ["ext4"], self_namespaces


def require_host() -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        blocked("requires Linux x86_64")
    if os.geteuid() != 0:
        blocked("requires root in a dedicated test host")
    pid1_cgroup = Path("/proc/1/cgroup").read_text(errors="replace")
    self_cgroup = Path("/proc/self/cgroup").read_text(errors="replace")
    systemd_container = Path("/run/systemd/container")
    uid_map = normalized_uid_map()
    native_root, host_namespaces = native_namespace_evidence()
    nspid = next(
        (
            line.split()[1:]
            for line in Path("/proc/self/status").read_text().splitlines()
            if line.startswith("NSpid:")
        ),
        [],
    )
    if (
        Path("/.dockerenv").exists()
        or Path("/.containerenv").exists()
        or (systemd_container.exists() and systemd_container.read_text().strip())
        or not native_root
        or len(host_namespaces) != 4
        or len(nspid) != 1
        or uid_map.split() != ["0", "0", "4294967295"]
        or cgroup_has_container_marker(pid1_cgroup)
        or cgroup_has_container_marker(self_cgroup)
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


def validate_host_receipt(value: object) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict):
        blocked("Linux host receipt must be an object")
    binding = value.get("binding")
    host = value.get("host")
    rust = value.get("rust")
    tools = value.get("tools")
    if (
        value.get("receipt_schema") != "trustforge.nf2-linux-host-receipt/v1"
        or not isinstance(binding, dict)
        or binding.get("issue") != 1088
        or binding.get("status") != "PROVISIONING_REVIEWED"
        or "exact_commit_review" not in binding
        or not isinstance(host, dict)
        or not isinstance(rust, dict)
        or not isinstance(tools, list)
    ):
        blocked("Linux host receipt schema or issue binding is invalid")
    container = host.get("container_evidence")
    native_root, host_namespaces = native_namespace_evidence()
    self_cgroup = Path("/proc/self/cgroup").read_text(errors="replace").strip()
    pid1_cgroup = Path("/proc/1/cgroup").read_text(errors="replace").strip()
    nspid = next(
        (
            line.split()[1:]
            for line in Path("/proc/self/status").read_text().splitlines()
            if line.startswith("NSpid:")
        ),
        [],
    )
    expected_container = {
        "dockerenv_absent": not (
            Path("/.dockerenv").exists() or Path("/.containerenv").exists()
        ),
        "native_ext4_root_mount": native_root,
        "namespace_inodes": host_namespaces,
        "nspid_depth": len(nspid),
        "pid1_cgroup": pid1_cgroup,
        "pid1_cgroup_container_markers_absent": not cgroup_has_container_marker(
            pid1_cgroup
        ),
        "self_cgroup": self_cgroup,
        "self_cgroup_container_markers_absent": not cgroup_has_container_marker(
            self_cgroup
        ),
        "systemd_container_marker_absent": not (
            Path("/run/systemd/container").exists()
            and Path("/run/systemd/container").read_text().strip()
        ),
        "uid_map": normalized_uid_map(),
    }
    if (
        host.get("operating_system") != platform.system()
        or host.get("architecture") != platform.machine()
        or host.get("kernel_release") != platform.release()
        or host.get("hostname") != platform.node()
        or host.get("boot_id")
        != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        or container != expected_container
    ):
        blocked("live host identity or container evidence differs from receipt")
    if (
        rust.get("release") != PINNED_RUST_RELEASE
        or rust.get("commit_hash") != PINNED_RUST_COMMIT
        or not isinstance(rust.get("installer_source"), str)
        or rust.get("installer_sha256") != APPROVED_TOOL_SHA256["rustup"]
        or rust.get("rust_lld_collected_at_utc") != "2026-07-30T08:01:33Z"
        or not str(rust.get("rust_lld_version", "")).startswith("LLD 22.1.2 ")
    ):
        blocked("Rust provisioning receipt is invalid")
    expected_configured = {
        "git": str(SYSTEM_GIT),
        "rustup": str(ROOT_RUSTUP),
        "rustc": f"rustup:{PINNED_TOOLCHAIN}:rustc",
        "cargo": f"rustup:{PINNED_TOOLCHAIN}:cargo",
        "rust-lld": str(ROOT_RUST_LLD),
        "unshare": str(SYSTEM_UNSHARE),
        "mount": str(SYSTEM_MOUNT),
        "shell": str(SYSTEM_SHELL),
        "sleep": str(SYSTEM_SLEEP),
        "mv": str(SYSTEM_MV),
        "ln": str(SYSTEM_LN),
        "touch": str(SYSTEM_TOUCH),
    }
    records: dict[str, dict[str, object]] = {}
    for record in tools:
        if not isinstance(record, dict) or not isinstance(record.get("label"), str):
            blocked("Linux host tool receipt entry is invalid")
        label = record["label"]
        if label in records or label not in expected_configured:
            blocked("Linux host tool receipt labels are duplicate or unexpected")
        if (
            record.get("configured") != expected_configured[label]
            or record.get("sha256") != APPROVED_TOOL_SHA256[label]
            or not isinstance(record.get("resolved"), str)
            or not isinstance(record.get("uid"), int)
            or not isinstance(record.get("gid"), int)
            or not isinstance(record.get("mode"), str)
            or (
                label == "rust-lld"
                and record.get("size") != 11_604_688
            )
        ):
            blocked(f"{label} receipt fields differ from reviewed allowlist")
        records[label] = record
    if set(records) != set(expected_configured):
        blocked("Linux host receipt must contain the exact reviewed tool set")
    if (
        len({record["configured"] for record in records.values()})
        != len(expected_configured)
        or len({record["resolved"] for record in records.values()})
        != len(expected_configured)
    ):
        blocked("Linux host receipt configured and resolved tool paths must be unique")
    return records


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
        pass_fds=verified_pass_fds(),
        check=True,
    ).stdout.strip()


def verify_tool(path: Path, expected_digest: str, label: str) -> VerifiedTool:
    if len(expected_digest) != 64 or any(
        character not in "0123456789abcdef" for character in expected_digest
    ):
        blocked("approved Linux tool receipt is not provisioned in reviewed commit")
    resolved = path.resolve(strict=True)
    original = resolved.open("rb")
    retained_fd = fcntl.fcntl(original.fileno(), fcntl.F_DUPFD_CLOEXEC, 100)
    original.close()
    stream = os.fdopen(retained_fd, "rb", closefd=True)
    metadata = os.fstat(stream.fileno())
    receipt = RECEIPT_TOOL_RECORDS.get(label)
    if (
        receipt is None
        or str(resolved) != receipt.get("resolved")
        or metadata.st_uid != receipt.get("uid")
        or metadata.st_gid != receipt.get("gid")
        or canonical_mode(metadata) != receipt.get("mode")
        or (
            "size" in receipt
            and metadata.st_size != receipt.get("size")
        )
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
        or digest_fd(stream.fileno()) != expected_digest
    ):
        stream.close()
        blocked(f"{label} differs from independently approved tool receipt")
    return VerifiedTool(path, resolved, stream, expected_digest)


def host_tool(label: str) -> str:
    try:
        return VERIFIED_HOST_TOOLS[label].exec_path()
    except KeyError:
        blocked(f"verified host tool unavailable: {label}")


def build_reviewed_brokers(
    source_tree: Path,
    reviewed_commit: str,
    target_root: Path,
) -> tuple[Path, Path, Path, dict[str, str]]:
    tool_receipt = APPROVED_TOOL_SHA256
    VERIFIED_HOST_TOOLS["git"] = verify_tool(
        SYSTEM_GIT, tool_receipt["git"], "git"
    )
    VERIFIED_HOST_TOOLS["rustup"] = verify_tool(
        ROOT_RUSTUP, tool_receipt["rustup"], "rustup"
    )
    git = host_tool("git")
    rustup = host_tool("rustup")
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
        pass_fds=verified_pass_fds(),
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
    VERIFIED_HOST_TOOLS["cargo"] = verify_tool(
        Path(
            subprocess.run(
                ["rustup", "which", "--toolchain", PINNED_TOOLCHAIN, "cargo"],
                executable=str(rustup),
                env=rustup_environment,
                text=True,
                stdout=subprocess.PIPE,
                pass_fds=verified_pass_fds(),
                check=True,
            ).stdout.strip()
        ),
        tool_receipt["cargo"],
        "cargo",
    )
    VERIFIED_HOST_TOOLS["rustc"] = verify_tool(
        Path(
            subprocess.run(
                ["rustup", "which", "--toolchain", PINNED_TOOLCHAIN, "rustc"],
                executable=str(rustup),
                env=rustup_environment,
                text=True,
                stdout=subprocess.PIPE,
                pass_fds=verified_pass_fds(),
                check=True,
            ).stdout.strip()
        ),
        tool_receipt["rustc"],
        "rustc",
    )
    VERIFIED_HOST_TOOLS["rust-lld"] = verify_tool(
        ROOT_RUST_LLD,
        tool_receipt["rust-lld"],
        "rust-lld",
    )
    cargo = host_tool("cargo")
    rustc = host_tool("rustc")
    rust_lld = host_tool("rust-lld")
    clean_home = target_root / "home"
    cargo_home = target_root / "cargo-home"
    clean_home.mkdir()
    cargo_home.mkdir()
    build_environment = {
        "PATH": os.pathsep.join(
            dict.fromkeys(
                [
                    str(VERIFIED_HOST_TOOLS["cargo"].resolved.parent),
                    str(VERIFIED_HOST_TOOLS["rustc"].resolved.parent),
                    "/usr/bin",
                    "/bin",
                ]
            )
        ),
        "HOME": str(clean_home),
        "CARGO_HOME": str(cargo_home),
        "RUSTC": str(rustc),
        "RUSTFLAGS": f"-C linker={rust_lld} -C linker-flavor=ld.lld",
        "LANG": "C",
        "LC_ALL": "C",
    }
    rust_version = subprocess.run(
        [str(rustc), "-Vv"],
        env=build_environment,
        text=True,
        stdout=subprocess.PIPE,
        pass_fds=verified_pass_fds(),
        check=True,
    ).stdout.strip()
    cargo_version = subprocess.run(
        [str(cargo), "-V"],
        env=build_environment,
        text=True,
        stdout=subprocess.PIPE,
        pass_fds=verified_pass_fds(),
        check=True,
    ).stdout.strip()
    if (
        f"release: {PINNED_RUST_RELEASE}" not in rust_version
        or f"commit-hash: {PINNED_RUST_COMMIT}" not in rust_version
        or not cargo_version.startswith(f"cargo {PINNED_RUST_RELEASE} ")
    ):
        blocked("pinned Rust 1.96.0 toolchain is unavailable")
    target_root, target_entries = load_target_receipt(
        Path(__file__).resolve().parents[1]
    )
    verify_target_tree(target_root, target_entries)
    subprocess.run(
        [
            str(cargo),
            "build",
            "--locked",
            "--offline",
            "--frozen",
            "--release",
            "--target",
            "x86_64-unknown-linux-musl",
            "--manifest-path",
            str(manifest),
            "--target-dir",
            str(normal_target),
        ],
        cwd=exported_source,
        env=build_environment,
        pass_fds=verified_pass_fds(),
        check=True,
    )
    subprocess.run(
        [
            str(cargo),
            "build",
            "--locked",
            "--offline",
            "--frozen",
            "--features",
            "adversarial-test-hooks",
            "--target",
            "x86_64-unknown-linux-musl",
            "--manifest-path",
            str(manifest),
            "--target-dir",
            str(hook_target),
        ],
        cwd=exported_source,
        env=build_environment,
        pass_fds=verified_pass_fds(),
        check=True,
    )
    verify_target_tree(target_root, target_entries)
    normal_broker = (
        normal_target
        / "x86_64-unknown-linux-musl/release/trustforge-nf2-zero-capability-broker"
    )
    hook_broker = (
        hook_target
        / "x86_64-unknown-linux-musl/debug/trustforge-nf2-zero-capability-broker"
    )
    second_exec_fixture = (
        hook_target / "x86_64-unknown-linux-musl/debug/nf2-second-exec-fixture"
    )
    verify_static_x86_64_elf(normal_broker)
    verify_static_x86_64_elf(hook_broker)
    verify_static_x86_64_elf(second_exec_fixture)
    return (
        normal_broker,
        hook_broker,
        second_exec_fixture,
        {
            "approved_tool_sha256": dict(sorted(tool_receipt.items())),
            "verified_host_tool_paths": {
                label: str(tool.resolved)
                for label, tool in sorted(VERIFIED_HOST_TOOLS.items())
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
    broker: Path,
    install: Path,
    *,
    test_mode: str | None = None,
    second_exec_fixture: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    inherited_fd = "exec 9</dev/null; " if test_mode == "extra-fd" else ""
    command = (
        f"{host_tool('mount')} --bind \"$1\" \"$2\" && "
        f"{host_tool('mount')} -o remount,bind,ro \"$2\" && "
        f"{close_verified_fds_shell()} {inherited_fd}exec \"$3\""
    )
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
    if test_mode:
        environment["TRUSTFORGE_NF2_TEST_MODE"] = test_mode
    if second_exec_fixture:
        environment["TRUSTFORGE_NF2_SECOND_EXEC_FIXTURE"] = str(second_exec_fixture)
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
        pass_fds=verified_pass_fds(),
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


def expect_exact_block(
    label: str, result: subprocess.CompletedProcess[str], diagnostic: str
) -> None:
    if (
        result.returncode != 70
        or result.stdout
        or result.stderr != f"BLOCK: {diagnostic}\n"
    ):
        raise RuntimeError(
            f"{label}: expected exact broker BLOCK, rc={result.returncode}, "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}"
        )


def verify_broker_death_kills_child(
    broker: Path, install: Path, root: Path, test_mode: str
) -> None:
    pidfile = root / f"broker-{test_mode}.pid"
    command = (
        f"{host_tool('mount')} --bind \"$1\" \"$2\" && "
        f"{host_tool('mount')} -o remount,bind,ro \"$2\" && "
        f"{close_verified_fds_shell()} "
        "TRUSTFORGE_NF2_TEST_MODE=\"$5\" \"$3\" & "
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
            test_mode,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        pass_fds=verified_pass_fds(),
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
    child_identities = {}
    for pid in children:
        identity = process_state_and_starttime(pid)
        if identity is not None:
            child_identities[pid] = identity[1]
    if len(child_identities) != len(children):
        process.kill()
        raise RuntimeError("broker-kill child identity was not retained")
    if not all(
        process_is_same_live(pid, starttime)
        for pid, starttime in child_identities.items()
    ):
        process.kill()
        raise RuntimeError("broker-kill child was not live before parent death")
    os.kill(broker_pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while (
        any(
            process_is_same_live(pid, starttime)
            for pid, starttime in child_identities.items()
        )
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    process.wait(timeout=5)
    if any(
        process_is_same_live(pid, starttime)
        for pid, starttime in child_identities.items()
    ):
        raise RuntimeError("PDEATHSIG did not remove broker child")


def process_state_and_starttime(pid: int) -> tuple[str, str] | None:
    try:
        value = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return None
    end = value.rfind(")")
    fields = value[end + 2 :].split() if end >= 0 else []
    if len(fields) < 20:
        raise RuntimeError("proc stat identity is malformed")
    return fields[0], fields[19]


def process_is_same_live(pid: int, starttime: str) -> bool:
    identity = process_state_and_starttime(pid)
    return (
        identity is not None
        and identity[1] == starttime
        and identity[0] not in {"Z", "X", "x"}
    )


def verify_openat2_toctou_blocks(
    broker: Path, install: Path, evidence_root: Path
) -> None:
    stdout_path = evidence_root / "openat2-toctou.stdout"
    stderr_path = evidence_root / "openat2-toctou.stderr"
    for path in (stdout_path, stderr_path):
        path.touch(mode=0o600, exist_ok=False)
        os.chown(path, 0, 0)
    command = r'''
__MOUNT__ --bind "$1" "$2"
( __CLOSE_FDS__ TRUSTFORGE_NF2_SEALED_PAUSE=after-install exec "$3" ) >"$4" 2>"$5" &
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
set +e
wait "$broker"
rc=$?
set -e
[ "$rc" -eq 70 ]
[ ! -s "$4" ]
IFS= read -r first_line <"$5"
case "$first_line" in
  "BLOCK: "*) ;;
  *) exit 1 ;;
esac
'''
    for marker, label in (
        ("__MOUNT__", "mount"),
        ("__SLEEP__", "sleep"),
        ("__MV__", "mv"),
        ("__LN__", "ln"),
        ("__TOUCH__", "touch"),
    ):
        command = command.replace(marker, str(host_tool(label)))
    command = command.replace("__CLOSE_FDS__", close_verified_fds_shell())
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
            str(stdout_path),
            str(stderr_path),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        pass_fds=verified_pass_fds(),
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
    receipt = Path(__file__).resolve().parents[1] / HOST_RECEIPT
    if digest(receipt) != HOST_RECEIPT_SHA256:
        blocked("canonical Linux host receipt differs from reviewed contract")
    receipt_value = json.loads(receipt.read_bytes())
    RECEIPT_TOOL_RECORDS.update(validate_host_receipt(receipt_value))
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
        broker, hook_broker, second_exec_fixture, toolchain = build_reviewed_brokers(
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
        expect_exact_block(
            "ptrace pathname authority: absolute-exec-path",
            run_case(hook_broker, positive, test_mode="absolute-exec-path"),
            "traced execveat pathname is not empty",
        )
        expect_exact_block(
            "ptrace one-shot authority: second-exec",
            run_case(
                hook_broker,
                positive,
                test_mode="second-exec",
                second_exec_fixture=second_exec_fixture,
            ),
            "second exec transition rejected",
        )
        toctou = root / "openat2-toctou"
        copy_install(source, toctou)
        verify_openat2_toctou_blocks(hook_broker, toctou, root)
        for stage in (
            "hang",
            "pause-bootstrap-stop",
            "pause-seccomp-stop",
            "pause-post-exec-stop",
        ):
            verify_broker_death_kills_child(hook_broker, positive, root, stage)
    print("PASS: NF2 real-Linux positive and adversarial install cases")
    return 0


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def digest_fd(fd: int) -> str:
    value = hashlib.sha256()
    offset = 0
    while True:
        chunk = os.pread(fd, 1024 * 1024, offset)
        if not chunk:
            return value.hexdigest()
        value.update(chunk)
        offset += len(chunk)


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()


def load_target_receipt(repository: Path) -> tuple[Path, list[dict[str, object]]]:
    receipt_path = repository / TARGET_RECEIPT
    if digest(receipt_path) != TARGET_RECEIPT_SHA256:
        blocked("pinned Rust musl target receipt differs from reviewed contract")
    value = json.loads(receipt_path.read_bytes())
    if not isinstance(value, dict):
        blocked("pinned Rust musl target receipt is not an object")
    entries = value.get("entries")
    if (
        value.get("schema") != "trustforge.rust-target-tree/v1"
        or value.get("target") != "x86_64-unknown-linux-musl"
        or value.get("root_relative") != "lib/rustlib/x86_64-unknown-linux-musl"
        or not isinstance(entries, list)
        or hashlib.sha256(canonical_json(entries)).hexdigest()
        != TARGET_ENTRIES_SHA256
    ):
        blocked("pinned Rust musl target receipt schema or aggregate differs")
    root = Path("/root/.rustup/toolchains") / PINNED_TOOLCHAIN / value["root_relative"]
    verify_immutable_root_ancestors(root)
    return root, entries


def verify_immutable_root_ancestors(root: Path) -> None:
    current = Path("/")
    for component in root.parts[1:]:
        current /= component
        metadata = current.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_mode & 0o022
        ):
            blocked(f"Rust target ancestor is not immutable root-owned directory: {current}")


def verify_target_tree(root: Path, entries: list[dict[str, object]]) -> None:
    root_metadata = root.lstat()
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or root_metadata.st_uid != 0
        or root_metadata.st_gid != 0
        or root_metadata.st_mode & 0o022
    ):
        blocked("pinned Rust musl target root is not immutable root-owned directory")
    if not all(isinstance(entry, dict) for entry in entries):
        blocked("pinned Rust musl target receipt contains a non-object entry")
    expected = {entry.get("path"): entry for entry in entries}
    if (
        len(expected) != len(entries)
        or not all(
            isinstance(path, str)
            and path
            and not path.startswith("/")
            and ".." not in Path(path).parts
            for path in expected
        )
    ):
        blocked("pinned Rust musl target receipt paths are invalid or duplicate")
    observed: set[str] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        for item in os.scandir(directory):
            path = Path(item.path)
            relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
            record = expected.get(relative)
            if record is None:
                blocked(f"extra pinned Rust musl target entry: {relative}")
            observed.add(relative)
            if stat.S_ISLNK(metadata.st_mode):
                blocked(f"symlink forbidden in pinned Rust musl target: {relative}")
            common = (
                metadata.st_uid == record.get("uid")
                and metadata.st_gid == record.get("gid")
                and f"{stat.S_IMODE(metadata.st_mode):04o}" == record.get("mode")
                and metadata.st_mode & 0o022 == 0
            )
            if record.get("type") == "directory":
                if not stat.S_ISDIR(metadata.st_mode) or not common:
                    blocked(f"pinned Rust musl target directory differs: {relative}")
                pending.append(path)
            elif record.get("type") == "file":
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or not common
                    or metadata.st_nlink != 1
                    or metadata.st_size != record.get("size")
                    or digest(path) != record.get("sha256")
                ):
                    blocked(f"pinned Rust musl target file differs: {relative}")
            else:
                blocked(f"unsupported pinned Rust musl target entry: {relative}")
    missing = set(expected) - observed
    if missing:
        blocked(f"missing pinned Rust musl target entry: {min(missing)}")


def verify_static_x86_64_elf(path: Path) -> None:
    data = path.read_bytes()
    if (
        len(data) < 64
        or data[:4] != b"\x7fELF"
        or data[4] != 2
        or data[5] != 1
        or data[6] != 1
    ):
        raise RuntimeError("broker output is not little-endian ELF64")
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    elf_type, machine, version, program_offset, header_size, program_size, program_count = (
        header[1],
        header[2],
        header[3],
        header[5],
        header[8],
        header[9],
        header[10],
    )
    if (
        elf_type not in (2, 3)
        or machine != 62
        or version != 1
        or header_size != 64
        or program_size != 56
        or program_count == 0
        or program_offset < header_size
    ):
        raise RuntimeError("broker output is not an x86_64 ELF with program headers")
    end = program_offset + program_size * program_count
    if end > len(data):
        raise RuntimeError("broker ELF program header table is out of bounds")
    has_load_segment = False
    for index in range(program_count):
        offset = program_offset + index * program_size
        kind, _, file_offset, _, _, file_size, _, _ = struct.unpack_from(
            "<IIQQQQQQ", data, offset
        )
        if file_offset + file_size > len(data):
            raise RuntimeError("broker ELF segment is out of bounds")
        if kind == 1:
            has_load_segment = True
        if kind == 3:
            raise RuntimeError("broker ELF contains PT_INTERP")
        if kind == 2:
            if file_size % 16:
                raise RuntimeError("broker ELF dynamic segment is malformed")
            terminated = False
            for dynamic_offset in range(file_offset, file_offset + file_size, 16):
                tag, _ = struct.unpack_from("<qQ", data, dynamic_offset)
                if tag == 1:
                    raise RuntimeError("broker ELF contains DT_NEEDED")
                if tag == 0:
                    terminated = True
                    break
            if not terminated:
                raise RuntimeError("broker ELF dynamic segment lacks DT_NULL")
    if not has_load_segment:
        raise RuntimeError("broker ELF has no PT_LOAD segment")


if __name__ == "__main__":
    raise SystemExit(main())
