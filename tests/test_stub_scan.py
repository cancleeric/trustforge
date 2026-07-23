import ast

from scripts.scan_source_stubs import _stub_kind, scan


def _function(source: str):
    return ast.parse(source).body[0]


def test_stub_kind_detects_empty_shapes_but_not_real_functions():
    assert _stub_kind(_function("def f():\n    pass\n")) == "pass"
    assert _stub_kind(_function("def f():\n    ...\n")) == "ellipsis"
    assert _stub_kind(_function("def f():\n    raise NotImplementedError('issue #1')\n")) == "not_implemented"
    assert _stub_kind(_function("def f():\n    return None\n")) is None
    assert _stub_kind(_function("@abstractmethod\ndef f():\n    pass\n")) == "pass"


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


def test_scan_ignores_only_abstractmethod_imported_from_abc(tmp_path, monkeypatch):
    root = tmp_path
    source = root / "src/trustforge/abstracts.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from abc import abstractmethod\n"
        "from abc import abstractmethod as required\n"
        "import abc\n"
        "import abc as standard_abc\n"
        "class Interface:\n"
        "    @abstractmethod\n"
        "    def direct(self): pass\n"
        "    @required\n"
        "    def aliased(self): ...\n"
        "    @abc.abstractmethod\n"
        "    def qualified(self): raise NotImplementedError\n"
        "    @standard_abc.abstractmethod\n"
        "    def module_alias(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


def test_scan_rejects_untrusted_and_shadowed_abstractmethod_names(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/fake_abstracts.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from abc import abstractmethod as shadowed\n"
        "import abc\n"
        "shadowed = lambda fn: fn\n"
        "class evil:\n"
        "    abstractmethod = staticmethod(lambda fn: fn)\n"
        "def abstractmethod(fn): return fn\n"
        "class Ordinary:\n"
        "    @abstractmethod\n"
        "    def local(self): pass\n"
        "    @evil.abstractmethod\n"
        "    def qualified(self): ...\n"
        "    @shadowed\n"
        "    def rebound(self): raise NotImplementedError\n"
        "class LocallyShadowed:\n"
        "    abc = evil\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.fake_abstracts.LocallyShadowed.method",
        "trustforge.fake_abstracts.Ordinary.local",
        "trustforge.fake_abstracts.Ordinary.qualified",
        "trustforge.fake_abstracts.Ordinary.rebound",
    ]
