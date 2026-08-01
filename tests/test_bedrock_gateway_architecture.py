"""#1347: repository-wide Bedrock request boundary architecture contract."""
from __future__ import annotations

import ast
from pathlib import Path

from trustforge import analysis_plan


ROOT = Path(__file__).resolve().parents[1]
MODEL_METHODS = {
    "converse",
    "converse_stream",
    "count_tokens",
    "invoke_model",
    "invoke_model_with_response_stream",
    "invoke_agent",
    "retrieve_and_generate",
}


def _production_python() -> list[Path]:
    return sorted(
        path
        for root in (ROOT / "src", ROOT / "scripts", ROOT / "app")
        for path in root.rglob("*.py")
    )


def test_only_the_audited_factory_constructs_bedrock_runtime_clients() -> None:
    violations: list[str] = []
    for path in _production_python():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            function = node.func
            if not (
                isinstance(function, ast.Attribute)
                and function.attr == "client"
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "bedrock-runtime"
            ):
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative != "src/trustforge/bedrock.py":
                violations.append(f"{relative}:{node.lineno}")
    assert violations == []


def test_all_real_model_calls_are_in_the_audited_inventory() -> None:
    calls: set[tuple[str, str]] = set()
    for path in _production_python():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in MODEL_METHODS:
                    calls.add((path.relative_to(ROOT).as_posix(), node.func.attr))

    assert calls == {
        ("src/trustforge/analysis_plan.py", "converse"),
        ("src/trustforge/analysis_plan.py", "count_tokens"),
        ("src/trustforge/bedrock.py", "converse"),
        ("src/trustforge/smoke.py", "converse"),
    }


def test_count_tokens_uses_the_shared_gate(monkeypatch) -> None:
    events: list[str] = []

    class Slot:
        def __enter__(self):
            events.append("acquire")

        def __exit__(self, *_args):
            events.append("release")

    class Runtime:
        def count_tokens(self, **_kwargs):
            events.append("count_tokens")
            return {"inputTokens": 3}

    monkeypatch.setattr("trustforge.bedrock.bedrock_invoke_slot", Slot)
    tokenizer = analysis_plan.BedrockConverseTokenizer(
        Runtime(), "model", "package", "version", "hash"
    )

    assert tokenizer.count(b"prompt") == 3
    assert events == ["acquire", "count_tokens", "release"]


def test_legacy_strands_demo_is_fail_closed() -> None:
    source = (ROOT / "app/CustomerSupport/main.py").read_text(encoding="utf-8")
    function = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "create_agent"
    )
    assert isinstance(function.body[1], ast.Raise)
