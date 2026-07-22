"""Pure, incremental cross-source corroboration.

The generator yields only pairs that passed every deterministic gate.  Runtime
code may classify that one pair and send the label back; providers, caches,
budgets, and I/O therefore remain outside the kernel boundary.

``StanceLabel`` is both the public static contract and a runtime-validated
boundary.  The production cached-stance adapter already emits only these three
labels, so hardening rejects malformed custom/provider results without changing
the formal cached path.
"""
from __future__ import annotations

import re
from collections.abc import Generator, Sequence
from dataclasses import dataclass
from typing import Literal

StanceLabel = Literal["entailment", "contradiction", "neutral"]
_STANCE_LABELS = frozenset({"entailment", "contradiction", "neutral"})

DOMAIN_STOP = frozenset(
    {
        "btc", "eth", "sol", "bnb", "xrp", "bitcoin", "ethereum", "solana",
        "比特幣", "比特", "以太坊", "以太", "幣", "市場", "價格", "成交量",
        "交易所", "交易", "行情", "數據", "分析", "資料", "報告", "漲跌",
        "上漲", "下跌", "看漲", "看跌", "走低", "走高", "目前", "近期",
        "顯示", "表示", "預計", "預測", "可能", "目標",
    }
)

_SOURCE_ALIASES = {
    "coindesk.com": "coindesk", "cointelegraph.com": "cointelegraph",
    "theblock.co": "theblock", "theblock": "theblock", "reuters.com": "reuters",
    "bloomberg.com": "bloomberg", "bitcoinmagazine.com": "bitcoinmagazine",
    "newsbtc.com": "newsbtc", "cryptoslate.com": "cryptoslate",
    "decrypt.co": "decrypt", "utoday.com": "utoday", "twitter": "x",
    "x.com": "x", "sec edgar": "sec-gov", "sec": "sec-gov", "sec.gov": "sec-gov",
}
_NEG_RX = re.compile(r"不會|不太|不致|不至|不再|沒有|沒|尚未|未|無法|別|勿|非")
_DIRECTION_WORDS = (
    "上漲", "漲", "看漲", "看多", "買入", "買盤", "累積", "增持", "突破",
    "流入", "利多", "走高", "反彈", "上揚", "攀升", "下跌", "跌", "看跌",
    "看空", "賣壓", "拋壓", "拋售", "流出", "利空", "走低", "暴跌", "崩",
    "恐慌", "清算", "賣盤", "下挫",
)


@dataclass(frozen=True, slots=True)
class CorroborationClaim:
    text: str
    source: str
    direction: str = "neutral"


@dataclass(frozen=True, slots=True)
class StancePair:
    target_text: str
    candidate_text: str


@dataclass(frozen=True, slots=True)
class CorroborationResult:
    independent_sources: frozenset[str]
    contradicting_sources: frozenset[str]


def canonical_source(source: str | None) -> str:
    if not source:
        return ""
    key = source.strip().casefold()
    return _SOURCE_ALIASES.get(key, key) if key else ""


def _normalize(text: str) -> set[str]:
    return {token for token in re.findall(r"[\w一-鿿]+", text.lower()) if len(token) > 1}


def _direction_compatible(left: str, right: str) -> bool:
    return "neutral" in (left, right) or left == right


def directional_word_polarities(text: str) -> tuple[set[str], set[str]]:
    candidates: list[tuple[int, int, str]] = []
    for word in _DIRECTION_WORDS:
        for match in re.finditer(re.escape(word), text):
            candidates.append((match.start(), match.end(), word))
    candidates.sort(key=lambda item: (-(item[1] - item[0]), item[0]))
    consumed: list[tuple[int, int]] = []
    asserted: set[str] = set()
    negated: set[str] = set()
    for start, end, word in candidates:
        if any(start < used_end and end > used_start for used_start, used_end in consumed):
            continue
        consumed.append((start, end))
        target = negated if _NEG_RX.search(text[max(0, start - 4):start]) else asserted
        target.add(word)
    return asserted, negated


def corroborate(
    target: CorroborationClaim,
    claims: Sequence[CorroborationClaim],
    *,
    require_stance: bool,
    require_entailment: bool = False,
) -> Generator[StancePair, StanceLabel, CorroborationResult]:
    """Return corroboration, yielding each next pair that needs a stance label."""
    target_tokens = _normalize(target.text) - DOMAIN_STOP
    independent: set[str] = set()
    contradicting: set[str] = set()
    if not target_tokens:
        return CorroborationResult(frozenset(), frozenset())
    target_key = canonical_source(target.source)
    for candidate in claims:
        candidate_key = canonical_source(candidate.source)
        if candidate_key == target_key or candidate_key in independent:
            continue
        candidate_tokens = _normalize(candidate.text) - DOMAIN_STOP
        intersection = len(target_tokens & candidate_tokens)
        if not intersection or intersection / len(target_tokens) < 0.4:
            continue
        if not _direction_compatible(target.direction, candidate.direction):
            continue
        target_asserted, target_negated = directional_word_polarities(target.text)
        candidate_asserted, candidate_negated = directional_word_polarities(candidate.text)
        if (target_asserted & candidate_negated) or (candidate_asserted & target_negated):
            continue
        if not require_stance:
            if not require_entailment:
                independent.add(candidate_key)
            continue
        label = yield StancePair(target.text, candidate.text)
        if not isinstance(label, str) or label not in _STANCE_LABELS:
            raise ValueError(
                "stance label must be 'entailment', 'contradiction', or 'neutral'"
            )
        if label == "contradiction":
            contradicting.add(candidate_key)
        elif not require_entailment or label == "entailment":
            independent.add(candidate_key)
    return CorroborationResult(frozenset(independent), frozenset(contradicting))


__all__ = [
    "CorroborationClaim", "CorroborationResult", "DOMAIN_STOP", "StanceLabel", "StancePair",
    "canonical_source", "corroborate", "directional_word_polarities",
]
