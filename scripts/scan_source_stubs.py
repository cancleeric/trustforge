#!/usr/bin/env python3
"""Fail CI when a new empty/not-implemented production function appears."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]


def _effective_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body.pop(0)
    return body


def _stub_kind(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    if any(
        (isinstance(decorator, ast.Name) and decorator.id == "abstractmethod")
        or (isinstance(decorator, ast.Attribute) and decorator.attr == "abstractmethod")
        for decorator in node.decorator_list
    ):
        return None
    body = _effective_body(node)
    if not body:
        return "empty"
    if len(body) != 1:
        return None
    statement = body[0]
    if isinstance(statement, ast.Pass):
        return "pass"
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) \
            and statement.value.value is Ellipsis:
        return "ellipsis"
    if isinstance(statement, ast.Raise):
        exc = statement.exc
        target = exc.func if isinstance(exc, ast.Call) else exc
        if isinstance(target, ast.Name) and target.id == "NotImplementedError":
            return "not_implemented"
    return None


class _FunctionVisitor(ast.NodeVisitor):
    def __init__(self, module: str, path: Path):
        self.module = module
        self.path = path
        self.scope: list[str] = []
        self.findings: list[dict[str, object]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        kind = _stub_kind(node)
        if kind:
            self.findings.append({
                "symbol": ".".join((self.module, *self.scope, node.name)),
                "kind": kind,
                "path": self.path.as_posix(),
                "line": node.lineno,
            })
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function


def scan(paths: Iterable[Path]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        module = ".".join(relative.with_suffix("").parts[1:])
        visitor = _FunctionVisitor(module, relative)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(relative)))
        findings.extend(visitor.findings)
    return sorted(findings, key=lambda item: (str(item["symbol"]), str(item["kind"])))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allowlist", type=Path, default=ROOT / "docs/audit/stub-allowlist.json")
    parser.add_argument("--out", type=Path, default=ROOT / "out/ci/stub-scan.json")
    args = parser.parse_args()
    allowed_doc = json.loads(args.allowlist.read_text(encoding="utf-8"))
    allowed = {(row["symbol"], row["kind"]): row for row in allowed_doc.get("allowed", [])}
    findings = scan((ROOT / "src/trustforge").rglob("*.py"))
    actual = {(row["symbol"], row["kind"]): row for row in findings}
    unexpected = [actual[key] for key in sorted(actual.keys() - allowed.keys())]
    stale = [allowed[key] for key in sorted(allowed.keys() - actual.keys())]
    report = {
        "schema_version": 1, "status": "passed" if not unexpected and not stale else "failed",
        "findings": findings, "unexpected": unexpected, "stale_allowlist": stale,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "findings": len(findings),
                      "unexpected": len(unexpected), "stale_allowlist": len(stale)}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
