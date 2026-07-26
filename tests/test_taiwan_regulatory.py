"""Tests for Taiwan regulatory adapter stubs."""
from __future__ import annotations

import pytest

from trustforge.ingestion.taiwan_regulatory import (
    FSCSource,
    MOPSSource,
    TPEXSource,
    TWSESource,
    TaiwanRegulatorySource,
)


@pytest.mark.parametrize("source_cls", [MOPSSource, FSCSource, TWSESource, TPEXSource])
def test_taiwan_source_fail_closed_returns_empty(source_cls) -> None:
    """Stub adapters must return empty list (fail-closed)."""
    src = source_cls()
    docs = src.fetch("crypto", coin="BTC")
    assert docs == []


@pytest.mark.parametrize("source_cls", [MOPSSource, FSCSource, TWSESource, TPEXSource])
def test_taiwan_source_has_regulatory_kind(source_cls) -> None:
    assert source_cls.kind == "regulatory"


def test_taiwan_source_validate_host_allows_official() -> None:
    src = TaiwanRegulatorySource()
    assert src._validate_host("https://mops.twse.com.tw/foo")
    assert src._validate_host("https://www.fsc.gov.tw/bar")
    assert not src._validate_host("https://evil.example.com/")


def test_taiwan_source_build_document_schema() -> None:
    src = TaiwanRegulatorySource()
    doc = src._build_document(
        source="mops",
        title="重大訊息公告",
        url="https://mops.twse.com.tw/t51sb01",
        text="公司發布重要財務訊息",
        published_at="2026-07-26T10:00:00+08:00",
    )
    assert doc.kind == "regulatory"
    assert doc.source == "mops"
    assert doc.meta["source_region"] == "TW"
    assert doc.meta["adapter_status"] == "stub"
    assert "published_at" in doc.meta


def test_taiwan_source_dedup_prep() -> None:
    """Document IDs must be deterministic given same text for dedup."""
    src = TaiwanRegulatorySource()
    d1 = src._build_document(source="fsc", title="A", url="", text="text", published_at="2026-01-01T00:00:00+08:00")
    d2 = src._build_document(source="fsc", title="A", url="", text="text", published_at="2026-01-01T00:00:00+08:00")
    d3 = src._build_document(source="fsc", title="A", url="", text="different", published_at="2026-01-01T00:00:00+08:00")
    assert d1.id == d2.id  # same text = same hash = dedup
    assert d1.id != d3.id  # different text
