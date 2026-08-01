#!/usr/bin/env python3
"""Reproducible musl builder for NF2/NF3 (NR1b PR-B3, issue #1076 acceptance #3).

This script cross-builds the NF2 and NF3 crates for ``x86_64-unknown-linux-musl``
twice in two independent clean target directories and asserts the evidence rlib,
release rlib and release binary artifacts are byte-identical across the two
builds. It then runs the static-ELF assertions (ET_EXEC / no PT_INTERP /
no DT_NEEDED) on the NF2 and NF3 release binaries.

Reproducibility knobs (RUSTFLAGS, SOURCE_DATE_EPOCH, incremental/tz/locale env)
are copied from the hermetic NF1 builder
(``scripts/build_native_hermetic_package.py``). The single intentional
divergence from NF1 is the ``--remap-path-prefix`` scope: NF1 remaps the copied
crate input directory, this builder remaps the whole source root so that nf3's
path dependencies on nf2 and native-sys collapse to a single canonical
``/workspace/trustforge`` prefix.

Toolchain pinning (PATH-shadow defense, codex re-review P1): cargo/rustc are
resolved to ABSOLUTE paths at startup via ``shutil.which`` and (on rustup hosts)
``rustup which --toolchain`` to dereference the rustup proxy — the cargo
subprocess env pins PATH to the resolved toolchain bin dir ONLY, sets RUSTC to
the resolved absolute rustc, and invokes cargo by absolute path; a startup
version assertion fails unless the resolved rustc reports ``release: 1.96.0``.
So a shadow ``cargo``/``rustc`` planted earlier in PATH cannot silently drive
both passes (it either trips the version assertion or, if it lies about its
version, surfaces as a non-toolchain resolved path in the receipt).

HONESTY / ISOLATION GRADE: this builder adopts the NF1 *style* (named-key env
closure + absolute-toolchain pinning + limited PATH), but the isolation is
DEV-GRADE, version-level — it is NOT NF1's release-grade sha256 /
toolchain-lock pinning. Accordingly this B3 L1 gate is NOT release-authoritative;
the authoritative release path is the .83 L2 two-clone gate plus the NF1
hermetic pipeline. The per-build subprocess env is built from an explicit dict
(never ``{**os.environ}``), ``CARGO_HOME`` points at a fresh empty tempdir, and
wrapper/rustflags-override knobs are provably absent — so host ``cargo test``
runs and any host sccache/wrapper config are untouched and cannot fake
byte-identity by sharing a wrapper cache across the two passes.

Stdlib-only; no third-party imports. Receipt schema intentionally carries no
signer/auth/release/eligibility authority metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# --- Reproducibility constants (copied byte-for-byte from NF1 builder) -------
# scripts/build_native_hermetic_package.py: TARGET/EPOCH (L25-26) and the env
# block at L1183-1200 (RUSTUP_TOOLCHAIN/SOURCE_DATE_EPOCH/TZ/LC_ALL/LANG/
# CARGO_INCREMENTAL/CARGO_NET_OFFLINE/CARGO_TARGET_..._LINKER/RUSTFLAGS).
TARGET = "x86_64-unknown-linux-musl"
EPOCH = 1_700_000_000
TOOLCHAIN = "1.96.0"
# CEO decision (PR-B3 item 1): remap the entire source root, not a single crate,
# so nf3 -> nf2/native-sys cross-crate paths all collapse to one prefix.
REMAP_TARGET = "/workspace/trustforge"

# RUSTFLAGS flag sequence copied verbatim from NF1 (L1197-1199); only the
# --remap-path-prefix source operand is widened to the source root.
RUSTFLAGS_TEMPLATE = (
    "--remap-path-prefix={source_root}=" + REMAP_TARGET
    + " -C relocation-model=static -C link-arg=--build-id=none"
    + " -C link-arg=-no-pie"
)

SOURCE_ROOT = Path(__file__).resolve().parents[1]
NF2_HARNESS = SOURCE_ROOT / "scripts" / "test_nf2_zero_capability_linux.py"

# Crate -> manifest + artifact basenames. nf2's release-eligible main binary is
# the package-name binary from src/main.rs; nf3's release-eligible binary is
# nf3_profile_probe (src/bin/nf3_profile_probe.rs, not feature-gated).
CRATES = {
    "nf2": {
        "manifest": SOURCE_ROOT / "native" / "nf2-zero-capability-broker" / "Cargo.toml",
        "rlib": "libtrustforge_nf2_zero_capability_broker.rlib",
        "release_bin": "trustforge-nf2-zero-capability-broker",
    },
    "nf3": {
        "manifest": SOURCE_ROOT / "native" / "nf3-one-shot-transaction" / "Cargo.toml",
        "rlib": "libtrustforge_nf3_one_shot_transaction.rlib",
        "release_bin": "nf3_profile_probe",
    },
}
PROFILES = ("evidence", "release")
PASSES = ("A", "B")

# --- Hermetic env isolation (mirrors NF1 builder discipline) ----------------
# The NF1 builder (scripts/build_native_hermetic_package.py) constructs an
# explicit-dict env (L1183-1201) — never ``{**os.environ}`` — points
# CARGO_HOME at a fresh empty tempdir so ambient ``~/.cargo/config.toml``
# cannot inject build.rustflags / build.rustc-wrapper, pins PATH to the resolved
# toolchain bin dir only (L1184) and RUSTC to the resolved absolute rustc
# (L1187), and rejects any ancestor ``.cargo/config[.toml]`` of the build root
# (L1044-1051). This builder mirrors that discipline (dev-grade: version-level).

# Reproducibility/correctness knobs that must NEVER reach the cargo subprocess
# from the host. If any leak in, two builds can share a wrapper cache (sccache
# via RUSTC_WRAPPER* -> fake byte-identical that hides timestamp/path/order
# drift) or silently override the pinned RUSTFLAGS (CARGO_ENCODED_RUSTFLAGS /
# CARGO_BUILD_RUSTFLAGS make cargo ignore RUSTFLAGS). The receipt records each
# key's ACTUAL value in the subprocess env (null == absent == isolated), so a
# contaminated run is visible in the receipt. (RUSTC is intentionally NOT in
# this list: this builder pins it itself to the resolved absolute rustc.)
_ENV_REJECTED = (
    "RUSTC_WRAPPER",
    "RUSTC_WORKSPACE_WRAPPER",
    "CARGO_BUILD_RUSTC_WRAPPER",
    "CARGO_ENCODED_RUSTFLAGS",
    "CARGO_BUILD_RUSTFLAGS",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_version(argv: list[str]) -> str:
    return subprocess.check_output(argv, text=True).strip()


def _resolve_tool(name: str) -> Path:
    """Resolve a host tool to a real absolute path, dereferencing rustup proxies.

    On rustup-managed hosts ``cargo``/``rustc`` are hardlinks to the ``rustup``
    proxy; invoking the proxy still re-resolves the toolchain at runtime from
    PATH, which defeats absolute-toolchain pinning. Mirrors NF1
    (``_resolve_tool``, build_native_hermetic_package.py:685-710): when the
    candidate resolves to ``rustup``, ask rustup for the real binary behind the
    pinned 1.96.0 toolchain. A shadow ``cargo``/``rustc`` planted earlier in
    PATH resolves here directly (its name is not ``rustup``); the startup
    version assertion in ``main`` then catches it. Fails fast if unavailable.
    """
    candidate = shutil.which(name)
    if candidate is None:
        raise SystemExit(
            f"[nf-reproducible] FAILED: required tool unavailable on host PATH: {name}"
        )
    candidate_path = Path(candidate)
    if candidate_path.resolve().name == "rustup" and name in {"cargo", "rustc"}:
        rustup = shutil.which("rustup")
        if rustup is None:
            raise SystemExit(
                "[nf-reproducible] FAILED: cargo/rustc resolve to the rustup "
                "proxy but rustup itself is not resolvable"
            )
        resolved = subprocess.run(
            [rustup, "which", "--toolchain", TOOLCHAIN, name],
            check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if resolved.returncode != 0:
            raise SystemExit(
                f"[nf-reproducible] FAILED: pinned {TOOLCHAIN} toolchain cannot "
                f"resolve {name}: {resolved.stderr.strip()}"
            )
        return Path(resolved.stdout.strip()).resolve()
    return candidate_path.resolve()


def _find_rust_lld(rustc: Path) -> Path:
    """Locate the resolved toolchain's rust-lld from ``rustc --print sysroot``."""
    sysroot = Path(_tool_version([str(rustc), "--print", "sysroot"]))
    host = _tool_version([str(rustc), "-vV"]).split("host: ", 1)[1].splitlines()[0]
    candidate = sysroot / "lib" / "rustlib" / host / "bin" / "rust-lld"
    if not candidate.exists():
        raise SystemExit(
            f"[nf-reproducible] FAILED: rust-lld not found for host {host!r}: {candidate}"
        )
    return candidate


def _reject_ancestor_cargo_config(path: Path) -> None:
    """Forbid ambient ancestor ``.cargo/config[.toml]`` above the build root.

    Cargo walks the working directory and every parent for
    ``.cargo/config[.toml]`` and applies ``build.rustflags`` /
    ``build.rustc-wrapper`` from them. A fresh empty CARGO_HOME tempdir already
    neutralizes ``~/.cargo/config.toml``; this guard neutralizes any config
    above the source root too. Mirrors NF1 builder L1044-1051. (Member crate
    ``.cargo/config.toml`` files live below the source root and are not
    ancestors of the build cwd, so they are unaffected.)
    """
    for ancestor in (path, *path.parents):
        cargo_dir = ancestor / ".cargo"
        for name in ("config", "config.toml"):
            if (cargo_dir / name).exists():
                raise RuntimeError(
                    f"ambient ancestor Cargo config is forbidden: {cargo_dir / name}"
                )


def _build_env(
    cargo: Path, rustc: Path, rust_lld: Path, cargo_home: str
) -> dict[str, str]:
    """Closed env injected per-build (never written to os.environ).

    Mirrors the hermetic NF1 builder (L1183-1201): the subprocess env is built
    from an explicit dict, NOT ``{**os.environ}`` — nothing is inherited from
    the host. PATH is pinned to the resolved toolchain bin dir only (NF1 L1184),
    RUSTC is the resolved absolute rustc (NF1 L1187), HOME is an isolated
    tempdir (NF1 sets a non-host HOME), and ``CARGO_HOME`` points at a fresh
    empty tempdir (``cargo_home``) so ambient ``~/.cargo/config.toml`` cannot
    inject ``build.rustflags`` or ``build.rustc-wrapper``. Because cargo is
    invoked by absolute path and rustc via the absolute RUSTC, a shadow
    ``cargo``/``rustc`` earlier in the host PATH cannot reach the subprocess.
    Every knob in ``_ENV_REJECTED`` is provably absent from the returned env.
    """
    return {
        "PATH": str(cargo.parent),
        "HOME": cargo_home,
        "CARGO_HOME": cargo_home,
        "RUSTC": str(rustc),
        "RUSTUP_TOOLCHAIN": TOOLCHAIN,
        "SOURCE_DATE_EPOCH": str(EPOCH),
        "CARGO_INCREMENTAL": "0",
        "TZ": "UTC",
        "LC_ALL": "C",
        "LANG": "C",
        "CARGO_NET_OFFLINE": "true",
        "CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER": str(rust_lld),
        "RUSTFLAGS": RUSTFLAGS_TEMPLATE.format(source_root=SOURCE_ROOT),
    }


def _env_isolation(env: dict[str, str], cargo_home: str) -> dict[str, object]:
    """Receipt block proving the rejected knobs are absent from the subprocess env.

    ``rejected_absent`` maps each ``_ENV_REJECTED`` key to its ACTUAL value in the
    subprocess env (``null`` == absent == isolated). On a contaminated host (e.g.
    ``RUSTC_WRAPPER=/bin/echo``) this block surfaces the leak instead of hiding
    it. ``subprocess_path`` / ``subprocess_home`` record the actual isolated
    PATH (toolchain bin dir) and HOME so a non-toolchain resolved path is
    visible. Mirrors the NF1 ``WRAPPER_AND_AMBIENT_KNOBS: rejected:not-in-
    subprocess-environment`` receipt pattern.
    """
    return {
        "rejected_absent": {key: env.get(key) for key in _ENV_REJECTED},
        "cargo_home": "isolated:fresh-empty-cargo-home",
        "cargo_home_path": cargo_home,
        "subprocess_path": env.get("PATH"),
        "subprocess_home": env.get("HOME"),
        "allowlist": sorted(env.keys()),
    }


def _toolchain_receipt(
    cargo: Path, rustc: Path, rust_lld: Path,
    rustc_verbose: str, cargo_version: str,
) -> dict[str, object]:
    """Receipt block making the resolved toolchain visible (PATH-shadow defense).

    Records the resolved ABSOLUTE paths of cargo/rustc/rust-lld, the actual
    ``rustc --version --verbose`` output, and the pinning grade. A shadow
    toolchain that slipped past the version assertion (e.g. one that lies about
    its version) shows up here as a non-toolchain resolved path, so a reviewer
    reading the receipt can catch it.
    """
    return {
        "resolved_cargo": str(cargo),
        "resolved_rustc": str(rustc),
        "resolved_rust_lld": str(rust_lld),
        "rustc_version_verbose": rustc_verbose,
        "cargo_version": cargo_version,
        "pinned_toolchain": TOOLCHAIN,
        "version_asserted_release": TOOLCHAIN,
        "pinning_grade": (
            "dev-grade: version-level (rustc --version --verbose asserts "
            f"release {TOOLCHAIN}); NOT sha256/toolchain-lock "
            "(NF1 release-grade). PATH pinned to the resolved toolchain bin dir."
        ),
    }


def _run_cargo(
    cargo: Path, manifest: Path, profile: str, target_dir: Path,
    env: dict[str, str],
) -> None:
    args = [
        str(cargo), "build",
        "--manifest-path", str(manifest),
        "--target", TARGET,
        "--target-dir", str(target_dir),
        "--locked", "--offline", "--frozen",
    ]
    if profile == "release":
        args.append("--release")
    else:
        args += ["--profile", profile]
    subprocess.run(args, cwd=str(SOURCE_ROOT), env=env, check=True)


def _artifact_path(target_dir: Path, profile: str, name: str) -> Path:
    return target_dir / TARGET / profile / name


def _load_verify_static():
    """Reuse verify_static_x86_64_elf from the NF2 harness (no duplication)."""
    spec = importlib.util.spec_from_file_location(
        "_nf2_harness_for_b3", NF2_HARNESS
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_static_x86_64_elf


def _artifact_plan(crate: str) -> list[tuple[str, str]]:
    """(profile, basename) pairs to hash for a crate."""
    info = CRATES[crate]
    plan = [("evidence", info["rlib"]), ("release", info["rlib"])]
    if info.get("release_bin"):
        plan.append(("release", info["release_bin"]))
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--double-build", action=argparse.BooleanOptionalAction, default=True,
        help="run two clean builds and assert byte-identical artifacts (default on).",
    )
    parser.add_argument(
        "--verify-static", action=argparse.BooleanOptionalAction, default=True,
        help="run static-ELF assertions on nf2/nf3 release binaries (default on).",
    )
    parser.add_argument(
        "--out", default="out/nf-reproducible/receipt.json",
        help="receipt JSON path (default: out/nf-reproducible/receipt.json).",
    )
    args = parser.parse_args()

    # --- Toolchain pinning (PATH-shadow defense, codex re-review P1) --------
    # Resolve cargo/rustc to ABSOLUTE paths once, at startup, from the host PATH
    # (dereferencing the rustup proxy on rustup hosts — mirrors NF1 _resolve_tool
    # L685-710). The cargo subprocess later gets PATH = the toolchain bin dir
    # only and RUSTC = the absolute rustc, so a shadow toolchain planted earlier
    # in PATH cannot drive the build.
    rustc = _resolve_tool("rustc")
    cargo = _resolve_tool("cargo")
    # Version assertion (dev-grade pin): the resolved rustc must report the
    # pinned release. A shadow rustc that prints a different version trips this
    # and fails the build before any artifact is produced.
    rustc_verbose = _tool_version([str(rustc), "--version", "--verbose"])
    if f"release: {TOOLCHAIN}" not in rustc_verbose:
        raise SystemExit(
            "[nf-reproducible] FAILED: resolved rustc is not the pinned "
            f"{TOOLCHAIN} toolchain: {rustc} -> "
            f"{rustc_verbose.splitlines()[0]}"
        )
    cargo_version = _tool_version([str(cargo), "--version"])
    rust_lld = _find_rust_lld(rustc)
    _reject_ancestor_cargo_config(SOURCE_ROOT)

    receipt: dict = {
        "schema": "trustforge.nf-reproducible-builder/v1",
        "build": {
            "host": platform.node(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "source_root": str(SOURCE_ROOT),
            "platform": platform.platform(),
            "rustc": rustc_verbose.splitlines()[0],
            "cargo": cargo_version,
        },
        "toolchain": _toolchain_receipt(
            cargo, rustc, rust_lld, rustc_verbose, cargo_version
        ),
        "reproducibility": {
            "source_date_epoch": EPOCH,
            "remap_target": REMAP_TARGET,
            "rustup_toolchain": TOOLCHAIN,
            "rust_lld": str(rust_lld),
            "rustflags": RUSTFLAGS_TEMPLATE.format(source_root=SOURCE_ROOT),
            "env": {
                "SOURCE_DATE_EPOCH": str(EPOCH),
                "CARGO_INCREMENTAL": "0",
                "TZ": "UTC",
                "LC_ALL": "C",
                "LANG": "C",
                "CARGO_NET_OFFLINE": "true",
                "RUSTUP_TOOLCHAIN": TOOLCHAIN,
                "CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER": str(rust_lld),
                "PATH": str(cargo.parent),
                "RUSTC": str(rustc),
                "HOME": "isolated:fresh-empty-cargo-home",
            },
            "target": TARGET,
        },
        "double_build": {},
        "verify_static": {},
        "all_byte_identical": None,
        "verify_static_passed": None,
        "env_isolation": {},
    }

    double_build_passed: bool | None = None  # None == not run (honest; P2)
    verify_static_passed: bool | None = None  # None == not run (honest; P2)

    # CARGO_HOME is a fresh empty tempdir shared by both passes. nf2/nf3 depend
    # only on path crates + stdlib (no registry crates), so --frozen --offline
    # resolves without any registry cache. The empty tempdir also guarantees no
    # ambient ~/.cargo/config.toml can inject build.rustflags / rustc-wrapper,
    # and isolates any cargo scratch writes from the host cargo home.
    with tempfile.TemporaryDirectory(prefix="nf-repro-cargo-home-") as cargo_home:
        build_env = _build_env(cargo, rustc, rust_lld, cargo_home)
        receipt["reproducibility"]["env"]["CARGO_HOME"] = (
            "isolated:fresh-empty-cargo-home"
        )
        receipt["env_isolation"] = _env_isolation(build_env, cargo_home)

        if args.double_build:
            double_build_passed = True
            # Hash artifacts built in two fully independent clean target dirs.
            hashes: dict[str, dict[str, str]] = {"A": {}, "B": {}}
            with tempfile.TemporaryDirectory(prefix="nf-repro-") as tmp:
                target_dirs = {p: Path(tmp) / p for p in PASSES}
                for pass_label, target_dir in target_dirs.items():
                    for crate in CRATES:
                        for profile in PROFILES:
                            _run_cargo(
                                cargo, CRATES[crate]["manifest"],
                                profile, target_dir, build_env,
                            )
                    for crate in CRATES:
                        for profile, name in _artifact_plan(crate):
                            key = f"{crate}/{profile}/{name}"
                            hashes[pass_label][key] = _sha256(
                                _artifact_path(target_dir, profile, name)
                            )

                for crate in CRATES:
                    receipt["double_build"][crate] = {}
                    for profile, name in _artifact_plan(crate):
                        key = f"{profile}/{name}"
                        a = hashes["A"][f"{crate}/{key}"]
                        b = hashes["B"][f"{crate}/{key}"]
                        identical = a == b
                        double_build_passed = double_build_passed and identical
                        receipt["double_build"][crate][key] = {
                            "pass_a_sha256": a,
                            "pass_b_sha256": b,
                            "byte_identical": identical,
                        }

                if args.verify_static:
                    verify_static_passed = True
                    verify_static_fn = _load_verify_static()
                    verify_targets = {
                        "nf2_release_bin": (
                            "nf2",
                            CRATES["nf2"]["release_bin"],
                        ),
                        "nf3_release_bin": (
                            "nf3",
                            CRATES["nf3"]["release_bin"],
                        ),
                    }
                    # Verify the pass-A release binaries (byte-identical to B).
                    base = target_dirs["A"]
                    for label, (crate, bin_name) in verify_targets.items():
                        bin_path = _artifact_path(base, "release", bin_name)
                        ok = True
                        detail = ""
                        try:
                            verify_static_fn(bin_path)
                        except Exception as exc:  # noqa: BLE001 - report any failure
                            ok = False
                            detail = f"{type(exc).__name__}: {exc}"
                        verify_static_passed = verify_static_passed and ok
                        receipt["verify_static"][label] = {
                            "crate": crate,
                            "path": str(bin_path.relative_to(SOURCE_ROOT))
                            if str(bin_path).startswith(str(SOURCE_ROOT))
                            else str(bin_path),
                            "sha256": hashes["A"][f"{crate}/release/{bin_name}"],
                            "passed": ok,
                            "detail": detail,
                        }
        else:
            receipt["double_build"] = "skipped (--no-double-build)"

    # P2 honesty: never claim byte-identical / static-ok without actually running.
    receipt["all_byte_identical"] = (
        double_build_passed if double_build_passed is not None else "skipped"
    )
    receipt["verify_static_passed"] = (
        verify_static_passed if verify_static_passed is not None else "skipped"
    )
    receipt["build"]["finished_at"] = datetime.now(timezone.utc).isoformat()

    out_path = (SOURCE_ROOT / args.out) if not os.path.isabs(args.out) else Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")

    # ok: a REQUESTED check must have actually run AND passed. Opting out of a
    # check (via --no-*) is allowed and records "skipped"; a requested check
    # that did not run (e.g. --verify-static without --double-build, which has
    # no built binaries to assert) is a failure, never a silent pass.
    ok = True
    if args.double_build:
        ok = ok and double_build_passed is True
    if args.verify_static:
        ok = ok and verify_static_passed is True
    if not ok:
        print(
            "[nf-reproducible] FAILED: "
            f"double_build={receipt['all_byte_identical']} "
            f"verify_static={receipt['verify_static_passed']}",
            file=sys.stderr,
        )
    else:
        print(f"[nf-reproducible] OK -> {out_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
