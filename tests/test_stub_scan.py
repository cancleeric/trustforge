import ast

import pytest

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
        "from abc import ABC, ABCMeta\n"
        "import abc\n"
        "import abc as standard_abc\n"
        "class Interface(ABC):\n"
        "    @abstractmethod\n"
        "    def direct(self): pass\n"
        "    @required\n"
        "    def aliased(self): ...\n"
        "    @abc.abstractmethod\n"
        "    def qualified(self): raise NotImplementedError\n"
        "    @standard_abc.abstractmethod\n"
        "    def module_alias(self): pass\n"
        "class MetaInterface(metaclass=ABCMeta):\n"
        "    @abstractmethod\n"
        "    def required(self): pass\n"
        "class QualifiedInterface(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def required(self): ...\n",
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
        "from abc import abstractmethod\n"
        "import abc\n"
        "shadowed = lambda fn: fn\n"
        "class evil:\n"
        "    abstractmethod = staticmethod(lambda fn: fn)\n"
        "class Ordinary:\n"
        "    @abstractmethod\n"
        "    def local(self): pass\n"
        "    @evil.abstractmethod\n"
        "    def qualified(self): ...\n"
        "    @shadowed\n"
        "    def rebound(self): raise NotImplementedError\n"
        "    @abstractmethod\n"
        "    def genuine_but_concrete(self): pass\n"
        "class LocallyShadowed:\n"
        "    abc = evil\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.fake_abstracts.LocallyShadowed.method",
        "trustforge.fake_abstracts.Ordinary.genuine_but_concrete",
        "trustforge.fake_abstracts.Ordinary.local",
        "trustforge.fake_abstracts.Ordinary.qualified",
        "trustforge.fake_abstracts.Ordinary.rebound",
    ]


def test_scan_rejects_shadowed_protocol_bindings(tmp_path, monkeypatch):
    root = tmp_path
    source = root / "src/trustforge/fake_protocols.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "from typing import Protocol as InterfaceBase\n"
        "import typing\n"
        "Protocol = object\n"
        "if False:\n"
        "    InterfaceBase = object\n"
        "try:\n"
        "    typing = object\n"
        "except Exception:\n"
        "    pass\n"
        "class Direct(Protocol):\n"
        "    def method(self): ...\n"
        "class Aliased(InterfaceBase):\n"
        "    def method(self): ...\n"
        "class Qualified(typing.Protocol):\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.fake_protocols.Aliased.method",
        "trustforge.fake_protocols.Direct.method",
        "trustforge.fake_protocols.Qualified.method",
    ]


def test_scan_rejects_protocol_direct_name_only_after_later_rebinding(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/protocol_rebound_later.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "class Before(Protocol):\n"
        "    def method(self): ...\n"
        "Protocol = object\n"
        "class After(Protocol):\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.protocol_rebound_later.After.method",
    ]


def test_scan_rejects_protocol_module_alias_only_after_later_rebinding(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/protocol_module_rebound_later.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "class Before(typing.Protocol):\n"
        "    def method(self): ...\n"
        "typing = object\n"
        "class After(typing.Protocol):\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.protocol_module_rebound_later.After.method",
    ]


def test_scan_rejects_abc_module_after_cross_category_reimport(tmp_path, monkeypatch):
    root = tmp_path
    source = root / "src/trustforge/cross_reimport_abc.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import abc\n"
        "from typing import Protocol as abc\n"
        "class Bad(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.cross_reimport_abc.Bad.method",
    ]


def test_scan_rejects_protocol_name_after_cross_category_reimport(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/cross_reimport_protocol_name.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "from abc import ABC as Protocol\n"
        "class Bad(Protocol):\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.cross_reimport_protocol_name.Bad.method",
    ]


def test_scan_rejects_protocol_module_after_cross_category_reimport(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/cross_reimport_protocol_module.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc as typing\n"
        "class Bad(typing.Protocol):\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.cross_reimport_protocol_module.Bad.method",
    ]


def test_scan_rejects_abc_rebinding_in_control_flow(tmp_path, monkeypatch):
    root = tmp_path
    source = root / "src/trustforge/control_flow_shadow.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from abc import ABC, abstractmethod\n"
        "import abc\n"
        "if False:\n"
        "    abstractmethod = lambda fn: fn\n"
        "for abc in ():\n"
        "    pass\n"
        "try:\n"
        "    ABC = object\n"
        "except Exception:\n"
        "    pass\n"
        "class Interface(ABC):\n"
        "    @abstractmethod\n"
        "    def direct(self): pass\n"
        "class Qualified:\n"
        "    @abc.abstractmethod\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.control_flow_shadow.Interface.direct",
        "trustforge.control_flow_shadow.Qualified.method",
    ]


def test_scan_rejects_walrus_shadowing_from_all_comprehension_shapes(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/comprehension_shadow.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "import typing as typing_alias\n"
        "from abc import ABC, abstractmethod as required\n"
        "import abc\n"
        "[(Protocol := object) for _ in ()]\n"
        "{(typing_alias := object) for _ in ()}\n"
        "{_: (required := (lambda fn: fn)) for _ in ()}\n"
        "((abc := object) for _ in ())\n"
        "class Direct(Protocol):\n"
        "    def method(self): ...\n"
        "class Qualified(typing_alias.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(ABC):\n"
        "    @required\n"
        "    def method(self): pass\n"
        "class QualifiedAbstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.comprehension_shadow.Abstract.method",
        "trustforge.comprehension_shadow.Direct.method",
        "trustforge.comprehension_shadow.Qualified.method",
        "trustforge.comprehension_shadow.QualifiedAbstract.method",
    ]


def test_scan_does_not_treat_comprehension_targets_as_outer_shadowing(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/comprehension_targets.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "from abc import ABC, abstractmethod\n"
        "[Protocol for Protocol in ()]\n"
        "{ABC for ABC in ()}\n"
        "{abstractmethod: abstractmethod for abstractmethod in ()}\n"
        "(Protocol for Protocol in ())\n"
        "class Direct(Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(ABC):\n"
        "    @abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


def test_scan_rejects_lambda_default_walrus_shadowing(tmp_path, monkeypatch):
    root = tmp_path
    source = root / "src/trustforge/lambda_default_shadow.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "import abc\n"
        "import abc as abc_alias\n"
        "from abc import abstractmethod as required, ABC as Base, ABCMeta as Meta\n"
        "(lambda value=(Protocol := object): None)\n"
        "(lambda value=(abc_alias := object): None)\n"
        "(lambda value=(required := (lambda fn: fn)): None)\n"
        "(lambda value=(Base := object): None)\n"
        "(lambda *, value=(Meta := type): None)\n"
        "class Direct(Protocol):\n"
        "    def method(self): ...\n"
        "class Qualified(abc_alias.ABC):\n"
        "    @abc_alias.abstractmethod\n"
        "    def method(self): ...\n"
        "class Required(abc.ABC):\n"
        "    @required\n"
        "    def method(self): pass\n"
        "class Based(Base):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n"
        "class MetaBased(metaclass=Meta):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.lambda_default_shadow.Based.method",
        "trustforge.lambda_default_shadow.Direct.method",
        "trustforge.lambda_default_shadow.MetaBased.method",
        "trustforge.lambda_default_shadow.Qualified.method",
        "trustforge.lambda_default_shadow.Required.method",
    ]


def test_scan_does_not_treat_lambda_body_walrus_as_outer_shadowing(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/lambda_body_scope.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "import abc as abc_alias\n"
        "from abc import abstractmethod as required, ABC as Base, ABCMeta as Meta\n"
        "(lambda: (Protocol := object))\n"
        "(lambda: (abc_alias := object))\n"
        "(lambda: (required := object))\n"
        "(lambda: (Base := object))\n"
        "(lambda: (Meta := object))\n"
        "class Direct(Protocol):\n"
        "    def method(self): ...\n"
        "class Based(Base):\n"
        "    @required\n"
        "    def method(self): pass\n"
        "class Qualified(abc_alias.ABC):\n"
        "    @abc_alias.abstractmethod\n"
        "    def method(self): ...\n"
        "class MetaBased(metaclass=Meta):\n"
        "    @required\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


def test_scan_rejects_function_parameter_annotation_walrus_shadowing(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/annotation_shadow.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "import abc as abc_alias\n"
        "from abc import abstractmethod as required, ABC as Base, ABCMeta as Meta\n"
        "def positional(value: (Protocol := object)): pass\n"
        "def positional_only(value: (abc_alias := object), /): pass\n"
        "def keyword_only(*, value: (required := object)): pass\n"
        "def varargs(*values: (Base := object)): pass\n"
        "async def kwargs(**values: (Meta := type)): pass\n"
        "class Direct(Protocol):\n"
        "    def method(self): ...\n"
        "class Qualified(abc_alias.ABC):\n"
        "    @abc_alias.abstractmethod\n"
        "    def method(self): ...\n"
        "class Required:\n"
        "    @required\n"
        "    def method(self): pass\n"
        "class Based(Base):\n"
        "    def method(self): ...\n"
        "class MetaBased(metaclass=Meta):\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.annotation_shadow.Based.method",
        "trustforge.annotation_shadow.Direct.method",
        "trustforge.annotation_shadow.MetaBased.method",
        "trustforge.annotation_shadow.Qualified.method",
        "trustforge.annotation_shadow.Required.method",
        "trustforge.annotation_shadow.keyword_only",
        "trustforge.annotation_shadow.kwargs",
        "trustforge.annotation_shadow.positional",
        "trustforge.annotation_shadow.positional_only",
        "trustforge.annotation_shadow.varargs",
    ]


@pytest.mark.skipif(
    "type_params" not in ast.FunctionDef._fields,
    reason="runtime parser does not support PEP 695 type parameters",
)
def test_scan_rejects_type_parameter_bound_and_default_walrus_shadowing(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/type_parameter_shadow.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "import abc as abc_alias\n"
        "from abc import abstractmethod as required, ABC as Base, ABCMeta as Meta\n"
        "def generic[T: (Protocol := object)](): pass\n"
        "async def async_generic[T = (abc_alias := object)](): pass\n"
        "class Required[T: (required := object)]:\n"
        "    def method(self): ...\n"
        "class Based[T = (Base := object)]:\n"
        "    def method(self): ...\n"
        "class MetaBased[T: (Meta := type)]:\n"
        "    def method(self): ...\n"
        "class Direct(Protocol):\n"
        "    def method(self): ...\n"
        "class Qualified(abc_alias.ABC):\n"
        "    @abc_alias.abstractmethod\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    symbols = [row["symbol"] for row in scan([source])]
    assert "trustforge.type_parameter_shadow.Direct.method" in symbols
    assert "trustforge.type_parameter_shadow.Qualified.method" in symbols
    assert "trustforge.type_parameter_shadow.Required.method" in symbols
    assert "trustforge.type_parameter_shadow.Based.method" in symbols
    assert "trustforge.type_parameter_shadow.MetaBased.method" in symbols


def test_scan_does_not_treat_function_body_walrus_as_outer_shadowing(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/function_body_scope.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "from abc import ABC, abstractmethod\n"
        "def mutate_locally():\n"
        "    (Protocol := object)\n"
        "    (ABC := object)\n"
        "    (abstractmethod := object)\n"
        "class Direct(Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(ABC):\n"
        "    @abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


def test_scan_rejects_all_trusted_bindings_shadowed_in_nested_scopes(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/nested_shadow.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "import typing as typing_alias\n"
        "from abc import abstractmethod as required, ABC as Base, ABCMeta as Meta\n"
        "import abc as abc_alias\n"
        "class Outer:\n"
        "    Protocol = object\n"
        "    typing_alias = object\n"
        "    required = lambda fn: fn\n"
        "    Base = object\n"
        "    Meta = type\n"
        "    abc_alias = object\n"
        "    class Direct(Protocol):\n"
        "        def method(self): ...\n"
        "    class Qualified(typing_alias.Protocol):\n"
        "        def method(self): ...\n"
        "    class Abstract(Base):\n"
        "        @required\n"
        "        def method(self): pass\n"
        "    class MetaAbstract(metaclass=Meta):\n"
        "        @required\n"
        "        def method(self): pass\n"
        "    class QualifiedAbstract(abc_alias.ABC):\n"
        "        @abc_alias.abstractmethod\n"
        "        def method(self): ...\n"
        "def outer(Protocol, typing_alias, required, Base, Meta, abc_alias):\n"
        "    class Direct(Protocol):\n"
        "        def method(self): ...\n"
        "    class Qualified(typing_alias.Protocol):\n"
        "        def method(self): ...\n"
        "    class Abstract(Base):\n"
        "        @required\n"
        "        def method(self): pass\n"
        "    class MetaAbstract(metaclass=Meta):\n"
        "        @required\n"
        "        def method(self): pass\n"
        "    class QualifiedAbstract(abc_alias.ABC):\n"
        "        @abc_alias.abstractmethod\n"
        "        def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    symbols = [row["symbol"] for row in scan([source])]
    assert len(symbols) == 10
    assert "trustforge.nested_shadow.Outer.Direct.method" in symbols
    assert "trustforge.nested_shadow.Outer.Qualified.method" in symbols
    assert "trustforge.nested_shadow.Outer.Abstract.method" in symbols
    assert "trustforge.nested_shadow.Outer.MetaAbstract.method" in symbols
    assert "trustforge.nested_shadow.Outer.QualifiedAbstract.method" in symbols
    assert "trustforge.nested_shadow.outer.Direct.method" in symbols
    assert "trustforge.nested_shadow.outer.Qualified.method" in symbols
    assert "trustforge.nested_shadow.outer.Abstract.method" in symbols
    assert "trustforge.nested_shadow.outer.MetaAbstract.method" in symbols
    assert "trustforge.nested_shadow.outer.QualifiedAbstract.method" in symbols


def test_scan_preserves_trusted_bindings_across_clean_nested_scopes(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/nested_clean.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "import typing as typing_alias\n"
        "from abc import abstractmethod as required, ABC as Base, ABCMeta as Meta\n"
        "import abc as abc_alias\n"
        "class Outer:\n"
        "    class Direct(Protocol):\n"
        "        def method(self): ...\n"
        "    class Qualified(typing_alias.Protocol):\n"
        "        def method(self): ...\n"
        "    class Abstract(Base):\n"
        "        @required\n"
        "        def method(self): pass\n"
        "    class MetaAbstract(metaclass=Meta):\n"
        "        @required\n"
        "        def method(self): pass\n"
        "    class QualifiedAbstract(abc_alias.ABC):\n"
        "        @abc_alias.abstractmethod\n"
        "        def method(self): ...\n"
        "def outer():\n"
        "    class Direct(Protocol):\n"
        "        def method(self): ...\n"
        "    class Abstract(Base):\n"
        "        @required\n"
        "        def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


def test_scan_revokes_module_trust_for_nested_global_mutations(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/global_mutation.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "import typing as typing_alias\n"
        "from abc import abstractmethod as required, ABC as Base, ABCMeta as Meta\n"
        "import abc as abc_alias\n"
        "class Container:\n"
        "    def mutate(self):\n"
        "        global Protocol, typing_alias, required, Base, Meta, abc_alias\n"
        "        Protocol = object\n"
        "        typing_alias = object\n"
        "        required = lambda fn: fn\n"
        "        Base = object\n"
        "        Meta = type\n"
        "        del abc_alias\n"
        "class Direct(Protocol):\n"
        "    def method(self): ...\n"
        "class Qualified(typing_alias.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(Base):\n"
        "    @required\n"
        "    def method(self): pass\n"
        "class MetaAbstract(metaclass=Meta):\n"
        "    @required\n"
        "    def method(self): pass\n"
        "class QualifiedAbstract(abc_alias.ABC):\n"
        "    @abc_alias.abstractmethod\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    symbols = [row["symbol"] for row in scan([source])]
    assert "trustforge.global_mutation.Direct.method" in symbols
    assert "trustforge.global_mutation.Qualified.method" in symbols
    assert "trustforge.global_mutation.Abstract.method" in symbols
    assert "trustforge.global_mutation.MetaAbstract.method" in symbols
    assert "trustforge.global_mutation.QualifiedAbstract.method" in symbols


def test_scan_keeps_module_trust_for_nested_global_read_only(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/global_read.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "from abc import ABC, abstractmethod\n"
        "def inspect():\n"
        "    global Protocol, ABC, abstractmethod\n"
        "    return Protocol, ABC, abstractmethod\n"
        "class Direct(Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(ABC):\n"
        "    @abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


def test_scan_revokes_enclosing_trust_for_nested_nonlocal_mutations(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/nonlocal_mutation.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "import abc as abc_alias\n"
        "def outer():\n"
        "    def mutate():\n"
        "        nonlocal Protocol, abc_alias\n"
        "        Protocol = object\n"
        "        del abc_alias\n"
        "    class Direct(Protocol):\n"
        "        def method(self): ...\n"
        "    class Qualified(abc_alias.ABC):\n"
        "        @abc_alias.abstractmethod\n"
        "        def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.nonlocal_mutation.outer.Direct.method",
        "trustforge.nonlocal_mutation.outer.Qualified.method",
    ]


def test_scan_keeps_enclosing_trust_for_nested_nonlocal_read_only(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/nonlocal_read.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "from abc import ABC, abstractmethod\n"
        "def outer():\n"
        "    def inspect():\n"
        "        nonlocal Protocol, ABC, abstractmethod\n"
        "        return Protocol, ABC, abstractmethod\n"
        "    class Direct(Protocol):\n"
        "        def method(self): ...\n"
        "    class Abstract(ABC):\n"
        "        @abstractmethod\n"
        "        def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


def test_scan_revokes_module_trust_for_class_body_global_mutations(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/class_global_mutation.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "import typing as typing_alias\n"
        "from abc import abstractmethod as required, ABC as Base, ABCMeta as Meta\n"
        "import abc as abc_alias\n"
        "class Mutator:\n"
        "    global Protocol, typing_alias, required, Base, Meta, abc_alias\n"
        "    Protocol = object\n"
        "    typing_alias = object\n"
        "    required = lambda fn: fn\n"
        "    Base = object\n"
        "    Meta = type\n"
        "    del abc_alias\n"
        "class Direct(Protocol):\n"
        "    def method(self): ...\n"
        "class Qualified(typing_alias.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(Base):\n"
        "    @required\n"
        "    def method(self): pass\n"
        "class MetaAbstract(metaclass=Meta):\n"
        "    @required\n"
        "    def method(self): pass\n"
        "class QualifiedAbstract(abc_alias.ABC):\n"
        "    @abc_alias.abstractmethod\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    symbols = [row["symbol"] for row in scan([source])]
    assert "trustforge.class_global_mutation.Direct.method" in symbols
    assert "trustforge.class_global_mutation.Qualified.method" in symbols
    assert "trustforge.class_global_mutation.Abstract.method" in symbols
    assert "trustforge.class_global_mutation.MetaAbstract.method" in symbols
    assert "trustforge.class_global_mutation.QualifiedAbstract.method" in symbols


def test_scan_revokes_function_trust_for_class_body_nonlocal_mutations(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/class_nonlocal_mutation.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "import typing as typing_alias\n"
        "from abc import abstractmethod as required, ABC as Base, ABCMeta as Meta\n"
        "import abc as abc_alias\n"
        "def outer():\n"
        "    class Mutator:\n"
        "        nonlocal Protocol, typing_alias, required, Base, Meta, abc_alias\n"
        "        Protocol = object\n"
        "        typing_alias = object\n"
        "        required = lambda fn: fn\n"
        "        Base = object\n"
        "        Meta = type\n"
        "        del abc_alias\n"
        "    class Direct(Protocol):\n"
        "        def method(self): ...\n"
        "    class Qualified(typing_alias.Protocol):\n"
        "        def method(self): ...\n"
        "    class Abstract(Base):\n"
        "        @required\n"
        "        def method(self): pass\n"
        "    class MetaAbstract(metaclass=Meta):\n"
        "        @required\n"
        "        def method(self): pass\n"
        "    class QualifiedAbstract(abc_alias.ABC):\n"
        "        @abc_alias.abstractmethod\n"
        "        def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    symbols = [row["symbol"] for row in scan([source])]
    assert "trustforge.class_nonlocal_mutation.outer.Direct.method" in symbols
    assert "trustforge.class_nonlocal_mutation.outer.Qualified.method" in symbols
    assert "trustforge.class_nonlocal_mutation.outer.Abstract.method" in symbols
    assert "trustforge.class_nonlocal_mutation.outer.MetaAbstract.method" in symbols
    assert "trustforge.class_nonlocal_mutation.outer.QualifiedAbstract.method" in symbols


def test_scan_keeps_trust_for_class_body_declaration_reads(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/class_declaration_read.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from typing import Protocol\n"
        "from abc import ABC, abstractmethod\n"
        "class GlobalReader:\n"
        "    global Protocol, ABC, abstractmethod\n"
        "    values = Protocol, ABC, abstractmethod\n"
        "def outer():\n"
        "    class NonlocalReader:\n"
        "        nonlocal Protocol, ABC, abstractmethod\n"
        "        values = Protocol, ABC, abstractmethod\n"
        "    class Direct(Protocol):\n"
        "        def method(self): ...\n"
        "    class Abstract(ABC):\n"
        "        @abstractmethod\n"
        "        def method(self): pass\n"
        "class Direct(Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(ABC):\n"
        "    @abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


def test_scan_revokes_trusted_module_alias_on_attribute_store_or_delete(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/module_attribute_mutation.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "def mutate_typing():\n"
        "    typing.Protocol = object\n"
        "class MutateAbc:\n"
        "    del abc.abstractmethod\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n"
        "class MetaAbstract(metaclass=abc.ABCMeta):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.module_attribute_mutation.Abstract.method",
        "trustforge.module_attribute_mutation.Direct.method",
        "trustforge.module_attribute_mutation.MetaAbstract.method",
    ]


def test_scan_keeps_trusted_module_alias_on_attribute_reads(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/module_attribute_read.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "def inspect():\n"
        "    return typing.Protocol, abc.ABC, abc.ABCMeta, abc.abstractmethod\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n"
        "class MetaAbstract(metaclass=abc.ABCMeta):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


def test_scan_revokes_module_trust_after_object_alias_escape_and_mutation(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/module_alias_escape.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import typing_extensions as typing_ext\n"
        "import abc\n"
        "typing_copy = typing\n"
        "abc_copy: object = abc\n"
        "(typing_ext_copy := typing_ext)\n"
        "typing_copy.Protocol = object\n"
        "del abc_copy.ABC\n"
        "abc.ABCMeta += ()\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Extension(typing_ext.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.module_alias_escape.Abstract.method",
        "trustforge.module_alias_escape.Direct.method",
        "trustforge.module_alias_escape.Extension.method",
    ]


def test_scan_revokes_module_trust_for_explicit_setattr_and_delattr(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/module_dynamic_attribute.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "setattr(typing, 'Protocol', object)\n"
        "abc_copy = abc\n"
        "delattr(abc_copy, 'abstractmethod')\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.module_dynamic_attribute.Abstract.method",
        "trustforge.module_dynamic_attribute.Direct.method",
    ]


def test_scan_keeps_module_trust_for_read_only_attribute_extraction(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/module_attribute_extract.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "protocol_type = typing.Protocol\n"
        "abstract_type = abc.ABC\n"
        "decorator = abc.abstractmethod\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


def test_scan_revokes_module_trust_for_nested_destructuring_aliases(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/destructuring_alias.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "(typing_copy, [marker, (abc_copy, tail)]) = "
        "(typing, [0, (abc, 1)])\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.destructuring_alias.Abstract.method",
        "trustforge.destructuring_alias.Direct.method",
    ]


def test_scan_revokes_module_trust_for_unresolved_destructuring(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/unresolved_destructuring.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "(head, *rest) = (typing, abc)\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.unresolved_destructuring.Abstract.method",
        "trustforge.unresolved_destructuring.Direct.method",
    ]


def test_scan_keeps_module_trust_for_nested_read_only_destructuring(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/read_only_destructuring.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "(protocol_type, [abstract_type, decorator]) = "
        "(typing.Protocol, [abc.ABC, abc.abstractmethod])\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


def test_scan_revokes_module_trust_for_for_and_async_for_aliases(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/loop_aliases.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "for typing_copy in (typing,):\n"
        "    typing_copy.Protocol = object\n"
        "async def mutate():\n"
        "    async for abc_copy in (abc,):\n"
        "        del abc_copy.abstractmethod\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.loop_aliases.Abstract.method",
        "trustforge.loop_aliases.Direct.method",
    ]


def test_scan_revokes_module_trust_for_nested_comprehension_aliases(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/comprehension_aliases.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "[(typing_copy, abc_copy) for "
        "(typing_copy, (abc_copy, marker)) in [(typing, (abc, 0))]]\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.comprehension_aliases.Abstract.method",
        "trustforge.comprehension_aliases.Direct.method",
    ]


def test_scan_keeps_module_trust_for_read_only_loop_values(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/read_only_loops.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "for protocol_type in (typing.Protocol,):\n"
        "    pass\n"
        "[abstract_type for abstract_type in (abc.ABC,)]\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


def test_scan_revokes_module_trust_for_rhs_starred_unpacking(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/starred_unpacking.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "(typing_copy,) = (*[typing],)\n"
        "[head, (abc_copy, tail)] = [0, (*[abc],)]\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.starred_unpacking.Abstract.method",
        "trustforge.starred_unpacking.Direct.method",
    ]


def test_scan_revokes_module_trust_for_call_wrapped_and_set_iterables(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/wrapped_iterables.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "for typing_copy in iter((typing,)):\n"
        "    typing_copy.Protocol = object\n"
        "[abc_copy for abc_copy in set([abc])]\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.wrapped_iterables.Abstract.method",
        "trustforge.wrapped_iterables.Direct.method",
    ]


def test_scan_revokes_module_trust_for_other_unresolved_iterable_shapes(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/non_tuple_iterables.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "for typing_copy in {typing}:\n"
        "    pass\n"
        "(abc_copy for abc_copy in (abc + ()))\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.non_tuple_iterables.Abstract.method",
        "trustforge.non_tuple_iterables.Direct.method",
    ]


def test_scan_keeps_module_trust_for_wrapped_attribute_read_iterables(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/read_only_wrapped_iterables.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "for protocol_type in iter((typing.Protocol,)):\n"
        "    pass\n"
        "[abstract_type for abstract_type in set([abc.ABC])]\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


def test_scan_revokes_module_trust_for_compound_attribute_receivers(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/compound_receivers.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "for item in (typing,).__getitem__(0):\n"
        "    pass\n"
        "[item for item in [abc].pop()]\n"
        "for item in {'module': typing}.values():\n"
        "    pass\n"
        "[item for item in (abc,).__iter__()]\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.compound_receivers.Abstract.method",
        "trustforge.compound_receivers.Direct.method",
    ]


def test_scan_revokes_module_trust_for_nonmember_module_attributes(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/nonmember_attributes.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "for item in typing.__dict__.values():\n"
        "    pass\n"
        "[item for item in abc.__dict__.values()]\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.nonmember_attributes.Abstract.method",
        "trustforge.nonmember_attributes.Direct.method",
    ]


def test_scan_revokes_module_trust_for_function_and_lambda_defaults(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/default_escape.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "def sync_default(module=typing): pass\n"
        "async def async_default(*, module=abc): pass\n"
        "(lambda module=typing: module)\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    symbols = [row["symbol"] for row in scan([source])]
    assert "trustforge.default_escape.Direct.method" in symbols
    assert "trustforge.default_escape.Abstract.method" in symbols


def test_scan_revokes_module_trust_for_call_arguments(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/call_escape.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "consume(typing)\n"
        "consume(module=abc)\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.call_escape.Abstract.method",
        "trustforge.call_escape.Direct.method",
    ]


def test_scan_keeps_module_trust_for_member_defaults_and_call_arguments(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/member_value_calls.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "def sync_default(member=typing.Protocol): return member\n"
        "async def async_default(*, member=abc.ABC): return member\n"
        "(lambda member=abc.abstractmethod: member)\n"
        "consume(typing.Protocol, abstract=abc.ABCMeta)\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


def test_scan_revokes_module_trust_for_return_yield_and_yield_from(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/return_escape.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "def returned(): return typing\n"
        "def yielded(): yield abc\n"
        "def delegated(): yield from typing\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.return_escape.Abstract.method",
        "trustforge.return_escape.Direct.method",
    ]


def test_scan_revokes_module_trust_for_evaluated_function_annotations(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/function_annotation_escape.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "def sync(posonly: typing, /, positional: abc, *values: typing,\n"
        "         keyword: abc, **options: typing) -> abc: return 1\n"
        "async def async_annotated(value: typing) -> abc: return 1\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.function_annotation_escape.Abstract.method",
        "trustforge.function_annotation_escape.Direct.method",
    ]


def test_scan_revokes_module_trust_for_annotated_assignment_annotation(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/assignment_annotation_escape.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "value: typing\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.assignment_annotation_escape.Direct.method",
    ]


def test_scan_keeps_module_trust_for_safe_member_returns_yields_and_annotations(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/safe_evaluated_values.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "value: typing.Protocol\n"
        "def returned(value: typing.Protocol, *args: abc.ABC,\n"
        "             keyword: abc.ABCMeta, **kwargs: typing.Protocol)"
        " -> abc.ABC: return typing.Protocol\n"
        "async def yielded(value: abc.ABC) -> typing.Protocol:\n"
        "    yield abc.ABC\n"
        "def delegated(): yield from (typing.Protocol,)\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


@pytest.mark.parametrize(
    "declaration",
    [
        "def generic[T: typing = abc](): return 1",
        "class Generic[T: typing = abc]: pass",
        "type Generic[T: typing = abc] = T",
    ],
)
def test_scan_revokes_module_trust_for_type_parameter_bounds_and_defaults(
    tmp_path, monkeypatch, declaration
):
    root = tmp_path
    source = root / "src/trustforge/type_parameter_escape.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        f"{declaration}\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.type_parameter_escape.Abstract.method",
        "trustforge.type_parameter_escape.Direct.method",
    ]


def test_scan_keeps_module_trust_for_safe_member_type_parameters(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/safe_type_parameters.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "def function[T: typing.Protocol = abc.ABC](): return 1\n"
        "async def async_function[T: abc.ABC = typing.Protocol](): return 1\n"
        "class Generic[T: abc.ABCMeta = typing.Protocol]: pass\n"
        "type Alias[T: typing.Protocol = abc.ABC] = T\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []


@pytest.mark.parametrize(
    "pattern",
    [
        "captured",
        '{"module": captured}',
        '[_, {"module": captured}]',
    ],
)
def test_scan_revokes_module_trust_for_match_subject_captures(
    tmp_path, monkeypatch, pattern
):
    root = tmp_path
    source = root / "src/trustforge/match_escape.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "match typing:\n"
        f"    case {pattern}:\n"
        "        pass\n"
        "match abc:\n"
        f"    case {pattern}:\n"
        "        pass\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert [row["symbol"] for row in scan([source])] == [
        "trustforge.match_escape.Abstract.method",
        "trustforge.match_escape.Direct.method",
    ]


def test_scan_keeps_module_trust_for_safe_member_match_subjects(
    tmp_path, monkeypatch
):
    root = tmp_path
    source = root / "src/trustforge/safe_match_subjects.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import typing\n"
        "import abc\n"
        "match typing.Protocol:\n"
        "    case protocol:\n"
        "        pass\n"
        "match abc.ABC:\n"
        '    case {"module": captured}:\n'
        "        pass\n"
        "match (typing.Protocol, abc.ABCMeta):\n"
        '    case [_, {"module": captured}]:\n'
        "        pass\n"
        "class Direct(typing.Protocol):\n"
        "    def method(self): ...\n"
        "class Abstract(abc.ABC):\n"
        "    @abc.abstractmethod\n"
        "    def method(self): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.scan_source_stubs.ROOT", root)

    assert scan([source]) == []
