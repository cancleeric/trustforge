"""Architecture import-boundary checks for platform extraction (#404).

These tests intentionally guard the migration boundary rather than pretending
the current codebase is already fully separated.  Existing bridges are listed
explicitly with the issue that owns their removal; new cross-layer imports must
either remove the dependency or add an issue-backed bridge entry.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
PACKAGE_ROOT = SRC_ROOT / "trustforge"

CORE_PREFIXES = (
    "trustforge_core",
    "trustforge.trust",
)

PLATFORM_PREFIXES = (
    "trustforge.policy",
    "trustforge.ports",
    "trustforge.module_telemetry",
    "trustforge.execlog",
    "trustforge.idempotency_lease",
)

APP_PREFIXES = (
    "trustforge.agent",
    "trustforge.analysis_flow",
    "trustforge.backfill",
    "trustforge.bedrock",
    "trustforge.ingestion",
    "trustforge.lambda_handler",
    "trustforge.pipeline",
    "trustforge.web",
)

# Temporary bridges are debt, not permission.  Each entry must point at the
# extraction issue expected to delete it.
TEMPORARY_BRIDGES: dict[tuple[str, str], str] = {
    ("trustforge.trust.kernel", "trustforge.trust.scoring"): "#419",
    ("trustforge.trust.scoring", "trustforge.bedrock"): "#407",
    ("trustforge.trust.scoring", "trustforge.ingestion.base"): "#419",
    ("trustforge.trust.scoring", "trustforge.module_telemetry"): "#412",
    ("trustforge.trust.stance_cache", "trustforge.ingestion.base"): "#419",
    ("trustforge.trust.insights", "trustforge.ingestion.base"): "#419",
    ("trustforge.trust.insights", "trustforge.schema"): "#420",
    ("trustforge.ports", "trustforge.bedrock"): "#407",
    ("trustforge.ports", "trustforge.ingestion.cache"): "#408",
}


@dataclass(frozen=True)
class ImportEdge:
    importer: str
    imported: str
    lineno: int


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC_ROOT).with_suffix("")
    return ".".join(rel.parts)


def _resolve_from_import(importer: str, level: int, module: str | None) -> str:
    if level == 0:
        return module or ""
    parts = importer.split(".")
    base = parts[: -level]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def _iter_import_edges(path: Path) -> list[ImportEdge]:
    importer = _module_name(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    edges: list[ImportEdge] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                edges.append(ImportEdge(importer, alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            imported = _resolve_from_import(importer, node.level, node.module)
            if imported:
                edges.append(ImportEdge(importer, imported, node.lineno))

    return edges


def _matches(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes)


def _is_temporary_bridge(edge: ImportEdge) -> bool:
    return any(
        edge.importer == importer
        and (edge.imported == imported or edge.imported.startswith(imported + "."))
        for importer, imported in TEMPORARY_BRIDGES
    )


def _format_violations(edges: list[ImportEdge]) -> str:
    return "\n".join(
        f"  {edge.importer}:{edge.lineno} imports {edge.imported}" for edge in edges
    )


def _all_project_edges() -> list[ImportEdge]:
    edges: list[ImportEdge] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        edges.extend(_iter_import_edges(path))
    return edges


def test_boundary_scanner_catches_synthetic_core_to_app_import():
    edge = ImportEdge("trustforge.trust.synthetic", "trustforge.pipeline", 1)
    violations = [
        edge
        for edge in [edge]
        if _matches(edge.importer, CORE_PREFIXES)
        and _matches(edge.imported, APP_PREFIXES + PLATFORM_PREFIXES)
        and not _is_temporary_bridge(edge)
    ]

    assert violations == [edge]


def test_core_imports_do_not_cross_into_platform_or_app_without_bridge_issue():
    violations = [
        edge
        for edge in _all_project_edges()
        if _matches(edge.importer, CORE_PREFIXES)
        and _matches(edge.imported, PLATFORM_PREFIXES + APP_PREFIXES)
        and not _is_temporary_bridge(edge)
    ]

    assert not violations, (
        "Core modules must not import platform/app modules without an "
        "issue-backed temporary bridge:\n"
        + _format_violations(violations)
    )


def test_platform_imports_do_not_cross_into_app_without_bridge_issue():
    violations = [
        edge
        for edge in _all_project_edges()
        if _matches(edge.importer, PLATFORM_PREFIXES)
        and _matches(edge.imported, APP_PREFIXES)
        and not _is_temporary_bridge(edge)
    ]

    assert not violations, (
        "Platform modules must not import TrustForge app/runtime modules "
        "without an issue-backed temporary bridge:\n"
        + _format_violations(violations)
    )


def test_temporary_bridges_are_issue_backed():
    bad_refs = {
        edge: issue
        for edge, issue in TEMPORARY_BRIDGES.items()
        if not issue.startswith("#") or not issue[1:].isdigit()
    }

    assert not bad_refs
