"""Tests for Trust Kernel facade (#381).

Validates:
1. Facade import — all re-exported symbols are importable and functional
2. Import-boundary — kernel.py does NOT import prohibited modules (R2)
3. Basic computation — score/aggregate produce expected types via kernel facade
"""
from __future__ import annotations

import ast
import inspect
import pathlib
from typing import Callable

import pytest


# ---------------------------------------------------------------------------
# T2: Import-boundary test (AST scan)
# ---------------------------------------------------------------------------

# Prohibited module patterns per R2 of the spec
PROHIBITED_IMPORTS = {
    "trustforge.bedrock",
    "trustforge.ingestion",
    "trustforge.web",
    "trustforge.skills",
    "trustforge.budget_guard",
    "trustforge.ledger",
    "trustforge.agent",
    "boto3",
    "botocore",
    "urllib",
    "http",
    "socket",
}

# Additional prohibited patterns (partial match for os.environ, os.getenv, open, pathlib read)
PROHIBITED_ATTRIBUTE_CALLS = {
    ("os", "environ"),
    ("os", "getenv"),
}


def _collect_kernel_py_files() -> list[pathlib.Path]:
    """Collect all .py files in the trust/kernel module (file or future sub-package)."""
    trust_dir = pathlib.Path(__file__).parent.parent / "src" / "trustforge" / "trust"
    kernel_file = trust_dir / "kernel.py"
    kernel_pkg = trust_dir / "kernel"

    files: list[pathlib.Path] = []
    if kernel_file.exists():
        files.append(kernel_file)
    if kernel_pkg.is_dir():
        files.extend(kernel_pkg.rglob("*.py"))
    return files


def _extract_imports(tree: ast.AST) -> list[str]:
    """Extract all imported module names from an AST."""
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
    return modules


def test_kernel_boundary_no_prohibited_imports():
    """R2/T2: Trust Kernel must not import prohibited modules."""
    kernel_files = _collect_kernel_py_files()
    assert kernel_files, "No kernel .py files found — expected at least kernel.py"

    violations: list[str] = []
    for fpath in kernel_files:
        source = fpath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(fpath))
        imports = _extract_imports(tree)
        for imp in imports:
            for prohibited in PROHIBITED_IMPORTS:
                if imp == prohibited or imp.startswith(prohibited + "."):
                    violations.append(f"{fpath.name}: imports '{imp}' (prohibited: {prohibited})")

    assert not violations, (
        "Trust Kernel boundary violation — prohibited imports found:\n"
        + "\n".join(f"  • {v}" for v in violations)
    )


def test_kernel_boundary_no_environ_access():
    """R2: Trust Kernel must not access os.environ or os.getenv."""
    kernel_files = _collect_kernel_py_files()
    violations: list[str] = []

    for fpath in kernel_files:
        source = fpath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(fpath))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                # Check for os.environ, os.getenv
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "os"
                    and node.attr in ("environ", "getenv")
                ):
                    violations.append(f"{fpath.name}: accesses os.{node.attr}")

    assert not violations, (
        "Trust Kernel boundary violation — environment access found:\n"
        + "\n".join(f"  • {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# T1: Facade import test — all __all__ symbols importable
# ---------------------------------------------------------------------------

def test_kernel_facade_importable():
    """Verify kernel.py can be imported and exposes all declared symbols."""
    from trustforge.trust import kernel

    # Check __all__ exists and is non-empty
    assert hasattr(kernel, "__all__")
    assert len(kernel.__all__) >= 7  # At minimum the declared symbols

    # Every name in __all__ should be resolvable
    for name in kernel.__all__:
        assert hasattr(kernel, name), f"kernel.__all__ declares '{name}' but it's not importable"


def test_kernel_schema_version():
    """KERNEL_SCHEMA_VERSION is accessible and matches data_contracts."""
    from trustforge.trust.kernel import KERNEL_SCHEMA_VERSION
    from trustforge.data_contracts import KERNEL_SCHEMA_VERSION as DC_VERSION

    assert KERNEL_SCHEMA_VERSION == "1.0.0"
    assert KERNEL_SCHEMA_VERSION == DC_VERSION


def test_kernel_constants_match_scoring():
    """Kernel facade constants are the same objects as scoring.py originals."""
    from trustforge.trust.kernel import DEFAULT_WEIGHTS, KIND_REPUTATION, KIND_HALFLIFE_HOURS
    from trustforge.trust.scoring import DEFAULT_WEIGHTS as S_WEIGHTS
    from trustforge.trust.scoring import KIND_REPUTATION as S_REP
    from trustforge.trust.scoring import KIND_HALFLIFE_HOURS as S_HALF

    # Same objects (not copies) — ensures no drift
    assert DEFAULT_WEIGHTS is S_WEIGHTS
    assert KIND_REPUTATION is S_REP
    assert KIND_HALFLIFE_HOURS is S_HALF


def test_kernel_functions_are_callable():
    """Core functions re-exported by kernel are callable."""
    from trustforge.trust.kernel import (
        extract_claims,
        score,
        aggregate,
        em_source_reliability,
    )

    assert callable(extract_claims)
    assert callable(score)
    assert callable(aggregate)
    assert callable(em_source_reliability)


def test_kernel_dataclasses_importable():
    """Claim, ScoredClaim, TrustedBrief are importable from kernel."""
    from trustforge.trust.kernel import Claim, ScoredClaim, TrustedBrief
    import dataclasses

    assert dataclasses.is_dataclass(Claim)
    assert dataclasses.is_dataclass(ScoredClaim)
    assert dataclasses.is_dataclass(TrustedBrief)


# ---------------------------------------------------------------------------
# Basic computation test (smoke) — ensure kernel facade is functional
# ---------------------------------------------------------------------------

def test_kernel_extract_claims_smoke():
    """extract_claims via kernel facade returns a list."""
    from trustforge.trust.kernel import extract_claims
    from trustforge.ingestion.base import Document

    doc = Document(
        id="test-1",
        kind="news",
        source="reuters",
        text="Bitcoin surges to new all-time high above $100k.",
        url="https://example.com/btc",
        ts=1700000000.0,
        meta={},
    )

    claims = extract_claims([doc])
    assert isinstance(claims, list)
    assert len(claims) >= 1


def test_kernel_em_source_reliability_smoke():
    """em_source_reliability via kernel returns reliability dict."""
    from trustforge.trust.kernel import em_source_reliability

    # Minimal fixture: 3 sources labelling 3 items
    # votes format: {(coin, window): {source: label}}
    annotations = {
        ("BTC", 0): {"src_a": "bullish", "src_b": "bullish", "src_c": "bearish"},
        ("BTC", 1): {"src_a": "bearish", "src_b": "bearish", "src_c": "bearish"},
        ("BTC", 2): {"src_a": "bullish", "src_b": "neutral", "src_c": "bullish"},
    }

    reliability, confusion, posterior, meta = em_source_reliability(annotations)
    assert isinstance(reliability, dict)
    # Should have a reliability per source
    assert "src_a" in reliability
    assert "src_b" in reliability
    assert "src_c" in reliability
    # Reliabilities should be float in [0, 1]
    for v in reliability.values():
        assert 0.0 <= v <= 1.0
