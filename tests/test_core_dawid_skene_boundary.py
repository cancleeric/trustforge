"""Canonical pure-core Dawid-Skene boundary tests (#419)."""

from __future__ import annotations

import ast
from pathlib import Path

from trustforge.trust.dawid_skene import em_source_reliability as legacy_em
from trustforge_core.dawid_skene import em_source_reliability as core_em


CORE_FILE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "trustforge_core"
    / "dawid_skene.py"
)


def _votes() -> dict[tuple[str, int], dict[str, str]]:
    return {
        ("BTC", 1): {"a": "bullish", "b": "bullish", "c": "bearish"},
        ("BTC", 2): {"a": "bearish", "b": "bearish", "c": "bullish"},
        ("BTC", 3): {"a": "neutral", "b": "neutral", "c": "bullish"},
    }


def test_legacy_import_is_same_canonical_implementation():
    assert legacy_em is core_em


def test_core_and_legacy_paths_have_exact_result_parity():
    assert core_em(_votes()) == legacy_em(_votes())


def test_core_estimator_has_standard_library_imports_only():
    tree = ast.parse(CORE_FILE.read_text(encoding="utf-8"), filename=str(CORE_FILE))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert imported == ["__future__", "math", "collections"]
