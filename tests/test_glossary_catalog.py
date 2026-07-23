from __future__ import annotations

import pytest

from trustforge.glossary import (
    CORE_GLOSSARY_TERMS,
    GLOSSARY_CATALOG_VERSION,
    GlossaryAudience,
    GlossaryTerm,
    glossary_by_phrase,
    glossary_by_term_id,
    glossary_catalog_payload,
    validate_glossary_catalog,
)


def test_core_glossary_catalog_contains_required_terms() -> None:
    payload = glossary_catalog_payload()

    assert payload["schema_version"] == GLOSSARY_CATALOG_VERSION
    terms = {term["term_id"]: term for term in payload["terms"]}  # type: ignore[index]
    assert set(terms) == {
        "fdv",
        "market_cap",
        "tvl",
        "tokenomics",
        "gas_fee",
        "unlock_sell_pressure",
    }
    assert terms["fdv"]["label"] == "FDV"
    assert "market capitalization" in terms["market_cap"]["aliases"]
    assert "popover" in terms["tvl"]["audiences"]
    assert "help_center" in terms["gas_fee"]["audiences"]


def test_glossary_lookup_is_shared_by_id_label_and_alias() -> None:
    assert glossary_by_term_id("tokenomics") is not None
    assert glossary_by_phrase("MC") == glossary_by_term_id("market_cap")
    assert glossary_by_phrase("market cap") == glossary_by_term_id("market_cap")
    assert glossary_by_phrase("代幣經濟") == glossary_by_term_id("tokenomics")
    assert glossary_by_phrase("missing") is None


def test_glossary_catalog_rejects_duplicate_term_ids_and_aliases() -> None:
    duplicate_id = (
        GlossaryTerm(term_id="fdv", label="FDV", definition="one"),
        GlossaryTerm(term_id="fdv", label="FDV2", definition="two"),
    )
    with pytest.raises(ValueError, match="duplicate glossary term_id: fdv"):
        validate_glossary_catalog(duplicate_id)

    duplicate_alias = (
        GlossaryTerm(term_id="one", label="One", definition="one", aliases=("Same",)),
        GlossaryTerm(term_id="two", label="Two", definition="two", aliases=("same",)),
    )
    with pytest.raises(ValueError, match="duplicate glossary alias: same"):
        validate_glossary_catalog(duplicate_alias)


def test_glossary_term_validates_required_fields_and_audiences() -> None:
    with pytest.raises(ValueError, match="GlossaryTerm.term_id must be non-empty string"):
        GlossaryTerm(term_id="", label="Label", definition="Definition")

    with pytest.raises(ValueError, match="GlossaryTerm.aliases must be tuple"):
        GlossaryTerm(term_id="x", label="X", definition="Definition", aliases=("",))

    with pytest.raises(ValueError, match="GlossaryTerm.audiences must be tuple"):
        GlossaryTerm(
            term_id="x",
            label="X",
            definition="Definition",
            audiences=("popover",),  # type: ignore[arg-type]
        )

    assert all(GlossaryAudience.POPOVER in term.audiences for term in CORE_GLOSSARY_TERMS)
