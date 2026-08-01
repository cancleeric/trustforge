#!/usr/bin/env python3
"""Produce a deterministic shadow receipt for package-scoped test selection."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from build_package_inventory import (
    InventoryError,
    _rules,
    build_inventory,
    classify,
    classify_test,
    glob_matches,
    load_manifest,
)


class SelectionError(ValueError):
    """A safe scoped selection cannot be derived."""


def git_sha(root: Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SelectionError(f"cannot resolve git ref: {ref}")
    return result.stdout.strip()


def merge_base_sha(root: Path, base_sha: str, head_sha: str) -> str:
    result = subprocess.run(
        ["git", "merge-base", base_sha, head_sha],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise SelectionError("cannot resolve merge base")
    return result.stdout.strip()


def require_clean_worktree(root: Path) -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SelectionError("cannot inspect worktree status")
    if result.stdout:
        raise SelectionError("--base selection requires a clean worktree")


def parse_name_status(data: bytes) -> tuple[list[str], list[str]]:
    fields = [field.decode("utf-8", errors="surrogateescape") for field in data.split(b"\0") if field]
    paths: set[str] = set()
    deleted: set[str] = set()
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        path_count = 2 if status.startswith(("R", "C")) else 1
        if index + path_count > len(fields):
            raise SelectionError("malformed git name-status output")
        status_paths = fields[index : index + path_count]
        index += path_count
        paths.update(status_paths)
        if status.startswith("D"):
            deleted.add(status_paths[0])
        elif status.startswith("R"):
            deleted.add(status_paths[0])
    return sorted(paths), sorted(deleted)


def changed_files(root: Path, base: str, head: str) -> tuple[list[str], list[str]]:
    result = subprocess.run(
        [
            "git", "diff", "--name-status", "-z", "--find-renames",
            "--diff-filter=ACDMRTUXB", f"{base}...{head}",
        ],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SelectionError("git diff failed; full-suite fallback is required")
    return parse_name_status(result.stdout)


def reverse_dependents(packages: dict[str, Any], seeds: set[str]) -> set[str]:
    affected = set(seeds)
    changed = True
    while changed:
        changed = False
        for package, definition in packages.items():
            if package not in affected and affected.intersection(definition["depends_on"]):
                affected.add(package)
                changed = True
    return affected


def bridge_aware_dependents(
    manifest: dict[str, Any], entries: dict[str, dict[str, Any]], seeds: set[str]
) -> set[str]:
    """Include consumers created by temporary reverse dependency bridges."""

    module_owners = {
        entry["path"][4:-3].replace("/", ".").removesuffix(".__init__"): entry["owners"][0]
        for entry in entries.values()
        if entry["disposition"] == "owned"
        and entry["path"].startswith("src/")
        and entry["path"].endswith(".py")
    }
    bridge_edges: list[tuple[str, str]] = []
    for bridge in manifest.get("temporary_bridges", []):
        importer_owner = module_owners.get(bridge["importer"])
        imported_owner = module_owners.get(bridge["imported"])
        if importer_owner is None or imported_owner is None:
            raise SelectionError("temporary bridge ownership cannot be resolved")
        bridge_edges.append((importer_owner, imported_owner))

    affected = reverse_dependents(manifest["packages"], seeds)
    changed = True
    while changed:
        changed = False
        for consumer, dependency in bridge_edges:
            if dependency in affected and consumer not in affected:
                affected = reverse_dependents(manifest["packages"], affected | {consumer})
                changed = True
    return affected


def _matches(path: str, patterns: list[str]) -> bool:
    return any(glob_matches(path, pattern) for pattern in patterns)


def select(
    root: Path,
    manifest: dict[str, Any],
    paths: list[str],
    *,
    deleted_paths: set[str] | None = None,
) -> dict[str, Any]:
    inventory = build_inventory(root, manifest)
    entries = {entry["path"]: entry for entry in inventory["entries"]}
    rules = _rules(manifest)
    global_globs = manifest["global_trigger_globs"]
    overrides = manifest["owned_over_global_globs"]
    inventory_globs = manifest["inventory_globs"]
    ignore_globs = manifest["explicit_ignore_globs"]
    seed_packages: set[str] = set()
    reasons: list[dict[str, Any]] = []
    full_suite_reasons: list[str] = []
    deleted_paths = deleted_paths or set()

    for path in sorted(set(paths)):
        if _matches(path, global_globs) and not _matches(path, overrides):
            full_suite_reasons.append(f"global_trigger:{path}")
            reasons.append({"path": path, "disposition": "global_trigger"})
            continue
        if _matches(path, inventory_globs):
            if path in deleted_paths and path.startswith("tests/"):
                full_suite_reasons.append(f"deleted_test:{path}")
            entry = entries.get(path)
            if entry and entry["disposition"] == "owned":
                owners = set(entry["owners"])
            elif path.startswith("tests/") and path.endswith(".py"):
                owners = set(classify_test(path, rules)[0])
            else:
                owners = {classify(path, rules)[0]}
            seed_packages.update(owners)
            reasons.append(
                {"path": path, "disposition": "owned", "owners": sorted(owners)}
            )
            continue
        if _matches(path, ignore_globs):
            reasons.append({"path": path, "disposition": "explicit_ignore"})
            continue
        full_suite_reasons.append(f"unclassified:{path}")
        reasons.append({"path": path, "disposition": "unclassified"})

    affected = bridge_aware_dependents(manifest, entries, seed_packages)
    always_globs = manifest.get("always_run_test_globs", [])
    if not isinstance(always_globs, list) or not all(
        isinstance(pattern, str) and pattern for pattern in always_globs
    ):
        raise SelectionError("always_run_test_globs must be a list of non-empty strings")
    candidate_tests = sorted({
        entry["path"]
        for entry in inventory["entries"]
        if entry["disposition"] == "owned"
        and entry["path"].startswith("tests/")
        and entry["path"].endswith(".py")
        and (
            affected.intersection(entry["owners"])
            or (affected and _matches(entry["path"], always_globs))
        )
    })
    candidate_lanes = sorted(
        lane for lane in ("backend", "frontend", "native")
        if (lane == "backend" and candidate_tests) or lane in affected
    )
    full_suite_required = bool(full_suite_reasons)
    all_backend_tests = sorted(
        entry["path"]
        for entry in inventory["entries"]
        if entry["disposition"] == "owned"
        and entry["path"].startswith("tests/")
        and entry["path"].endswith(".py")
    )
    execution_tests = all_backend_tests if full_suite_required else candidate_tests
    execution_lanes = ["backend", "frontend", "native"] if full_suite_required else candidate_lanes
    return {
        "schema_version": 1,
        "mode": "shadow_only",
        "selection_ready": False,
        "changed_files": sorted(set(paths)),
        "seed_packages": sorted(seed_packages),
        "affected_packages": sorted(affected),
        "shadow_candidate_lanes": candidate_lanes,
        "shadow_candidate_backend_test_count": len(candidate_tests),
        "shadow_candidate_backend_tests": candidate_tests,
        "execution_plan": {
            "mode": "full_suite" if full_suite_required else "scoped_candidate",
            "lanes": execution_lanes,
            "backend_test_count": len(execution_tests),
            "backend_tests": execution_tests,
        },
        "full_suite_required": full_suite_required,
        "full_suite_reasons": sorted(full_suite_reasons),
        "reasons": reasons,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        manifest = load_manifest(args.manifest or root / "qa" / "package-ownership.json")
        paths = list(args.changed_file)
        deleted_paths: set[str] = set()
        vcs: dict[str, str] | None = None
        if args.base:
            require_clean_worktree(root)
            head_sha = git_sha(root, args.head)
            current_sha = git_sha(root, "HEAD")
            if head_sha != current_sha:
                raise SelectionError("--head must resolve to the checked-out HEAD")
            base_sha = git_sha(root, args.base)
            actual_merge_base_sha = merge_base_sha(root, base_sha, head_sha)
            diff_paths, deleted = changed_files(root, args.base, args.head)
            paths.extend(diff_paths)
            deleted_paths.update(deleted)
            vcs = {
                "base_ref": args.base,
                "base_sha": base_sha,
                "merge_base_sha": actual_merge_base_sha,
                "head_ref": args.head,
                "head_sha": head_sha,
                "diff_mode": "merge_base_to_head",
            }
        if not paths and not args.base:
            raise SelectionError("provide --base or at least one --changed-file")
        receipt = select(root, manifest, paths, deleted_paths=deleted_paths)
        if vcs:
            receipt["vcs"] = vcs
    except (InventoryError, SelectionError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"package test selection failed: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
