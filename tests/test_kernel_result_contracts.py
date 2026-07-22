"""Golden and boundary tests for versioned kernel result contracts (#450)."""

from __future__ import annotations

import dataclasses
import json

import pytest

from trustforge.ingestion.base import Document
from trustforge.trust.scoring import Claim, ScoredClaim, TrustedBrief
from trustforge_core import (
    KERNEL_CONTRACT_VERSION,
    KernelClaim,
    KernelDocument,
    KernelInput,
    KernelOutput,
    KernelReputationTrace,
    KernelScoredClaim,
    UnsupportedKernelContractVersion,
    require_supported_contract_version,
)


def test_legacy_dto_attribute_shape_golden():
    """Freeze legacy DTO attributes before adding v2 result contracts."""
    document = Document(
        "doc-1",
        "regulatory",
        "sec",
        "ETF inflows increased",
        ts=1_700_000_000.0,
        url="https://example.test/doc-1",
        meta={},
    )
    claim = Claim("claim-1", document.text, document, "fact", "bullish")
    scored = ScoredClaim(
        claim,
        0.82,
        {"source": 0.9, "recency": 0.8},
        {
            "source": "sec",
            "prior": 0.75,
            "final": 0.9,
            "agree_n": 2,
            "contradict_n": 1,
            "iterations_run": 3,
        },
        ["pump"],
        ["similar_text"],
    )
    contrary_document = Document(
        "doc-2",
        "news",
        "wire",
        "Outflows may increase",
        ts=1_700_000_010.0,
        url="https://example.test/doc-2",
        meta={},
    )
    contrary_claim = Claim(
        "claim-2", contrary_document.text, contrary_document, "inference", "bearish"
    )
    contrary = ScoredClaim(
        contrary_claim,
        0.31,
        {"source": 0.4, "recency": 0.7},
    )
    brief = TrustedBrief(
        "BTC outlook",
        [scored],
        [contrary],
        0.82,
        calibrated_confidence=0.71,
    )

    all_scored = (*brief.supporting, *brief.contrarian)

    def report_value(item: ScoredClaim) -> dict:
        return {
            "claim_id": item.claim.id,
            "trust": item.trust,
            "components": item.components,
            "reputation_trace": item.reputation_trace,
            "manip_flags": item.manip_flags,
            "info_flags": item.info_flags,
        }

    actual = {
        "query": brief.query,
        "scored_claims": [report_value(item) for item in all_scored],
        "supporting": [item.claim.id for item in brief.supporting],
        "contrarian": [item.claim.id for item in brief.contrarian],
        "confidence": brief.confidence,
        "calibrated_confidence": brief.calibrated_confidence,
    }

    assert actual == {
        "query": "BTC outlook",
        "scored_claims": [
            {
                "claim_id": "claim-1",
                "trust": 0.82,
                "components": {"source": 0.9, "recency": 0.8},
                "reputation_trace": {
                    "source": "sec",
                    "prior": 0.75,
                    "final": 0.9,
                    "agree_n": 2,
                    "contradict_n": 1,
                    "iterations_run": 3,
                },
                "manip_flags": ["pump"],
                "info_flags": ["similar_text"],
            },
            {
                "claim_id": "claim-2",
                "trust": 0.31,
                "components": {"source": 0.4, "recency": 0.7},
                "reputation_trace": None,
                "manip_flags": [],
                "info_flags": [],
            },
        ],
        "supporting": ["claim-1"],
        "contrarian": ["claim-2"],
        "confidence": 0.82,
        "calibrated_confidence": 0.71,
    }


def _core_result() -> KernelOutput:
    supporting_claim = KernelClaim(
        "claim-1",
        "ETF inflows increased",
        KernelDocument(
            "doc-1",
            "regulatory",
            "sec",
            "ETF inflows increased",
            1_700_000_000.0,
            "https://example.test/doc-1",
        ),
        "fact",
        "bullish",
    )
    supporting = KernelScoredClaim(
        supporting_claim,
        0.82,
        (("source", 0.9), ("recency", 0.8)),
        KernelReputationTrace("sec", 0.75, 0.9, 2, 1, 3),
        ("pump",),
        ("similar_text",),
    )
    contrary_claim = KernelClaim(
        "claim-2",
        "Outflows may increase",
        KernelDocument(
            "doc-2",
            "news",
            "wire",
            "Outflows may increase",
            1_700_000_010.0,
            "https://example.test/doc-2",
        ),
        "inference",
        "bearish",
    )
    contrary = KernelScoredClaim(
        contrary_claim,
        0.31,
        (("source", 0.4), ("recency", 0.7)),
    )
    return KernelOutput(
        0.82,
        0.71,
        False,
        "bullish",
        (),
        1,
        2,
        query="BTC outlook",
        scored_claims=(supporting, contrary),
        supporting=(supporting,),
        contrarian=(contrary,),
    )


def test_v2_result_dto_preserves_legacy_report_facing_golden_values():
    result = _core_result()

    def report_value(item: KernelScoredClaim) -> dict:
        trace = item.reputation_trace
        return {
            "claim_id": item.claim.id,
            "trust": item.trust,
            "components": dict(item.components),
            "reputation_trace": (
                {
                    "source": trace.source,
                    "prior": trace.prior,
                    "final": trace.final,
                    "agree_n": trace.agree_n,
                    "contradict_n": trace.contradict_n,
                    "iterations_run": trace.iterations_run,
                }
                if trace
                else None
            ),
            "manip_flags": list(item.manip_flags),
            "info_flags": list(item.info_flags),
        }

    actual = {
        "query": result.query,
        "scored_claims": [report_value(item) for item in result.scored_claims],
        "supporting": [item.claim.id for item in result.supporting],
        "contrarian": [item.claim.id for item in result.contrarian],
        "confidence": result.trust_score,
        "calibrated_confidence": result.confidence,
    }

    assert actual == {
        "query": "BTC outlook",
        "scored_claims": [
            {
                "claim_id": "claim-1",
                "trust": 0.82,
                "components": {"source": 0.9, "recency": 0.8},
                "reputation_trace": {
                    "source": "sec",
                    "prior": 0.75,
                    "final": 0.9,
                    "agree_n": 2,
                    "contradict_n": 1,
                    "iterations_run": 3,
                },
                "manip_flags": ["pump"],
                "info_flags": ["similar_text"],
            },
            {
                "claim_id": "claim-2",
                "trust": 0.31,
                "components": {"source": 0.4, "recency": 0.7},
                "reputation_trace": None,
                "manip_flags": [],
                "info_flags": [],
            },
        ],
        "supporting": ["claim-1"],
        "contrarian": ["claim-2"],
        "confidence": 0.82,
        "calibrated_confidence": 0.71,
    }


def test_result_contracts_are_frozen_slotted_and_json_safe():
    result = _core_result()

    assert KERNEL_CONTRACT_VERSION == "2.2.0"
    assert json.dumps(dataclasses.asdict(result), allow_nan=False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.query = "changed"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.scored_claims[0].trust = 0.1  # type: ignore[misc]


@pytest.mark.parametrize("version", ["", "1.0.0", "2.0.0", "2.1.1", "latest"])
def test_unknown_contract_versions_fail_closed(version: str):
    document = KernelDocument("d", "news", "source", "text", 1.0)
    claim = KernelClaim("c", "text", document)

    with pytest.raises(UnsupportedKernelContractVersion):
        require_supported_contract_version(version)
    with pytest.raises(UnsupportedKernelContractVersion):
        KernelInput((claim,), 1.0, "BTC", "q", version)
    with pytest.raises(UnsupportedKernelContractVersion):
        KernelOutput(0.5, 0.5, False, "neutral", (), 0, 0, version)


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), -float("inf")])
def test_result_float_fields_reject_bool_and_nonfinite(value: float):
    claim = KernelClaim("c", "text", KernelDocument("d", "news", "s", "text", 1.0))

    with pytest.raises(ValueError):
        KernelScoredClaim(claim, value)
    with pytest.raises(ValueError):
        KernelOutput(value, 0.5, False, "neutral", (), 0, 0)
    with pytest.raises(ValueError):
        KernelOutput(0.5, value, False, "neutral", (), 0, 0)
    with pytest.raises(ValueError):
        KernelReputationTrace("s", value, 0.5, 0, 0, 0)
    with pytest.raises(ValueError):
        KernelScoredClaim(claim, 0.5, (("component", value),))


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_result_counts_must_be_nonnegative_integers(value: int):
    with pytest.raises(ValueError):
        KernelOutput(0.5, 0.5, False, "neutral", (), value, 0)
    with pytest.raises(ValueError):
        KernelOutput(0.5, 0.5, False, "neutral", (), 0, value)
    with pytest.raises(ValueError):
        KernelReputationTrace("s", 0.5, 0.5, value, 0, 0)


def test_result_decision_enum_and_tuple_boundaries():
    claim = KernelClaim("c", "text", KernelDocument("d", "news", "s", "text", 1.0))

    for decision in ("abstain", "low_confidence", "normal"):
        assert KernelOutput(0.5, 0.5, False, "neutral", (), 0, 0, decision_state=decision)
    with pytest.raises(ValueError):
        KernelOutput(0.5, 0.5, False, "neutral", (), 0, 0, decision_state="unknown")
    with pytest.raises(ValueError):
        KernelScoredClaim(claim, 0.5, [("source", 0.5)])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        KernelScoredClaim(claim, 0.5, manip_flags=["flag"])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        KernelOutput(0.5, 0.5, False, "neutral", [], 0, 0)  # type: ignore[arg-type]


def test_kernel_output_keeps_seven_positional_arguments():
    result = KernelOutput(0.8, 0.7, False, "bullish", ("supported",), 1, 2)

    assert result.trust_score == 0.8
    assert result.confidence == 0.7
    assert result.contract_version == "2.2.0"
    assert result.query == ""


@pytest.mark.parametrize("value", [object(), [], {}])
def test_nested_result_contract_types_reject_arbitrary_values(value: object):
    kernel_claim = KernelClaim(
        "c", "text", KernelDocument("d", "news", "source", "text", 1.0)
    )

    with pytest.raises(ValueError):
        KernelReputationTrace(value, 0.5, 0.5, 0, 0, 0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        KernelReputationTrace("source", 0.5, 0.5, 0, 0, 0, value)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        KernelScoredClaim(value, 0.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        KernelScoredClaim(kernel_claim, 0.5, reputation_trace=value)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        KernelOutput(0.5, 0.5, value, "neutral", (), 0, 0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        KernelOutput(0.5, 0.5, False, value, (), 0, 0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        KernelOutput(0.5, 0.5, False, "neutral", (), 0, 0, query=value)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        KernelOutput(0.5, 0.5, False, "neutral", (value,), 0, 0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        KernelOutput(0.5, 0.5, False, "neutral", (), 0, 0, scored_claims=(value,))  # type: ignore[arg-type]


def test_scored_claim_rejects_application_claim():
    app_document = Document("d", "news", "source", "text", ts=1.0, meta={})
    app_claim = Claim("c", "text", app_document)

    with pytest.raises(ValueError):
        KernelScoredClaim(app_claim, 0.5)  # type: ignore[arg-type]


@pytest.mark.parametrize("mode", ["", "legacy", "DS_EM", [], {}])
def test_reputation_trace_mode_is_a_closed_string_enum(mode: object):
    with pytest.raises(ValueError):
        KernelReputationTrace("source", 0.5, 0.5, 0, 0, 0, mode)  # type: ignore[arg-type]

    assert KernelReputationTrace("source", 0.5, 0.5, 0, 0, 0, "entailment")
    assert KernelReputationTrace("source", 0.5, 0.5, 0, 0, 0, "ds_em")


def test_decision_state_unhashable_value_fails_with_contract_error():
    with pytest.raises(ValueError):
        KernelOutput(0.5, 0.5, False, "neutral", (), 0, 0, decision_state=[])  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [10**1_000, -(10**1_000)], ids=["positive", "negative"])
def test_huge_integers_fail_as_nonfinite_contract_numbers(value: int):
    claim = KernelClaim("c", "text", KernelDocument("d", "news", "s", "text", 1.0))

    with pytest.raises(ValueError):
        KernelScoredClaim(claim, value)
    with pytest.raises(ValueError):
        KernelScoredClaim(claim, 0.5, (("component", value),))
    with pytest.raises(ValueError):
        KernelReputationTrace("s", value, 0.5, 0, 0, 0)
    with pytest.raises(ValueError):
        KernelOutput(value, 0.5, False, "neutral", (), 0, 0)


@pytest.mark.parametrize(
    "contract",
    [KernelInput, KernelReputationTrace, KernelScoredClaim, KernelOutput],
)
def test_runtime_contracts_are_sealed(contract: type):
    with pytest.raises(TypeError, match="sealed"):
        type(f"{contract.__name__}Subclass", (contract,), {})


def test_json_serialization_rejects_no_values_from_valid_result():
    payload = dataclasses.asdict(_core_result())

    encoded = json.dumps(payload, allow_nan=False)
    assert json.loads(encoded)["decision_state"] == "normal"


@pytest.mark.parametrize("field", ["id", "kind", "source", "text", "url"])
def test_kernel_document_string_fields_reject_objects(field: str):
    values = {
        "id": "d",
        "kind": "news",
        "source": "source",
        "text": "text",
        "timestamp": 1.0,
        "url": "https://example.test",
    }
    values[field] = object()

    with pytest.raises(ValueError):
        KernelDocument(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("timestamp", [True, float("nan"), float("inf"), object()])
def test_kernel_document_timestamp_must_be_finite(timestamp: object):
    with pytest.raises(ValueError):
        KernelDocument("d", "news", "source", "text", timestamp)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "metadata",
    [
        object(),
        [],
        {},
        (("bad", []),),
        (("bad", {}),),
        (("bad", set()),),
        (("bad", object()),),
        (("bad", float("nan")),),
        (("bad", ("nested", ["mutable"])),),
        ((object(), "value"),),
        (("missing-value",),),
    ],
)
def test_kernel_document_metadata_rejects_mutable_or_non_json_values(metadata: object):
    with pytest.raises(ValueError):
        KernelDocument("d", "news", "source", "text", 1.0, metadata=metadata)  # type: ignore[arg-type]


def test_kernel_document_metadata_is_recursively_immutable_and_json_safe():
    document = KernelDocument(
        "d",
        "news",
        "source",
        "text",
        1.0,
        metadata=(
            ("active", True),
            ("nested", (("tags", ("etf", "flow")),)),
            ("nullable", None),
        ),
    )

    assert json.dumps(dataclasses.asdict(document), allow_nan=False)
    with pytest.raises(TypeError):
        document.metadata[1][1][0] = ("changed", "value")  # type: ignore[index]


@pytest.mark.parametrize("field", ["id", "text", "claim_type", "direction"])
def test_kernel_claim_string_fields_reject_objects(field: str):
    values = {
        "id": "c",
        "text": "text",
        "document": KernelDocument("d", "news", "source", "text", 1.0),
        "claim_type": "fact",
        "direction": "bullish",
    }
    values[field] = object()

    with pytest.raises(ValueError):
        KernelClaim(**values)  # type: ignore[arg-type]


def test_kernel_claim_requires_exact_document_contract():
    with pytest.raises(ValueError):
        KernelClaim("c", "text", object())  # type: ignore[arg-type]


@pytest.mark.parametrize("claims", [[], {}, object()])
def test_kernel_input_requires_tuple_of_exact_kernel_claims(claims: object):
    with pytest.raises(ValueError):
        KernelInput(claims, 1.0, "BTC", "q")  # type: ignore[arg-type]

    app_document = Document("d", "news", "source", "text", ts=1.0, meta={})
    app_claim = Claim("c", "text", app_document)
    with pytest.raises(ValueError):
        KernelInput((app_claim,), 1.0, "BTC", "q")  # type: ignore[arg-type]


@pytest.mark.parametrize("pit_epoch", [True, float("nan"), float("inf"), object()])
def test_kernel_input_pit_epoch_must_be_finite(pit_epoch: object):
    with pytest.raises(ValueError):
        KernelInput((), pit_epoch, "BTC", "q")  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["coin", "query"])
def test_kernel_input_string_fields_reject_objects(field: str):
    values = {"claims": (), "pit_epoch": 1.0, "coin": "BTC", "query": "q"}
    values[field] = object()

    with pytest.raises(ValueError):
        KernelInput(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("contract", [KernelDocument, KernelClaim])
def test_document_and_claim_contracts_are_sealed(contract: type):
    with pytest.raises(TypeError, match="sealed"):
        type(f"{contract.__name__}Subclass", (contract,), {})


def test_complete_output_graph_is_strict_json_safe():
    result = _core_result()
    payload = dataclasses.asdict(result)

    assert json.loads(json.dumps(payload, allow_nan=False))["query"] == "BTC outlook"


def test_contract_version_rejects_non_string_and_equality_spoof_values():
    class EqualitySpoof:
        def __eq__(self, other: object) -> bool:
            return True

    class VersionStringSubclass(str):
        pass

    invalid_versions = [
        object(),
        [],
        {},
        EqualitySpoof(),
        VersionStringSubclass(KERNEL_CONTRACT_VERSION),
    ]

    for version in invalid_versions:
        with pytest.raises(UnsupportedKernelContractVersion):
            require_supported_contract_version(version)  # type: ignore[arg-type]
        with pytest.raises(UnsupportedKernelContractVersion):
            KernelInput((), 1.0, "BTC", "q", version)  # type: ignore[arg-type]
        with pytest.raises(UnsupportedKernelContractVersion):
            KernelOutput(0.5, 0.5, False, "neutral", (), 0, 0, version)  # type: ignore[arg-type]


def test_non_string_contract_version_never_calls_value_repr():
    class BadRepr:
        calls = 0

        def __repr__(self) -> str:
            type(self).calls += 1
            raise RuntimeError("repr must not run")

    version = BadRepr()
    operations = (
        lambda: require_supported_contract_version(version),  # type: ignore[arg-type]
        lambda: KernelInput((), 1.0, "BTC", "q", version),  # type: ignore[arg-type]
        lambda: KernelOutput(0.5, 0.5, False, "neutral", (), 0, 0, version),  # type: ignore[arg-type]
    )

    for operation in operations:
        with pytest.raises(UnsupportedKernelContractVersion, match="expected exact str"):
            operation()
    assert BadRepr.calls == 0


def test_closed_enums_reject_str_subclass_without_hash_or_equality_calls():
    class SpoofedString(str):
        hash_calls = 0
        equality_calls = 0

        def __hash__(self) -> int:
            type(self).hash_calls += 1
            raise RuntimeError("hash must not run")

        def __eq__(self, other: object) -> bool:
            type(self).equality_calls += 1
            raise RuntimeError("equality must not run")

    mode = SpoofedString("entailment")
    decision = SpoofedString("normal")

    with pytest.raises(ValueError, match="mode"):
        KernelReputationTrace("source", 0.5, 0.5, 0, 0, 0, mode)
    with pytest.raises(ValueError, match="decision_state"):
        KernelOutput(0.5, 0.5, False, "neutral", (), 0, 0, decision_state=decision)
    assert SpoofedString.hash_calls == 0
    assert SpoofedString.equality_calls == 0


def test_invalid_metadata_and_component_values_never_render_untrusted_keys():
    class BadStr(str):
        repr_calls = 0

        def __repr__(self) -> str:
            type(self).repr_calls += 1
            raise RuntimeError("repr must not run")

    claim = KernelClaim(
        "c", "text", KernelDocument("d", "news", "source", "text", 1.0)
    )
    keys = (BadStr("hostile"), "x" * 100_000)

    for key in keys:
        with pytest.raises(ValueError) as metadata_error:
            KernelDocument(
                "d",
                "news",
                "source",
                "text",
                1.0,
                metadata=((key, object()),),
            )
        with pytest.raises(ValueError) as component_error:
            KernelScoredClaim(claim, 0.5, ((key, object()),))  # type: ignore[arg-type]
        assert len(str(metadata_error.value)) < 100
        assert len(str(component_error.value)) < 100
    assert BadStr.repr_calls == 0


def test_contract_version_errors_are_bounded_for_huge_values_and_type_names():
    huge_string = "x" * 100_000
    huge_named_type = type("Y" * 100_000, (), {})

    for version in (huge_string, huge_named_type()):
        with pytest.raises(UnsupportedKernelContractVersion) as error:
            require_supported_contract_version(version)  # type: ignore[arg-type]
        assert len(str(error.value)) < 100


def test_all_immutable_collection_boundaries_reject_tuple_subclasses_without_hooks():
    class BadTuple(tuple):
        hook_calls = 0

        def __iter__(self):
            type(self).hook_calls += 1
            raise RuntimeError("iteration must not run")

        def __len__(self) -> int:
            type(self).hook_calls += 1
            raise RuntimeError("length must not run")

        def __getitem__(self, index: object) -> object:
            type(self).hook_calls += 1
            raise RuntimeError("indexing must not run")

    document = KernelDocument("d", "news", "source", "text", 1.0)
    claim = KernelClaim("c", "text", document)
    scored = KernelScoredClaim(claim, 0.5)
    empty = BadTuple()
    component = BadTuple(("source", 0.5))
    metadata_pair = BadTuple(("key", "value"))
    nested_value = BadTuple(("value",))
    operations = (
        lambda: KernelDocument("d", "news", "source", "text", 1.0, metadata=empty),
        lambda: KernelDocument(
            "d", "news", "source", "text", 1.0, metadata=(metadata_pair,)
        ),
        lambda: KernelDocument(
            "d", "news", "source", "text", 1.0, metadata=(("key", nested_value),)
        ),
        lambda: KernelInput(empty, 1.0, "BTC", "q"),
        lambda: KernelScoredClaim(claim, 0.5, empty),
        lambda: KernelScoredClaim(claim, 0.5, (component,)),
        lambda: KernelScoredClaim(claim, 0.5, manip_flags=empty),
        lambda: KernelScoredClaim(claim, 0.5, info_flags=empty),
        lambda: KernelOutput(0.5, 0.5, False, "neutral", empty, 0, 0),
        lambda: KernelOutput(0.5, 0.5, False, "neutral", (), 0, 0, scored_claims=empty),
        lambda: KernelOutput(0.5, 0.5, False, "neutral", (), 0, 0, supporting=empty),
        lambda: KernelOutput(0.5, 0.5, False, "neutral", (), 0, 0, contrarian=empty),
        lambda: KernelOutput(
            0.5, 0.5, False, "neutral", (), 0, 0, scored_claims=BadTuple((scored,))
        ),
    )

    for operation in operations:
        with pytest.raises(ValueError):
            operation()
    assert BadTuple.hook_calls == 0


def test_all_scalar_boundaries_reject_subclasses_without_hooks():
    class BadStr(str):
        hook_calls = 0

        def __deepcopy__(self, memo: object) -> object:
            type(self).hook_calls += 1
            raise RuntimeError("deepcopy must not run")

        def __hash__(self) -> int:
            type(self).hook_calls += 1
            raise RuntimeError("hash must not run")

        def __eq__(self, other: object) -> bool:
            type(self).hook_calls += 1
            raise RuntimeError("equality must not run")

        def __repr__(self) -> str:
            type(self).hook_calls += 1
            raise RuntimeError("repr must not run")

    class BadInt(int):
        hook_calls = 0

        def __float__(self) -> float:
            type(self).hook_calls += 1
            raise RuntimeError("float must not run")

        def __lt__(self, other: object) -> bool:
            type(self).hook_calls += 1
            raise RuntimeError("comparison must not run")

        def __deepcopy__(self, memo: object) -> object:
            type(self).hook_calls += 1
            raise RuntimeError("deepcopy must not run")

    bad_string = BadStr("hostile")
    bad_number = BadInt(1)
    document = KernelDocument("d", "news", "source", "text", 1.0)
    claim = KernelClaim("c", "text", document)
    operations = (
        lambda: KernelDocument(bad_string, "news", "source", "text", 1.0),
        lambda: KernelDocument("d", "news", "source", "text", bad_number),
        lambda: KernelDocument(
            "d", "news", "source", "text", 1.0, metadata=((bad_string, "value"),)
        ),
        lambda: KernelClaim(bad_string, "text", document),
        lambda: KernelInput((claim,), bad_number, "BTC", "q"),
        lambda: KernelInput((claim,), 1.0, bad_string, "q"),
        lambda: KernelReputationTrace(bad_string, 0.5, 0.5, 0, 0, 0),
        lambda: KernelReputationTrace("source", bad_number, 0.5, 0, 0, 0),
        lambda: KernelReputationTrace("source", 0.5, 0.5, bad_number, 0, 0),
        lambda: KernelScoredClaim(claim, bad_number),
        lambda: KernelScoredClaim(claim, 0.5, ((bad_string, 0.5),)),
        lambda: KernelScoredClaim(claim, 0.5, manip_flags=(bad_string,)),
        lambda: KernelOutput(bad_number, 0.5, False, "neutral", (), 0, 0),
        lambda: KernelOutput(0.5, 0.5, False, bad_string, (), 0, 0),
        lambda: KernelOutput(0.5, 0.5, False, "neutral", (bad_string,), 0, 0),
        lambda: KernelOutput(0.5, 0.5, False, "neutral", (), bad_number, 0),
    )

    for operation in operations:
        with pytest.raises(ValueError):
            operation()
    assert BadStr.hook_calls == 0
    assert BadInt.hook_calls == 0
