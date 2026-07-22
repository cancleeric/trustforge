"""Exact legacy behavior locked before moving corroboration into the core."""
from __future__ import annotations

from trustforge.ingestion.base import Document
from trustforge.trust.scoring import Claim, _corroboration_detail


def _claim(cid: str, source: str, text: str) -> Claim:
    return Claim(
        id=cid,
        text=text,
        doc=Document(id=f"doc-{cid}", kind="news", source=source, text=text),
        direction="neutral",
    )


def _fixture() -> tuple[Claim, list[Claim]]:
    target = _claim("target", "target-source", "機構 資金 流入 現貨 ETF 推升 信心")
    return target, [
        target,
        _claim("same-source", " TARGET-SOURCE ", "機構 資金 流入 現貨 ETF 推升 信心"),
        _claim("low-overlap", "noise", "監管 法案 投票 延後"),
        _claim("a1", "source-a", "機構 資金 流入 現貨 ETF 推升 價格"),
        _claim("a2", "SOURCE-A", "機構 資金 流入 現貨 ETF 提振 信心"),
        _claim("b1", "source-b", "機構 資金 流入 現貨 ETF 增加 信心"),
        _claim("b2", " source-b ", "機構 資金 流入 現貨 ETF 帶來 信心"),
    ]


def test_golden_callback_sequence_count_and_ordinary_exact_result() -> None:
    target, claims = _fixture()
    calls: list[tuple[str, str]] = []
    labels = iter(("contradiction", "entailment", "neutral"))

    def stance(left: str, right: str) -> str:
        calls.append((left, right))
        return next(labels)

    result = _corroboration_detail(target, claims, stance_fn=stance)

    assert calls == [
        (target.text, claims[3].text),
        (target.text, claims[4].text),
        (target.text, claims[5].text),
    ]
    assert len(calls) == 3
    assert result == ({"source-a", "source-b"}, {"source-a"})


def test_golden_callback_sequence_count_and_strict_exact_result() -> None:
    target, claims = _fixture()
    calls: list[tuple[str, str]] = []
    labels = iter(("contradiction", "neutral", "neutral", "entailment"))

    def stance(left: str, right: str) -> str:
        calls.append((left, right))
        return next(labels)

    result = _corroboration_detail(
        target, claims, stance_fn=stance, require_entailment=True
    )

    assert calls == [
        (target.text, claims[3].text),
        (target.text, claims[4].text),
        (target.text, claims[5].text),
        (target.text, claims[6].text),
    ]
    assert len(calls) == 4
    assert result == ({"source-b"}, {"source-a"})
