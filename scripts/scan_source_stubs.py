#!/usr/bin/env python3
"""Fail the local pre-push gate on production stubs."""
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


def _stub_kind(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    abstractmethod_names: set[str] | None = None,
    abc_modules: set[str] | None = None,
) -> str | None:
    abstractmethod_names = abstractmethod_names or set()
    abc_modules = abc_modules or set()
    if any(
        (isinstance(decorator, ast.Name) and decorator.id in abstractmethod_names)
        or (
            isinstance(decorator, ast.Attribute)
            and decorator.attr == "abstractmethod"
            and isinstance(decorator.value, ast.Name)
            and decorator.value.id in abc_modules
        )
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
    def __init__(
        self,
        module: str,
        path: Path,
        protocol_names: set[str],
        protocol_modules: set[str],
        abstractmethod_names: set[str],
        abc_modules: set[str],
    ):
        self.module = module
        self.path = path
        self.protocol_names = protocol_names
        self.protocol_modules = protocol_modules
        self.abstractmethod_names = abstractmethod_names
        self.abc_modules = abc_modules
        self.scope: list[str] = []
        self.scope_kinds: list[str] = []
        self.protocol_classes: list[bool] = []
        self.findings: list[dict[str, object]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        shadowed = _bound_names(node.body)
        previous_names = self.abstractmethod_names
        previous_modules = self.abc_modules
        self.abstractmethod_names = previous_names - shadowed
        self.abc_modules = previous_modules - shadowed
        self.scope.append(node.name)
        self.scope_kinds.append("class")
        self.protocol_classes.append(any(
            _is_protocol_base(base, self.protocol_names, self.protocol_modules)
            for base in node.bases
        ))
        self.generic_visit(node)
        self.protocol_classes.pop()
        self.scope_kinds.pop()
        self.scope.pop()
        self.abstractmethod_names = previous_names
        self.abc_modules = previous_modules

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_direct_protocol_method = (
            bool(self.scope_kinds)
            and self.scope_kinds[-1] == "class"
            and self.protocol_classes[-1]
        )
        kind = None if is_direct_protocol_method else _stub_kind(
            node, self.abstractmethod_names, self.abc_modules
        )
        if kind:
            self.findings.append({
                "symbol": ".".join((self.module, *self.scope, node.name)),
                "kind": kind,
                "path": self.path.as_posix(),
                "line": node.lineno,
            })
        self.scope.append(node.name)
        self.scope_kinds.append("function")
        previous_names = self.abstractmethod_names
        previous_modules = self.abc_modules
        shadowed = _bound_names(node.body) | {
            argument.arg
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
        }
        if node.args.vararg:
            shadowed.add(node.args.vararg.arg)
        if node.args.kwarg:
            shadowed.add(node.args.kwarg.arg)
        self.abstractmethod_names = previous_names - shadowed
        self.abc_modules = previous_modules - shadowed
        self.generic_visit(node)
        self.abstractmethod_names = previous_names
        self.abc_modules = previous_modules
        self.scope_kinds.pop()
        self.scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function


def _protocol_bindings(tree: ast.Module) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in {"typing", "typing_extensions"}:
            names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "Protocol"
            )
        elif isinstance(node, ast.Import):
            modules.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name in {"typing", "typing_extensions"}
            )
    return names, modules


def _bound_names(nodes: Iterable[ast.stmt]) -> set[str]:
    """Return names rebound directly in one lexical scope."""
    names: set[str] = set()

    def add_target(target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                add_target(element)

    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                add_target(target)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _abc_bindings(tree: ast.Module) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    modules: set[str] = set()
    trusted_import_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "abc":
            for alias in node.names:
                if alias.name == "abstractmethod":
                    bound = alias.asname or alias.name
                    names.add(bound)
                    trusted_import_names.add(bound)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "abc":
                    bound = alias.asname or alias.name
                    modules.add(bound)
                    trusted_import_names.add(bound)

    # A same-scope second binding makes the provenance ambiguous. Fail closed.
    all_bound = _bound_names(tree.body)
    binding_counts: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                binding_counts[bound] = binding_counts.get(bound, 0) + 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            binding_counts[node.name] = binding_counts.get(node.name, 0) + 1
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for bound in _bound_names([node]):
                binding_counts[bound] = binding_counts.get(bound, 0) + 1
    shadowed = {
        name for name in all_bound & trusted_import_names
        if binding_counts.get(name, 0) > 1
    }
    return names - shadowed, modules - shadowed


def _is_protocol_base(
    node: ast.expr,
    protocol_names: set[str],
    protocol_modules: set[str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in protocol_names
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "Protocol"
        and isinstance(node.value, ast.Name)
        and node.value.id in protocol_modules
    )


def scan(paths: Iterable[Path]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        module = ".".join(relative.with_suffix("").parts[1:])
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        protocol_names, protocol_modules = _protocol_bindings(tree)
        abstractmethod_names, abc_modules = _abc_bindings(tree)
        visitor = _FunctionVisitor(
            module, relative, protocol_names, protocol_modules,
            abstractmethod_names, abc_modules,
        )
        visitor.visit(tree)
        findings.extend(visitor.findings)
    return sorted(findings, key=lambda item: (str(item["symbol"]), str(item["kind"])))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allowlist", type=Path, default=ROOT / "docs/audit/stub-allowlist.json")
    parser.add_argument("--out", type=Path, default=ROOT / "out/pre-push/stub-scan.json")
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
