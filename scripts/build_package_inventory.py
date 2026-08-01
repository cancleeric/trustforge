#!/usr/bin/env python3
"""Build a deterministic ownership inventory for future scoped test selection."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any


class InventoryError(ValueError):
    """The ownership manifest cannot classify the repository safely."""


@dataclass(frozen=True)
class Rule:
    owner: str
    priority: int
    glob: str


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise InventoryError("unsupported package ownership schema_version")
    packages = manifest.get("packages")
    if not isinstance(packages, dict) or not packages:
        raise InventoryError("packages must be a non-empty object")
    if not all(isinstance(package, str) and package for package in packages):
        raise InventoryError("package names must be non-empty strings")
    validate_dependency_graph(packages)
    return manifest


def validate_dependency_graph(packages: dict[str, Any]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(package: str) -> None:
        if package in visiting:
            raise InventoryError(f"package dependency cycle includes {package}")
        if package in visited:
            return
        definition = packages.get(package)
        if not isinstance(definition, dict):
            raise InventoryError(f"invalid package definition: {package}")
        dependencies = definition.get("depends_on", [])
        if not isinstance(dependencies, list) or not all(
            isinstance(dependency, str) and dependency for dependency in dependencies
        ):
            raise InventoryError(f"depends_on must be a list of package names: {package}")
        visiting.add(package)
        for dependency in dependencies:
            if dependency not in packages:
                raise InventoryError(f"unknown dependency {dependency!r} for {package}")
            visit(dependency)
        visiting.remove(package)
        visited.add(package)

    for package in packages:
        visit(package)


def _rules(manifest: dict[str, Any]) -> list[Rule]:
    packages = manifest["packages"]
    result: list[Rule] = []
    for raw in manifest.get("rules", []):
        if not isinstance(raw, dict):
            raise InventoryError("every rule must be an object")
        try:
            owner, priority, glob = raw["owner"], raw["priority"], raw["glob"]
        except KeyError as exc:
            raise InventoryError("every rule requires owner, integer priority, and glob") from exc
        if (
            not isinstance(owner, str)
            or type(priority) is not int
            or not isinstance(glob, str)
            or not glob
        ):
            raise InventoryError("every rule requires owner, integer priority, and glob")
        rule = Rule(owner=owner, priority=priority, glob=glob)
        if rule.owner not in packages:
            raise InventoryError(f"rule uses unknown owner: {rule.owner}")
        result.append(rule)
    if not result:
        raise InventoryError("rules must not be empty")
    return result


def glob_matches(path: str, pattern: str) -> bool:
    """Match slash-delimited paths; only ** may cross directory boundaries."""

    path_parts = path.split("/")
    pattern_parts = pattern.split("/")

    def match(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        part = pattern_parts[pattern_index]
        if part == "**":
            return match(path_index, pattern_index + 1) or (
                path_index < len(path_parts) and match(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatchcase(path_parts[path_index], part)
            and match(path_index + 1, pattern_index + 1)
        )

    return match(0, 0)


def classify(path: str, rules: list[Rule]) -> tuple[str, str]:
    matches = [rule for rule in rules if glob_matches(path, rule.glob)]
    if not matches:
        raise InventoryError(f"unclassified path: {path}")
    top_priority = max(rule.priority for rule in matches)
    winners = [rule for rule in matches if rule.priority == top_priority]
    owners = {rule.owner for rule in winners}
    if len(owners) != 1:
        detail = ", ".join(sorted(f"{rule.owner}:{rule.glob}" for rule in winners))
        raise InventoryError(f"multiply-owned path at priority {top_priority}: {path}: {detail}")
    winner = sorted(winners, key=lambda rule: rule.glob)[0]
    return winner.owner, winner.glob


def classify_test(path: str, rules: list[Rule]) -> tuple[list[str], list[str]]:
    matches = [rule for rule in rules if glob_matches(path, rule.glob)]
    if not matches:
        raise InventoryError(f"unclassified path: {path}")
    specific = [rule for rule in matches if rule.priority > 10]
    selected = specific or [rule for rule in matches if rule.priority == 10]
    owners = sorted({rule.owner for rule in selected})
    return owners, sorted({rule.glob for rule in selected})


def _module_name(path: str) -> str | None:
    if not path.startswith("src/") or not path.endswith(".py"):
        return None
    parts = path[4:-3].split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def infer_test_owners(
    root: Path, path: str, module_owners: dict[str, str]
) -> tuple[set[str], set[str]]:
    tree = ast.parse((root / path).read_text(encoding="utf-8"), filename=path)
    candidates: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            candidates.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            candidates.add(node.module)
            candidates.update(f"{node.module}.{alias.name}" for alias in node.names)
    owners: set[str] = set()
    evidence: set[str] = set()
    for candidate in candidates:
        current = candidate
        while current:
            owner = module_owners.get(current)
            if owner:
                owners.add(owner)
                evidence.add(f"import:{current}")
                break
            current = current.rpartition(".")[0]
    return owners, evidence


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise InventoryError("git ls-files failed; tracked-file inventory is required")
    return sorted(
        path.decode("utf-8") for path in result.stdout.split(b"\0") if path
    )


def _string_globs(manifest: dict[str, Any], key: str, *, allow_empty: bool = False) -> list[str]:
    value = manifest.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise InventoryError(f"{key} must be a list of non-empty strings")
    if not value and not allow_empty:
        raise InventoryError(f"{key} must not be empty")
    return value


def build_inventory(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    inventory_globs = _string_globs(manifest, "inventory_globs")
    global_globs = _string_globs(manifest, "global_trigger_globs")
    owned_over_global_globs = _string_globs(manifest, "owned_over_global_globs")
    ignore_globs = _string_globs(manifest, "explicit_ignore_globs")
    rules = _rules(manifest)
    paths = tracked_files(root)
    module_owners: dict[str, str] = {}
    for path in paths:
        module = _module_name(path)
        if module and any(glob_matches(path, pattern) for pattern in inventory_globs):
            owner, _ = classify(path, rules)
            module_owners[module] = owner
    entries = []
    counts = {package: 0 for package in manifest["packages"]}
    disposition_counts = {"owned": 0, "global_trigger": 0, "explicit_ignore": 0}
    for path in paths:
        is_inventory = any(glob_matches(path, pattern) for pattern in inventory_globs)
        is_global = any(glob_matches(path, pattern) for pattern in global_globs)
        owned_override = any(
            glob_matches(path, pattern) for pattern in owned_over_global_globs
        )
        if is_global and not owned_override:
            disposition_counts["global_trigger"] += 1
            entries.append({"path": path, "disposition": "global_trigger"})
            continue
        if is_inventory:
            if path.startswith("tests/") and path.endswith(".py"):
                owners, matched_rules = classify_test(path, rules)
                if (root / path).is_file():
                    inferred_owners, import_evidence = infer_test_owners(
                        root, path, module_owners
                    )
                else:
                    inferred_owners, import_evidence = set(), {"deleted:test"}
                owners = sorted(set(owners) | inferred_owners)
                matched_rules = sorted(set(matched_rules) | import_evidence)
            else:
                owner, matched_rule = classify(path, rules)
                owners, matched_rules = [owner], [matched_rule]
            for owner in owners:
                counts[owner] += 1
            disposition_counts["owned"] += 1
            entries.append(
                {
                    "path": path,
                    "disposition": "owned",
                    "owners": owners,
                    "rules": matched_rules,
                }
            )
            continue
        if any(glob_matches(path, pattern) for pattern in ignore_globs):
            disposition_counts["explicit_ignore"] += 1
            entries.append({"path": path, "disposition": "explicit_ignore"})
            continue
        raise InventoryError(f"tracked path has no ownership policy: {path}")
    if not entries:
        raise InventoryError("tracked-file inventory must not be empty")
    empty_packages = sorted(package for package, count in counts.items() if count == 0)
    if empty_packages:
        raise InventoryError(f"packages have no owned files: {', '.join(empty_packages)}")
    return {
        "schema_version": 1,
        "mode": "inventory_shadow_only",
        "selection_ready": False,
        "manifest_schema_version": manifest["schema_version"],
        "counts": counts,
        "disposition_counts": disposition_counts,
        "entries": entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    manifest_path = args.manifest or root / "qa" / "package-ownership.json"
    try:
        receipt = build_inventory(root, load_manifest(manifest_path))
    except (InventoryError, json.JSONDecodeError, OSError) as exc:
        print(f"package inventory failed: {exc}", file=sys.stderr)
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
