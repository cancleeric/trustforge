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
    def __init__(
        self,
        module: str,
        path: Path,
        protocol_classes: set[int],
    ):
        self.module = module
        self.path = path
        self.known_protocol_classes = protocol_classes
        self.scope: list[str] = []
        self.scope_kinds: list[str] = []
        self.protocol_classes: list[bool] = []
        self.findings: list[dict[str, object]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.scope_kinds.append("class")
        self.protocol_classes.append(id(node) in self.known_protocol_classes)
        self.generic_visit(node)
        self.protocol_classes.pop()
        self.scope_kinds.pop()
        self.scope.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_direct_protocol_method = (
            bool(self.scope_kinds)
            and self.scope_kinds[-1] == "class"
            and self.protocol_classes[-1]
        )
        kind = None if is_direct_protocol_method else _stub_kind(node)
        if kind:
            self.findings.append({
                "symbol": ".".join((self.module, *self.scope, node.name)),
                "kind": kind,
                "path": self.path.as_posix(),
                "line": node.lineno,
            })
        self.scope.append(node.name)
        self.scope_kinds.append("function")
        self.generic_visit(node)
        self.scope_kinds.pop()
        self.scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function


def _bound_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {
            name
            for element in target.elts
            for name in _bound_names(element)
        }
    return set()


class _NamedExprVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.names.update(_bound_names(node.target))
        self.visit(node.value)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef


def _named_expr_names(node: ast.AST) -> set[str]:
    visitor = _NamedExprVisitor()
    visitor.visit(node)
    return visitor.names


def _pattern_names(pattern: ast.pattern) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(pattern):
        if isinstance(node, ast.MatchAs) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


class _ProtocolBindingAnalyzer:
    def __init__(self) -> None:
        self.protocol_classes: set[int] = set()

    @staticmethod
    def _intersection(
        states: list[tuple[set[str], set[str]]],
    ) -> tuple[set[str], set[str]]:
        return (
            set.intersection(*(names for names, _ in states)),
            set.intersection(*(modules for _, modules in states)),
        )

    @staticmethod
    def _invalidate_names(
        state: tuple[set[str], set[str]], bound: set[str]
    ) -> tuple[set[str], set[str]]:
        names, modules = state
        return names - bound, modules - bound

    @staticmethod
    def _invalidate_targets(
        state: tuple[set[str], set[str]], targets: list[ast.expr]
    ) -> tuple[set[str], set[str]]:
        names, modules = state
        bound = {name for target in targets for name in _bound_names(target)}
        names, modules = names - bound, modules - bound
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "Protocol"
                and isinstance(target.value, ast.Name)
            ):
                modules.discard(target.value.id)
        return names, modules

    def _block(
        self,
        body: list[ast.stmt],
        state: tuple[set[str], set[str]],
    ) -> tuple[set[str], set[str]]:
        for node in body:
            state = self._statement(node, state)
        return state

    def _statement(
        self,
        node: ast.stmt,
        state: tuple[set[str], set[str]],
    ) -> tuple[set[str], set[str]]:
        names, modules = map(set, state)
        state = (names, modules)
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound = alias.asname or alias.name
                state = self._invalidate_names(state, {bound})
                if (
                    node.module in {"typing", "typing_extensions"}
                    and alias.name == "Protocol"
                ):
                    state[0].add(bound)
            return state
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                state = self._invalidate_names(state, {bound})
                if alias.name in {"typing", "typing_extensions"}:
                    state[1].add(bound)
            return state
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            state = self._invalidate_names(state, _named_expr_names(node))
            if isinstance(node, ast.ClassDef) and any(
                _is_protocol_base(base, *state) for base in node.bases
            ):
                self.protocol_classes.add(id(node))
            return self._invalidate_names(state, {node.name})
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            state = self._invalidate_names(state, _named_expr_names(node))
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            return self._invalidate_targets(state, targets)
        if isinstance(node, ast.Delete):
            return self._invalidate_targets(state, node.targets)
        if isinstance(node, ast.If):
            state = self._invalidate_names(state, _named_expr_names(node.test))
            return self._intersection([
                self._block(node.body, state),
                self._block(node.orelse, state),
            ])
        if isinstance(node, (ast.For, ast.AsyncFor)):
            state = self._invalidate_names(state, _named_expr_names(node.iter))
            loop_state = self._invalidate_targets(state, [node.target])
            body_state = self._block(node.body, loop_state)
            loop_exit = self._intersection([state, body_state])
            else_state = self._block(node.orelse, loop_exit)
            return self._intersection([loop_exit, else_state])
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                state = self._invalidate_names(
                    state, _named_expr_names(item.context_expr)
                )
                if item.optional_vars:
                    state = self._invalidate_targets(state, [item.optional_vars])
            return self._block(node.body, state)
        if isinstance(node, (ast.Try, ast.TryStar)):
            body_state = state
            exception_states = [state]
            for statement in node.body:
                body_state = self._statement(statement, body_state)
                exception_states.append(body_state)
            normal = self._block(node.orelse, body_state)
            paths = [normal]
            handler_entry = self._intersection(exception_states)
            for handler in node.handlers:
                handler_state = handler_entry
                if handler.name:
                    handler_state = self._invalidate_names(
                        handler_state, {handler.name}
                    )
                paths.append(self._block(handler.body, handler_state))
            if node.finalbody:
                paths = [self._block(node.finalbody, path) for path in paths]
            return self._intersection(paths)
        if isinstance(node, ast.Match):
            state = self._invalidate_names(state, _named_expr_names(node.subject))
            paths = [state]
            for case in node.cases:
                case_state = self._invalidate_names(
                    state, _pattern_names(case.pattern)
                )
                if case.guard:
                    case_state = self._invalidate_names(
                        case_state, _named_expr_names(case.guard)
                    )
                paths.append(self._block(case.body, case_state))
            return self._intersection(paths)
        return self._invalidate_names(state, _named_expr_names(node))

    def analyze(self, tree: ast.Module) -> set[int]:
        self._block(tree.body, (set(), set()))
        return self.protocol_classes


def _protocol_classes(tree: ast.Module) -> set[int]:
    return _ProtocolBindingAnalyzer().analyze(tree)


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
        visitor = _FunctionVisitor(module, relative, _protocol_classes(tree))
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
