"""Versioned glossary catalog shared by reports, popovers, and help content."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

GLOSSARY_CATALOG_VERSION = "1.0.0"


class GlossaryAudience(StrEnum):
    REPORT = "report"
    POPOVER = "popover"
    HELP_CENTER = "help_center"


def _canonical_key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


@dataclass(frozen=True)
class GlossaryTerm:
    term_id: str
    label: str
    definition: str
    aliases: tuple[str, ...] = ()
    audiences: tuple[GlossaryAudience, ...] = (
        GlossaryAudience.REPORT,
        GlossaryAudience.POPOVER,
        GlossaryAudience.HELP_CENTER,
    )

    def __post_init__(self) -> None:
        for field_name in ("term_id", "label", "definition"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"GlossaryTerm.{field_name} must be non-empty string")
        if not isinstance(self.aliases, tuple) or any(
            not isinstance(alias, str) or not alias.strip() for alias in self.aliases
        ):
            raise ValueError("GlossaryTerm.aliases must be tuple of non-empty strings")
        if not isinstance(self.audiences, tuple) or any(
            not isinstance(audience, GlossaryAudience) for audience in self.audiences
        ):
            raise ValueError("GlossaryTerm.audiences must be tuple of GlossaryAudience")

    def to_dict(self) -> dict[str, object]:
        return {
            "term_id": self.term_id,
            "label": self.label,
            "definition": self.definition,
            "aliases": list(self.aliases),
            "audiences": [audience.value for audience in self.audiences],
        }


CORE_GLOSSARY_TERMS: tuple[GlossaryTerm, ...] = (
    GlossaryTerm(
        term_id="fdv",
        label="FDV",
        definition="Fully diluted valuation; market cap estimated as if all token supply were circulating.",
        aliases=("fully diluted valuation",),
    ),
    GlossaryTerm(
        term_id="market_cap",
        label="MC",
        definition="Market capitalization; circulating token supply multiplied by current market price.",
        aliases=("market cap", "market capitalization", "市值"),
    ),
    GlossaryTerm(
        term_id="tvl",
        label="TVL",
        definition="Total value locked; assets deposited in a protocol or chain, measured at observation time.",
        aliases=("total value locked",),
    ),
    GlossaryTerm(
        term_id="tokenomics",
        label="Tokenomics",
        definition="Token supply, allocation, emissions, utility, and incentive design.",
        aliases=("token economics", "代幣經濟"),
    ),
    GlossaryTerm(
        term_id="gas_fee",
        label="Gas Fee",
        definition="Network fee paid to execute or settle transactions.",
        aliases=("gas", "transaction fee", "手續費"),
    ),
    GlossaryTerm(
        term_id="unlock_sell_pressure",
        label="解鎖賣壓",
        definition="Potential selling pressure created when previously locked tokens become transferable.",
        aliases=("unlock pressure", "token unlock", "unlock sell pressure"),
    ),
)


def validate_glossary_catalog(terms: Iterable[GlossaryTerm]) -> tuple[GlossaryTerm, ...]:
    catalog = tuple(terms)
    seen_ids: set[str] = set()
    seen_aliases: dict[str, str] = {}
    for term in catalog:
        canonical_term_id = _canonical_key(term.term_id)
        if canonical_term_id in seen_ids:
            raise ValueError(f"duplicate glossary term_id: {term.term_id}")
        seen_ids.add(canonical_term_id)
        for phrase in (term.label, *term.aliases):
            key = _canonical_key(phrase)
            owner = seen_aliases.get(key)
            if owner is not None and owner != canonical_term_id:
                raise ValueError(f"duplicate glossary alias: {key}")
            seen_aliases[key] = canonical_term_id
    return catalog


GLOSSARY_CATALOG = validate_glossary_catalog(CORE_GLOSSARY_TERMS)


def glossary_catalog_payload() -> dict[str, object]:
    return {
        "schema_version": GLOSSARY_CATALOG_VERSION,
        "terms": [term.to_dict() for term in GLOSSARY_CATALOG],
    }


def glossary_by_term_id(term_id: str) -> GlossaryTerm | None:
    needle = _canonical_key(term_id)
    return next((term for term in GLOSSARY_CATALOG if _canonical_key(term.term_id) == needle), None)


def glossary_by_phrase(phrase: str) -> GlossaryTerm | None:
    needle = _canonical_key(phrase)
    for term in GLOSSARY_CATALOG:
        if _canonical_key(term.label) == needle:
            return term
        if any(_canonical_key(alias) == needle for alias in term.aliases):
            return term
    return None
