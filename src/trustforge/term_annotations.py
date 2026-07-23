"""Deterministic glossary term annotation over plain text."""

from __future__ import annotations

from dataclasses import dataclass

from trustforge.glossary import GLOSSARY_CATALOG, GlossaryTerm


@dataclass(frozen=True)
class TermAnnotation:
    term_id: str
    matched_text: str
    start: int
    end: int

    def to_dict(self) -> dict[str, object]:
        return {
            "term_id": self.term_id,
            "matched_text": self.matched_text,
            "start": self.start,
            "end": self.end,
        }


def annotate_terms(text: str, catalog: tuple[GlossaryTerm, ...] = GLOSSARY_CATALOG) -> tuple[TermAnnotation, ...]:
    if not isinstance(text, str):
        raise ValueError("text must be string")
    phrases: list[tuple[str, GlossaryTerm]] = []
    for term in catalog:
        for phrase in (term.label, *term.aliases):
            if phrase.strip():
                phrases.append((phrase, term))
    phrases.sort(key=lambda item: (-len(item[0]), item[0].casefold()))

    annotations: list[TermAnnotation] = []
    occupied: set[int] = set()
    folded_text = text.casefold()
    for phrase, term in phrases:
        folded_phrase = phrase.casefold()
        start = 0
        while True:
            index = folded_text.find(folded_phrase, start)
            if index < 0:
                break
            end = index + len(phrase)
            span = set(range(index, end))
            if not occupied.intersection(span):
                annotations.append(
                    TermAnnotation(
                        term_id=term.term_id,
                        matched_text=text[index:end],
                        start=index,
                        end=end,
                    )
                )
                occupied.update(span)
            start = index + 1

    return tuple(sorted(annotations, key=lambda annotation: (annotation.start, annotation.end)))
