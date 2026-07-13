#!/usr/bin/env python3
"""Single release-version gate for package metadata, tags and changelog."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def package_version() -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    if not match:
        raise RuntimeError("pyproject project.version is missing")
    return match.group(1)


def verify(ref: str | None = None) -> None:
    version = package_version()
    expected = f"v{version}"
    tag = ref or subprocess.check_output(["git", "describe", "--tags", "--exact-match"], cwd=ROOT, text=True).strip()
    if tag != expected:
        raise RuntimeError(f"release tag {tag!r} must equal package version {expected!r}")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^## {re.escape(expected)}\b", changelog, re.M):
        raise RuntimeError(f"CHANGELOG.md needs a {expected} heading")
    dirty = subprocess.run(["git", "diff", "--quiet"], cwd=ROOT).returncode != 0
    if dirty:
        raise RuntimeError("refusing a dirty release")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--ref", help="release tag when verifying a checked-out immutable ref")
    args = parser.parse_args(argv)
    if not args.verify:
        parser.error("--verify is required")
    verify(args.ref)
    print(f"release version verified: v{package_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
