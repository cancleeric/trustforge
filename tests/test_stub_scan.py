import ast

from scripts.scan_source_stubs import _stub_kind, scan


def _function(source: str):
    return ast.parse(source).body[0]


def _scan_symbols(tmp_path, monkeypatch, source_text):
    source = tmp_path / "src/trustforge/control_flow.py"
    source.parent.mkdir(parents=True)
    source.write_text(source_text, encoding="utf-8")
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", tmp_path)
    return [row["symbol"] for row in scan([source])]


def test_stub_kind_detects_empty_shapes_but_not_real_functions():
    assert _stub_kind(_function("def f():\n    pass\n")) == "pass"
    assert _stub_kind(_function("def f():\n    ...\n")) == "ellipsis"
    assert _stub_kind(_function("def f():\n    raise NotImplementedError('issue #1')\n")) == "not_implemented"
    assert _stub_kind(_function("def f():\n    return None\n")) is None
    assert _stub_kind(_function("@abstractmethod\ndef f():\n    pass\n")) is None


def test_scan_uses_stable_qualified_symbols(tmp_path, monkeypatch):
    root = tmp_path
    source = root / "src/trustforge/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("class Connector:\n    def fetch(self):\n        pass\n", encoding="utf-8")
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == [{
        "symbol": "trustforge.example.Connector.fetch", "kind": "pass",
        "path": "src/trustforge/example.py", "line": 2,
    }]


def test_scan_ignores_only_direct_protocol_interface_methods(tmp_path, monkeypatch):
    root = tmp_path
    source = root / "src/trustforge/protocols.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "import typing\n"
        "import typing_extensions as te\n"
        "from typing_extensions import Protocol as ExtensionProtocol\n"
        "class Direct(Protocol):\n"
        "    def sync(self): ...\n"
        "    async def async_method(self): ...\n"
        "class Qualified(typing.Protocol):\n"
        "    def sync(self): ...\n"
        "class Extension(te.Protocol):\n"
        "    def sync(self): ...\n"
        "class Aliased(ExtensionProtocol):\n"
        "    def sync(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


def test_scan_keeps_ordinary_nested_and_indirect_class_stubs(tmp_path, monkeypatch):
    root = tmp_path
    source = root / "src/trustforge/ordinary.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "class LocalProtocol: pass\n"
        "class Interface(Protocol):\n"
        "    def method(self):\n"
        "        def nested(): ...\n"
        "class Indirect(Interface):\n"
        "    def method(self): ...\n"
        "class Ordinary:\n"
        "    async def method(self): ...\n"
        "class NotTyping(LocalProtocol):\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.ordinary.Indirect.method",
        "trustforge.ordinary.Interface.method.nested",
        "trustforge.ordinary.NotTyping.method",
        "trustforge.ordinary.Ordinary.method",
    ]


def test_scan_invalidates_direct_protocol_binding_in_statement_order(tmp_path, monkeypatch):
    root = tmp_path
    source = root / "src/trustforge/direct_rebinding.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class BeforeImport(Protocol):\n"
        "    def method(self): ...\n"
        "from typing import Protocol\n"
        "class RealProtocol(Protocol):\n"
        "    def method(self): ...\n"
        "Protocol = object\n"
        "class AfterAssign(Protocol):\n"
        "    def method(self): ...\n"
        "from typing_extensions import Protocol\n"
        "class AfterReimport(Protocol):\n"
        "    def method(self): ...\n"
        "from elsewhere import Protocol\n"
        "class AfterOtherImport(Protocol):\n"
        "    def method(self): ...\n"
        "from typing import Protocol\n"
        "del Protocol\n"
        "class AfterDelete(Protocol):\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.direct_rebinding.AfterAssign.method",
        "trustforge.direct_rebinding.AfterDelete.method",
        "trustforge.direct_rebinding.AfterOtherImport.method",
        "trustforge.direct_rebinding.BeforeImport.method",
    ]


def test_scan_invalidates_qualified_protocol_binding_in_statement_order(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/qualified_rebinding.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing as t\n"
        "class RealProtocol(t.Protocol):\n"
        "    def method(self): ...\n"
        "t = object\n"
        "class AfterAssign(t.Protocol):\n"
        "    def method(self): ...\n"
        "import typing_extensions as t\n"
        "class AfterReimport(t.Protocol):\n"
        "    def method(self): ...\n"
        "from elsewhere import value as t\n"
        "class AfterOtherImport(t.Protocol):\n"
        "    def method(self): ...\n"
        "import typing as t\n"
        "class t:\n"
        "    Protocol = object\n"
        "class AfterDefinition(t.Protocol):\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.qualified_rebinding.AfterAssign.method",
        "trustforge.qualified_rebinding.AfterDefinition.method",
        "trustforge.qualified_rebinding.AfterOtherImport.method",
    ]


def test_scan_requires_protocol_binding_on_every_if_and_try_path(
    tmp_path, monkeypatch
):
    symbols = _scan_symbols(
        tmp_path,
        monkeypatch,
        "condition = True\n"
        "from typing import Protocol\n"
        "if condition:\n"
        "    Protocol = object\n"
        "class AfterIf(Protocol):\n"
        "    def method(self): ...\n"
        "if condition:\n"
        "    from typing import Protocol\n"
        "else:\n"
        "    from typing_extensions import Protocol\n"
        "class DefiniteAfterIf(Protocol):\n"
        "    def method(self): ...\n"
        "try:\n"
        "    Protocol = object\n"
        "    risky()\n"
        "except Exception:\n"
        "    class InHandler(Protocol):\n"
        "        def method(self): ...\n"
        "class AfterTry(Protocol):\n"
        "    def method(self): ...\n"
        "try:\n"
        "    from typing import Protocol\n"
        "except* ValueError:\n"
        "    Protocol = object\n"
        "class AfterTryStar(Protocol):\n"
        "    def method(self): ...\n",
    )

    assert symbols == [
        "trustforge.control_flow.AfterIf.method",
        "trustforge.control_flow.AfterTry.method",
        "trustforge.control_flow.AfterTryStar.method",
        "trustforge.control_flow.InHandler.method",
    ]


def test_scan_invalidates_loop_with_and_named_expression_bindings(
    tmp_path, monkeypatch
):
    symbols = _scan_symbols(
        tmp_path,
        monkeypatch,
        "from typing import Protocol\n"
        "for Protocol in values:\n"
        "    class InFor(Protocol):\n"
        "        def method(self): ...\n"
        "else:\n"
        "    class InForElse(Protocol):\n"
        "        def method(self): ...\n"
        "class AfterFor(Protocol):\n"
        "    def method(self): ...\n"
        "from typing import Protocol\n"
        "with manager() as Protocol:\n"
        "    class InWith(Protocol):\n"
        "        def method(self): ...\n"
        "class AfterWith(Protocol):\n"
        "    def method(self): ...\n"
        "from typing import Protocol\n"
        "if (Protocol := object):\n"
        "    pass\n"
        "class AfterNamedExpr(Protocol):\n"
        "    def method(self): ...\n",
    )

    assert symbols == [
        "trustforge.control_flow.AfterFor.method",
        "trustforge.control_flow.AfterNamedExpr.method",
        "trustforge.control_flow.AfterWith.method",
        "trustforge.control_flow.InFor.method",
        "trustforge.control_flow.InForElse.method",
        "trustforge.control_flow.InWith.method",
    ]


def test_scan_invalidates_async_loop_and_with_bindings(tmp_path, monkeypatch):
    symbols = _scan_symbols(
        tmp_path,
        monkeypatch,
        "import typing as t\n"
        "async for t in values:\n"
        "    class InAsyncFor(t.Protocol):\n"
        "        def method(self): ...\n"
        "class AfterAsyncFor(t.Protocol):\n"
        "    def method(self): ...\n"
        "import typing as t\n"
        "async with manager() as t:\n"
        "    class InAsyncWith(t.Protocol):\n"
        "        def method(self): ...\n"
        "class AfterAsyncWith(t.Protocol):\n"
        "    def method(self): ...\n",
    )

    assert symbols == [
        "trustforge.control_flow.AfterAsyncFor.method",
        "trustforge.control_flow.AfterAsyncWith.method",
        "trustforge.control_flow.InAsyncFor.method",
        "trustforge.control_flow.InAsyncWith.method",
    ]


def test_scan_invalidates_match_captures_and_protocol_attribute_mutation(
    tmp_path, monkeypatch
):
    symbols = _scan_symbols(
        tmp_path,
        monkeypatch,
        "import typing as t\n"
        "match value:\n"
        "    case {'protocol': t}:\n"
        "        class InMatch(t.Protocol):\n"
        "            def method(self): ...\n"
        "class AfterMatch(t.Protocol):\n"
        "    def method(self): ...\n"
        "import typing as t\n"
        "t.Protocol = object\n"
        "class AfterAttributeAssign(t.Protocol):\n"
        "    def method(self): ...\n"
        "import typing as t\n"
        "t.Protocol += marker\n"
        "class AfterAttributeAugAssign(t.Protocol):\n"
        "    def method(self): ...\n"
        "import typing as t\n"
        "del t.Protocol\n"
        "class AfterAttributeDelete(t.Protocol):\n"
        "    def method(self): ...\n",
    )

    assert symbols == [
        "trustforge.control_flow.AfterAttributeAssign.method",
        "trustforge.control_flow.AfterAttributeAugAssign.method",
        "trustforge.control_flow.AfterAttributeDelete.method",
        "trustforge.control_flow.AfterMatch.method",
        "trustforge.control_flow.InMatch.method",
    ]


def test_scan_preserves_only_definite_bindings_through_control_flow(
    tmp_path, monkeypatch
):
    symbols = _scan_symbols(
        tmp_path,
        monkeypatch,
        "from typing import Protocol\n"
        "for item in values:\n"
        "    pass\n"
        "with manager() as item:\n"
        "    pass\n"
        "match value:\n"
        "    case 1:\n"
        "        pass\n"
        "try:\n"
        "    Protocol = object\n"
        "finally:\n"
        "    from typing_extensions import Protocol\n"
        "class DefiniteProtocol(Protocol):\n"
        "    def method(self): ...\n"
        "import typing as t\n"
        "if condition:\n"
        "    import typing as t\n"
        "else:\n"
        "    import typing_extensions as t\n"
        "class DefiniteQualified(t.Protocol):\n"
        "    def method(self): ...\n",
    )

    assert symbols == []
