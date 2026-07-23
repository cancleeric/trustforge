from __future__ import annotations

from trustforge.term_annotations import annotate_terms


def test_term_annotations_match_aliases_and_labels_without_html() -> None:
    annotations = annotate_terms("FDV and market cap are not the same as TVL.")

    assert [item.to_dict() for item in annotations] == [
        {"term_id": "fdv", "matched_text": "FDV", "start": 0, "end": 3},
        {"term_id": "market_cap", "matched_text": "market cap", "start": 8, "end": 18},
        {"term_id": "tvl", "matched_text": "TVL", "start": 39, "end": 42},
    ]
    assert "<" not in repr(annotations)


def test_term_annotations_use_longest_match_for_overlapping_terms() -> None:
    annotations = annotate_terms("The market capitalization signal differs from market cap.")

    assert [item.term_id for item in annotations] == ["market_cap", "market_cap"]
    assert annotations[0].matched_text == "market capitalization"
    assert annotations[1].matched_text == "market cap"


def test_term_annotations_report_code_point_offsets_for_cjk_and_emoji() -> None:
    text = "🚀 代幣經濟 可能造成 解鎖賣壓"
    annotations = annotate_terms(text)

    tokenomics = next(item for item in annotations if item.term_id == "tokenomics")
    unlock = next(item for item in annotations if item.term_id == "unlock_sell_pressure")
    assert text[tokenomics.start : tokenomics.end] == "代幣經濟"
    assert text[unlock.start : unlock.end] == "解鎖賣壓"


def test_term_annotations_are_case_insensitive() -> None:
    annotations = annotate_terms("gas fee and TOTAL VALUE LOCKED changed.")

    assert [item.term_id for item in annotations] == ["gas_fee", "tvl"]
