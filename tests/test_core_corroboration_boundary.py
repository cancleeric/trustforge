"""Golden parity and import-boundary tests for core corroboration."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from trustforge_core import StanceLabel
from trustforge_core.corroboration import (
    CorroborationClaim,
    corroborate,
)


CORE_FILE = (
    Path(__file__).resolve().parents[1] / "src" / "trustforge_core" / "corroboration.py"
)
SOURCE_IDENTITY_FILE = CORE_FILE.with_name("source_identity.py")


def test_core_yields_only_one_pending_pair_until_app_injects_result():
    claims = (
        CorroborationClaim("機構 資金 流入 現貨 ETF 推升 信心", "target"),
        CorroborationClaim("監管 法案 投票 延後", "noise"),
        CorroborationClaim("機構 資金 流入 現貨 ETF 推升 價格", "source-a"),
        CorroborationClaim("機構 資金 流入 現貨 ETF 增加 信心", "source-b"),
    )
    engine = corroborate(claims[0], claims, require_stance=True)

    first = next(engine)
    assert first.candidate_text == claims[2].text
    second = engine.send("neutral")
    assert second.candidate_text == claims[3].text
    try:
        engine.send("contradiction")
    except StopIteration as completed:
        result = completed.value
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("engine must complete after the last eligible pair")

    assert result.independent_sources == frozenset({"source-a"})
    assert result.contradicting_sources == frozenset({"source-b"})


@pytest.mark.parametrize(
    "require_entailment", [False, True], ids=["ordinary", "strict"]
)
@pytest.mark.parametrize("invalid_label", [None, "", "entails", "typo", [], {}])
def test_core_rejects_invalid_runtime_stance_labels(
    require_entailment: bool, invalid_label: object
) -> None:
    claims = (
        CorroborationClaim("機構 資金 流入 現貨 ETF 推升 信心", "target"),
        CorroborationClaim("機構 資金 流入 現貨 ETF 推升 價格", "source-a"),
    )
    engine = corroborate(
        claims[0], claims, require_stance=True, require_entailment=require_entailment
    )
    next(engine)

    with pytest.raises(ValueError, match="stance label must be"):
        engine.send(invalid_label)  # type: ignore[arg-type]


def test_stance_label_is_a_public_type_contract() -> None:
    label: StanceLabel = "entailment"
    assert label == "entailment"


def test_core_corroboration_imports_standard_library_only():
    tree = ast.parse(CORE_FILE.read_text(encoding="utf-8"), filename=str(CORE_FILE))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    assert imports == [
        "__future__",
        "re",
        "collections.abc",
        "dataclasses",
        "typing",
        "source_identity",
    ]


def test_source_identity_module_has_no_runtime_or_application_dependencies():
    tree = ast.parse(
        SOURCE_IDENTITY_FILE.read_text(encoding="utf-8"),
        filename=str(SOURCE_IDENTITY_FILE),
    )
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    ]
    assert imports == ["__future__"]
