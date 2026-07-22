"""Independent Trust Kernel contract tests (#418)."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from trustforge.agent.kernel_mapper import to_kernel_input
from trustforge.ingestion.base import Document
from trustforge.trust.scoring import Claim
from trustforge_core import (
    KERNEL_CONTRACT_VERSION,
    KernelClaim,
    KernelDocument,
    KernelInput,
    KernelOutput,
)


CORE_ROOT = Path(__file__).resolve().parents[1] / "src" / "trustforge_core"


def test_core_contract_package_has_no_trustforge_or_runtime_imports():
    forbidden = {"trustforge", "boto3", "sqlite3", "threading", "os", "subprocess"}
    violations: list[str] = []
    for path in CORE_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if any(name == item or name.startswith(item + ".") for item in forbidden):
                    violations.append(f"{path.name}:{node.lineno} imports {name}")
    assert violations == []


def test_contracts_are_frozen_slotted_values():
    document = KernelDocument("d1", "news", "source", "BTC news", 100.0)
    claim = KernelClaim("c1", "BTC news", document)
    request = KernelInput((claim,), 100.0, "BTC", "outlook")
    result = KernelOutput(0.7, 0.6, False, "偏多", ("supported",), 1, 1)

    assert KERNEL_CONTRACT_VERSION == "2.0.0"
    assert request.contract_version == KERNEL_CONTRACT_VERSION
    assert result.contract_version == KERNEL_CONTRACT_VERSION
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.coin = "ETH"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.confidence = 0.1  # type: ignore[misc]


def test_application_mapper_detaches_claims_from_mutable_app_schema():
    metadata = {"coin": "BTC", "nested": {"tags": ["etf", "flow"]}}
    document = Document(
        id="d1",
        kind="regulatory",
        source="sec",
        text="BTC ETF inflows expanded",
        ts=1_700_000_000.0,
        url="https://example.test/d1",
        meta=metadata,
    )
    claim = Claim("c1", document.text, document, "fact", "bullish")

    request = to_kernel_input(
        [claim], pit_epoch=1_700_000_100.0, coin="BTC", query="BTC outlook"
    )
    metadata["coin"] = "ETH"
    document.text = "mutated after mapping"

    assert isinstance(request, KernelInput)
    assert request.claims[0].document.text == "BTC ETF inflows expanded"
    assert request.claims[0].document.metadata == (
        ("coin", "BTC"),
        ("nested", (("tags", ("etf", "flow")),)),
    )


def test_mapper_preserves_claim_order_and_normalized_fields():
    docs = [
        Document(f"d{i}", "news", f"s{i}", f"claim {i}", ts=float(i), meta={})
        for i in range(2)
    ]
    claims = [Claim(f"c{i}", doc.text, doc) for i, doc in enumerate(docs)]

    request = to_kernel_input(claims, pit_epoch=10.0, coin="BTC", query="q")

    assert [claim.id for claim in request.claims] == ["c0", "c1"]
    assert request.pit_epoch == 10.0
    assert request.coin == "BTC"
    assert request.query == "q"
