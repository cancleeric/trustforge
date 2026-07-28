#!/usr/bin/env python3
"""Single release-version gate for package metadata, tags and changelog."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def package_version(root: Path = ROOT) -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    if not match:
        raise RuntimeError("pyproject project.version is missing")
    return match.group(1)


def parse_version(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(value.strip())
    if not match:
        raise RuntimeError(f"invalid semantic version: {value!r}")
    return tuple(int(part) for part in match.groups())


def list_release_tags(root: Path = ROOT, remote: str = "origin") -> list[str]:
    output = subprocess.check_output(
        ["git", "tag", "--list", "v[0-9]*.[0-9]*.[0-9]*"],
        cwd=root,
        text=True,
    )
    tags = {tag for tag in output.splitlines() if VERSION_PATTERN.fullmatch(tag)}
    remote_output = subprocess.check_output(
        ["git", "ls-remote", "--tags", remote, "refs/tags/v*"],
        cwd=root,
        text=True,
    )
    for line in remote_output.splitlines():
        ref = line.rsplit(maxsplit=1)[-1]
        tag = ref.removeprefix("refs/tags/").removesuffix("^{}")
        if VERSION_PATTERN.fullmatch(tag):
            tags.add(tag)
    return sorted(tags, key=parse_version)


def highest_release_version(tags: list[str]) -> tuple[int, int, int]:
    if not tags:
        return (0, 0, 0)
    return max(parse_version(tag) for tag in tags)


def bumped_version(current: tuple[int, int, int], level: str) -> str:
    major, minor, patch = current
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise RuntimeError(f"unsupported bump level: {level}")


def _replace_once(path: Path, pattern: str, replacement: str) -> None:
    original = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, original, count=1, flags=re.M)
    if count != 1:
        raise RuntimeError(f"expected exactly one version field in {path}")
    path.write_text(updated, encoding="utf-8")


def update_version_files(version: str, root: Path = ROOT) -> None:
    parse_version(version)
    _replace_once(root / "pyproject.toml", r'^version\s*=\s*"[^"]+"', f'version = "{version}"')
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    _replace_once(root / "src/trustforge/__init__.py", r'^__version__\s*=\s*"[^"]+"', f'__version__ = "{version}"')
    _replace_once(root / "src/trustforge/_version.py", r'^VERSION\s*=\s*"[^"]+"', f'VERSION = "{version}"')

    package_path = root / "frontend/package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["version"] = version
    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lock_path = root / "frontend/package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["version"] = version
    root_package = lock.get("packages", {}).get("")
    if not isinstance(root_package, dict):
        raise RuntimeError("frontend/package-lock.json is missing packages['']")
    root_package["version"] = version
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    changelog_path = root / "CHANGELOG.md"
    changelog = changelog_path.read_text(encoding="utf-8")
    heading = f"## v{version}"
    if not re.search(rf"^{re.escape(heading)}\b", changelog, re.M):
        marker = "# Changelog\n"
        if marker not in changelog:
            raise RuntimeError("CHANGELOG.md is missing its title")
        entry = f"\n## v{version} — {dt.date.today().isoformat()}\n\n- release: automatic version preparation.\n"
        changelog_path.write_text(changelog.replace(marker, marker + entry, 1), encoding="utf-8")

    notes_path = root / f"docs/RELEASE-NOTES-v{version}.md"
    if not notes_path.exists():
        notes_path.write_text(
            f"# TrustForge v{version}\n\n"
            f"Release date: {dt.date.today().isoformat()}\n\n"
            "## Highlights\n\n- Release notes pending final review.\n\n"
            "## Verification\n\n- Pre-push, review, release, deployment, and rollback evidence pending.\n",
            encoding="utf-8",
        )


def version_sources(root: Path = ROOT) -> dict[str, str]:
    package = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "frontend/package-lock.json").read_text(encoding="utf-8"))
    init_match = re.search(
        r'^__version__\s*=\s*"([^"]+)"',
        (root / "src/trustforge/__init__.py").read_text(encoding="utf-8"),
        re.M,
    )
    module_match = re.search(
        r'^VERSION\s*=\s*"([^"]+)"',
        (root / "src/trustforge/_version.py").read_text(encoding="utf-8"),
        re.M,
    )
    if not init_match or not module_match:
        raise RuntimeError("runtime version source is missing")
    return {
        "pyproject.toml": package_version(root),
        "VERSION": (root / "VERSION").read_text(encoding="utf-8").strip(),
        "src/trustforge/__init__.py": init_match.group(1),
        "src/trustforge/_version.py": module_match.group(1),
        "frontend/package.json": str(package.get("version", "")),
        "frontend/package-lock.json": str(lock.get("version", "")),
        "frontend/package-lock.json packages['']": str(lock.get("packages", {}).get("", {}).get("version", "")),
    }


def verify(ref: str | None = None, root: Path = ROOT) -> None:
    version = package_version(root)
    expected = f"v{version}"
    mismatches = {source: value for source, value in version_sources(root).items() if value != version}
    if mismatches:
        raise RuntimeError(f"release version sources disagree with {version}: {mismatches}")
    tag = ref or subprocess.check_output(["git", "describe", "--tags", "--exact-match"], cwd=root, text=True).strip()
    if tag != expected:
        raise RuntimeError(f"release tag {tag!r} must equal package version {expected!r}")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^## {re.escape(expected)}\b", changelog, re.M):
        raise RuntimeError(f"CHANGELOG.md needs a {expected} heading")
    notes = root / f"docs/RELEASE-NOTES-{expected}.md"
    if not notes.is_file():
        raise RuntimeError(f"release notes are missing: {notes.relative_to(root)}")
    dirty = subprocess.run(["git", "diff", "--quiet"], cwd=root).returncode != 0
    if dirty:
        raise RuntimeError("refusing a dirty release")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--ref", help="release tag when verifying a checked-out immutable ref")
    parser.add_argument("--bump", choices=("patch", "minor", "major"))
    args = parser.parse_args(argv)
    if args.verify == bool(args.bump):
        parser.error("choose exactly one of --verify or --bump")
    if args.bump:
        tags = list_release_tags()
        previous = highest_release_version(tags)
        version = bumped_version(previous, args.bump)
        update_version_files(version)
        print(f"release version updated: v{version} (from v{'.'.join(map(str, previous))})")
        return 0
    verify(args.ref)
    print(f"release version verified: v{package_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
