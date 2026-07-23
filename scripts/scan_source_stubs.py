#!/usr/bin/env python3
"""Fail the local pre-push gate on production stubs."""
from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
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
    *,
    allow_abstractmethod: bool = False,
) -> str | None:
    abstractmethod_names = abstractmethod_names or set()
    abc_modules = abc_modules or set()
    if allow_abstractmethod and any(
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
    _TRUSTED_BINDING_ATTRIBUTES = (
        "protocol_names",
        "protocol_modules",
        "abstractmethod_names",
        "abc_modules",
        "abc_names",
        "abc_meta_names",
    )

    def __init__(
        self,
        module: str,
        path: Path,
        protocol_names: set[str],
        protocol_modules: set[str],
        abstractmethod_names: set[str],
        abc_modules: set[str],
        abc_names: set[str],
        abc_meta_names: set[str],
    ):
        self.module = module
        self.path = path
        self.protocol_names = protocol_names
        self.protocol_modules = protocol_modules
        self.abstractmethod_names = abstractmethod_names
        self.abc_modules = abc_modules
        self.abc_names = abc_names
        self.abc_meta_names = abc_meta_names
        self.scope: list[str] = []
        self.scope_kinds: list[str] = []
        self.protocol_classes: list[bool] = []
        self.abstract_classes: list[bool] = []
        self.findings: list[dict[str, object]] = []

    def _enter_lexical_scope(self, shadowed: set[str]) -> dict[str, set[str]]:
        snapshot: dict[str, set[str]] = {}
        for attribute in self._TRUSTED_BINDING_ATTRIBUTES:
            current = getattr(self, attribute)
            snapshot[attribute] = current
            setattr(self, attribute, current - shadowed)
        return snapshot

    def _leave_lexical_scope(self, snapshot: dict[str, set[str]]) -> None:
        for attribute, value in snapshot.items():
            setattr(self, attribute, value)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        is_protocol = any(
            _is_protocol_base(base, self.protocol_names, self.protocol_modules)
            for base in node.bases
        )
        is_abstract = any(
            _is_named_or_qualified(base, self.abc_names, self.abc_modules, "ABC")
            for base in node.bases
        ) or any(
            keyword.arg == "metaclass"
            and _is_named_or_qualified(
                keyword.value, self.abc_meta_names, self.abc_modules, "ABCMeta"
            )
            for keyword in node.keywords
        )
        shadowed = _bound_names(node.body)
        trusted_snapshot = self._enter_lexical_scope(shadowed)
        self.scope.append(node.name)
        self.scope_kinds.append("class")
        self.protocol_classes.append(is_protocol)
        self.abstract_classes.append(is_abstract)
        self.generic_visit(node)
        self.protocol_classes.pop()
        self.abstract_classes.pop()
        self.scope_kinds.pop()
        self.scope.pop()
        self._leave_lexical_scope(trusted_snapshot)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_direct_protocol_method = (
            bool(self.scope_kinds)
            and self.scope_kinds[-1] == "class"
            and self.protocol_classes[-1]
        )
        kind = None if is_direct_protocol_method else _stub_kind(
            node,
            self.abstractmethod_names,
            self.abc_modules,
            allow_abstractmethod=(
                is_direct_protocol_method
                or (
                    bool(self.scope_kinds)
                    and self.scope_kinds[-1] == "class"
                    and self.abstract_classes[-1]
                )
            ),
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
        shadowed = _bound_names(node.body) | _nested_nonlocal_mutations(node.body) | {
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
        trusted_snapshot = self._enter_lexical_scope(shadowed)
        self.generic_visit(node)
        self._leave_lexical_scope(trusted_snapshot)
        self.scope_kinds.pop()
        self.scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function


class _ScopeBindingVisitor(ast.NodeVisitor):
    """Count every binding in one lexical scope, including control-flow bodies."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.counts[node.id] += 1

    def visit_Global(self, node: ast.Global) -> None:
        self.counts.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.counts.update(node.names)

    def visit_Import(self, node: ast.Import) -> None:
        self.counts.update(alias.asname or alias.name.split(".")[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.counts.update(alias.asname or alias.name for alias in node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.counts[node.name] += 1
        for decorator in node.decorator_list:
            self.visit(decorator)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            if argument.annotation:
                self.visit(argument.annotation)
        if node.args.vararg and node.args.vararg.annotation:
            self.visit(node.args.vararg.annotation)
        if node.args.kwarg and node.args.kwarg.annotation:
            self.visit(node.args.kwarg.annotation)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default:
                self.visit(default)
        if node.returns:
            self.visit(node.returns)
        self._visit_type_parameters(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.counts[node.name] += 1
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        self._visit_type_parameters(node)

    def _visit_type_parameters(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> None:
        for type_parameter in getattr(node, "type_params", ()):
            for attribute in ("bound", "default_value"):
                value = getattr(type_parameter, attribute, None)
                if value:
                    self.visit(value)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # Defaults are evaluated in the enclosing scope; the body and
        # parameters belong to the lambda's own lexical scope.
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default:
                self.visit(default)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self.visit(node.elt)
        self._visit_comprehension_generators(node.generators)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self.visit(node.elt)
        self._visit_comprehension_generators(node.generators)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self.visit(node.key)
        self.visit(node.value)
        self._visit_comprehension_generators(node.generators)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self.visit(node.elt)
        self._visit_comprehension_generators(node.generators)

    def _visit_comprehension_generators(
        self, generators: list[ast.comprehension]
    ) -> None:
        for generator in generators:
            # Comprehension iteration targets live in the implicit inner scope.
            # Iterable/condition walrus expressions still bind the outer scope.
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.counts[node.name] += 1
        for statement in node.body:
            self.visit(statement)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.counts[node.name] += 1
        if node.pattern:
            self.visit(node.pattern)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.counts[node.name] += 1

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.counts[node.rest] += 1
        self.generic_visit(node)


def _scope_binding_counts(nodes: Iterable[ast.stmt]) -> Counter[str]:
    visitor = _ScopeBindingVisitor()
    for node in nodes:
        visitor.visit(node)
    return visitor.counts


class _ScopeMutationVisitor(_ScopeBindingVisitor):
    """Collect actual writes in one scope, excluding declarations alone."""

    def visit_Global(self, node: ast.Global) -> None:
        return

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        return


def _scope_mutated_names(nodes: Iterable[ast.stmt]) -> set[str]:
    visitor = _ScopeMutationVisitor()
    for node in nodes:
        visitor.visit(node)
    return set(visitor.counts)


class _ScopeDeclarationVisitor(ast.NodeVisitor):
    def __init__(self, declaration_type: type[ast.Global] | type[ast.Nonlocal]):
        self.declaration_type = declaration_type
        self.names: set[str] = set()

    def visit_Global(self, node: ast.Global) -> None:
        if self.declaration_type is ast.Global:
            self.names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        if self.declaration_type is ast.Nonlocal:
            self.names.update(node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _scope_declared_names(
    nodes: Iterable[ast.stmt],
    declaration_type: type[ast.Global] | type[ast.Nonlocal],
) -> set[str]:
    visitor = _ScopeDeclarationVisitor(declaration_type)
    for node in nodes:
        visitor.visit(node)
    return visitor.names


class _NestedDeclarationMutationVisitor(ast.NodeVisitor):
    def __init__(self, declaration_type: type[ast.Global] | type[ast.Nonlocal]):
        self.declaration_type = declaration_type
        self.names: set[str] = set()

    def _visit_scope(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> None:
        declared = _scope_declared_names(node.body, self.declaration_type)
        self.names.update(declared & _scope_mutated_names(node.body))
        for statement in node.body:
            self.visit(statement)

    visit_FunctionDef = _visit_scope
    visit_AsyncFunctionDef = _visit_scope
    visit_ClassDef = _visit_scope


def _nested_declaration_mutations(
    nodes: Iterable[ast.stmt],
    declaration_type: type[ast.Global] | type[ast.Nonlocal],
) -> set[str]:
    visitor = _NestedDeclarationMutationVisitor(declaration_type)
    for node in nodes:
        visitor.visit(node)
    return visitor.names


def _nested_global_mutations(tree: ast.Module) -> set[str]:
    return _nested_declaration_mutations(tree.body, ast.Global)


def _nested_nonlocal_mutations(nodes: Iterable[ast.stmt]) -> set[str]:
    return _nested_declaration_mutations(nodes, ast.Nonlocal)


class _AttributeMutationVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.roots: set[str] = set()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            root: ast.expr = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                self.roots.add(root.id)
        self.generic_visit(node)


def _attribute_mutated_roots(tree: ast.Module) -> set[str]:
    visitor = _AttributeMutationVisitor()
    visitor.visit(tree)
    return visitor.roots


def _trusted_import_bindings(
    tree: ast.Module,
    *,
    modules: set[str],
    symbol: str,
) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    module_aliases: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in modules:
            names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == symbol
            )
        elif isinstance(node, ast.Import):
            module_aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name in modules
            )
    counts = _scope_binding_counts(tree.body)
    globally_mutated = _nested_global_mutations(tree)
    attribute_mutated = _attribute_mutated_roots(tree)
    return (
        {
            name for name in names
            if counts[name] == 1 and name not in globally_mutated
        },
        {
            name for name in module_aliases
            if (
                counts[name] == 1
                and name not in globally_mutated
                and name not in attribute_mutated
            )
        },
    )


def _protocol_bindings(tree: ast.Module) -> tuple[set[str], set[str]]:
    return _trusted_import_bindings(
        tree, modules={"typing", "typing_extensions"}, symbol="Protocol"
    )


def _bound_names(nodes: Iterable[ast.stmt]) -> set[str]:
    """Return names rebound directly in one lexical scope."""
    return set(_scope_binding_counts(nodes))


def _abc_bindings(tree: ast.Module) -> tuple[set[str], set[str]]:
    return _trusted_import_bindings(tree, modules={"abc"}, symbol="abstractmethod")


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


def _is_named_or_qualified(
    node: ast.expr,
    names: set[str],
    modules: set[str],
    attribute: str,
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in names
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attribute
        and isinstance(node.value, ast.Name)
        and node.value.id in modules
    )


def scan(paths: Iterable[Path]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        module = ".".join(relative.with_suffix("").parts[1:])
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        protocol_names, protocol_modules = _protocol_bindings(tree)
        abstractmethod_names, abc_modules = _abc_bindings(tree)
        abc_names, _ = _trusted_import_bindings(
            tree, modules={"abc"}, symbol="ABC"
        )
        abc_meta_names, _ = _trusted_import_bindings(
            tree, modules={"abc"}, symbol="ABCMeta"
        )
        visitor = _FunctionVisitor(
            module, relative, protocol_names, protocol_modules,
            abstractmethod_names, abc_modules, abc_names, abc_meta_names,
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
