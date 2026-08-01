#!/usr/bin/env python3
"""Enforce the package dependency DAG declared by package-ownership.json."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from build_package_inventory import Rule, classify, glob_matches, load_manifest


class BoundaryError(ValueError):
    """The package boundary cannot be proven safe."""


@dataclass(frozen=True)
class ImportEdge:
    importer: str
    imported: str
    lineno: int
    symbol_fallback: bool = False


@dataclass(frozen=True)
class Bridge:
    importer: str
    imported: str
    issue: str


def module_name(path: Path, src_root: Path) -> str:
    relative = path.relative_to(src_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def resolve_from_import(
    importer: str, level: int, module: str | None, *, importer_is_package: bool = False
) -> str:
    if level == 0:
        return module or ""
    package_parts = importer.split(".") if importer_is_package else importer.split(".")[:-1]
    if level > len(package_parts):
        return ""
    base = package_parts[: len(package_parts) - level + 1]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def import_edges(path: Path, src_root: Path) -> list[ImportEdge]:
    importer = module_name(path, src_root)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    edges: list[ImportEdge] = []
    importlib_aliases = {"importlib"}
    import_module_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            importlib_aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "importlib"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            import_module_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "import_module"
            )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            edges.extend(ImportEdge(importer, alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported = resolve_from_import(
                importer,
                node.level,
                node.module,
                importer_is_package=path.name == "__init__.py",
            )
            if imported:
                edges.append(ImportEdge(importer, imported, node.lineno))
                edges.extend(
                    ImportEdge(
                        importer,
                        f"{imported}.{alias.name}",
                        node.lineno,
                        symbol_fallback=True,
                    )
                    for alias in node.names
                    if alias.name != "*"
                )
        elif isinstance(node, ast.Call) and node.args:
            function = node.func
            is_dynamic_import = (
                isinstance(function, ast.Name)
                and function.id in {"__import__", *import_module_names}
            ) or (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id in importlib_aliases
                and function.attr == "import_module"
            )
            if (
                is_dynamic_import
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                imported = node.args[0].value
                if imported.startswith("."):
                    level = len(imported) - len(imported.lstrip("."))
                    imported = resolve_from_import(
                        importer,
                        level,
                        imported[level:] or None,
                        importer_is_package=path.name == "__init__.py",
                    )
                if imported:
                    edges.append(ImportEdge(importer, imported, node.lineno))
    return edges


def production_rules(manifest: dict[str, Any]) -> list[Rule]:
    result = []
    for raw in manifest["rules"]:
        if raw["glob"].startswith("src/"):
            result.append(Rule(raw["owner"], raw["priority"], raw["glob"]))
    return result


def module_owners(root: Path, manifest: dict[str, Any]) -> dict[str, str]:
    src_root = root / "src"
    rules = production_rules(manifest)
    owners: dict[str, str] = {}
    for path in sorted(src_root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if not any(glob_matches(relative, pattern) for pattern in manifest["inventory_globs"]):
            raise BoundaryError(f"production module is outside inventory: {relative}")
        owner, _ = classify(relative, rules)
        owners[module_name(path, src_root)] = owner
    if not owners:
        raise BoundaryError("no production modules found")
    return owners


def owner_for_module(
    module: str, owners: dict[str, str], *, symbol_fallback: bool = False
) -> str | None:
    if module in owners:
        return owners[module]
    if not symbol_fallback:
        return None
    current = module.rpartition(".")[0]
    while current:
        if current in owners:
            return owners[current]
        current = current.rpartition(".")[0]
    return None


def load_bridges(manifest: dict[str, Any], owners: dict[str, str]) -> list[Bridge]:
    raw_bridges = manifest.get("temporary_bridges")
    if not isinstance(raw_bridges, list):
        raise BoundaryError("temporary_bridges must be a list")
    bridges: list[Bridge] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_bridges:
        if not isinstance(raw, dict):
            raise BoundaryError("temporary bridge must be an object")
        try:
            bridge = Bridge(raw["importer"], raw["imported"], raw["issue"])
        except (KeyError, TypeError) as exc:
            raise BoundaryError("temporary bridge requires importer, imported, and issue") from exc
        if not all(isinstance(value, str) and value for value in bridge.__dict__.values()):
            raise BoundaryError("temporary bridge values must be non-empty strings")
        if re.fullmatch(r"#[1-9][0-9]*", bridge.issue) is None:
            raise BoundaryError(f"temporary bridge has invalid issue: {bridge}")
        key = (bridge.importer, bridge.imported)
        if key in seen:
            raise BoundaryError(f"duplicate temporary bridge: {key}")
        seen.add(key)
        if bridge.importer not in owners:
            raise BoundaryError(f"temporary bridge importer does not exist: {bridge.importer}")
        if bridge.imported not in owners:
            raise BoundaryError(f"temporary bridge import does not exist: {bridge.imported}")
        bridges.append(bridge)
    return bridges


def bridge_for(edge: ImportEdge, bridges: list[Bridge]) -> Bridge | None:
    return next(
        (
            bridge
            for bridge in bridges
            if edge.importer == bridge.importer
            and (
                edge.imported == bridge.imported
                or edge.imported.startswith(bridge.imported + ".")
            )
        ),
        None,
    )


def forbidden_edges(
    edges: list[ImportEdge],
    owners: dict[str, str],
    packages: dict[str, Any],
    bridges: list[Bridge],
) -> tuple[list[ImportEdge], set[Bridge]]:
    violations: list[ImportEdge] = []
    used_bridges: set[Bridge] = set()
    for edge in edges:
        importer_owner = owner_for_module(edge.importer, owners)
        imported_owner = owner_for_module(
            edge.imported, owners, symbol_fallback=edge.symbol_fallback
        )
        if importer_owner is None:
            raise BoundaryError(f"unknown importing production module: {edge.importer}")
        if imported_owner is None and (
            edge.imported == "trustforge"
            or edge.imported.startswith("trustforge.")
            or edge.imported == "trustforge_core"
            or edge.imported.startswith("trustforge_core.")
        ):
            raise BoundaryError(f"unknown imported production module: {edge.imported}")
        if imported_owner is None or imported_owner == importer_owner:
            continue
        allowed = set(packages[importer_owner]["depends_on"])
        if imported_owner in allowed:
            continue
        bridge = bridge_for(edge, bridges)
        if bridge:
            used_bridges.add(bridge)
        else:
            violations.append(edge)
    return violations, used_bridges


def check(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    owners = module_owners(root, manifest)
    bridges = load_bridges(manifest, owners)
    src_root = root / "src"
    edges = [
        edge
        for path in sorted(src_root.rglob("*.py"))
        for edge in import_edges(path, src_root)
    ]
    violations, used_bridges = forbidden_edges(
        edges, owners, manifest["packages"], bridges
    )
    stale = sorted(set(bridges) - used_bridges, key=lambda item: (item.importer, item.imported))
    if violations:
        detail = "\n".join(
            f"{edge.importer}:{edge.lineno} imports {edge.imported}"
            for edge in violations
        )
        raise BoundaryError(f"forbidden package imports:\n{detail}")
    if stale:
        detail = ", ".join(f"{item.importer}->{item.imported}" for item in stale)
        raise BoundaryError(f"stale temporary bridges: {detail}")
    counts = {package: sum(owner == package for owner in owners.values()) for package in manifest["packages"]}
    return {
        "schema_version": 1,
        "modules": len(owners),
        "imports": len(edges),
        "package_module_counts": counts,
        "temporary_bridges": len(used_bridges),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        result = check(
            root,
            load_manifest(args.manifest or root / "qa" / "package-ownership.json"),
        )
    except (BoundaryError, ValueError, OSError, SyntaxError) as exc:
        print(f"package boundary check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
