"""Regression tests for errors crossing the app/core stance boundary."""
from __future__ import annotations

import pytest

from trustforge.ingestion.base import Document
from trustforge.trust.scoring import Claim, _corroboration_detail


def _eligible_pair() -> tuple[Claim, Claim]:
    target = Claim(
        id="target",
        text="機構 資金 流入 現貨 ETF 推升 信心",
        doc=Document(
            id="target-doc",
            kind="news",
            source="target",
            text="機構 資金 流入 現貨 ETF 推升 信心",
        ),
        direction="neutral",
    )
    candidate = Claim(
        id="candidate",
        text="機構 資金 流入 現貨 ETF 推升 價格",
        doc=Document(
            id="candidate-doc",
            kind="news",
            source="source-a",
            text="機構 資金 流入 現貨 ETF 推升 價格",
        ),
        direction="neutral",
    )
    return target, candidate


def test_callback_stop_iteration_propagates_unchanged() -> None:
    target, candidate = _eligible_pair()
    callback_error = StopIteration("callback failed")

    def failing_stance(_left: str, _right: str) -> str:
        raise callback_error

    with pytest.raises(StopIteration) as caught:
        _corroboration_detail(target, [target, candidate], stance_fn=failing_stance)

    assert caught.value is callback_error


def test_callback_other_exception_propagates_unchanged() -> None:
    target, candidate = _eligible_pair()
    callback_error = RuntimeError("provider failed")

    def failing_stance(_left: str, _right: str) -> str:
        raise callback_error

    with pytest.raises(RuntimeError) as caught:
        _corroboration_detail(target, [target, candidate], stance_fn=failing_stance)

    assert caught.value is callback_error


@pytest.mark.parametrize("invalid_label", ["typo", None, []])
def test_legacy_facade_maps_invalid_return_to_neutral_in_ordinary_mode(
    invalid_label: object,
) -> None:
    target, candidate = _eligible_pair()

    def invalid_stance(_left: str, _right: str) -> object:
        return invalid_label

    result = _corroboration_detail(
        target, [target, candidate], stance_fn=invalid_stance  # type: ignore[arg-type]
    )

    assert result == ({"source-a"}, set())


@pytest.mark.parametrize("invalid_label", ["typo", None, []])
def test_legacy_facade_maps_invalid_return_to_neutral_in_strict_mode(
    invalid_label: object,
) -> None:
    target, candidate = _eligible_pair()

    def invalid_stance(_left: str, _right: str) -> object:
        return invalid_label

    result = _corroboration_detail(
        target,
        [target, candidate],
        stance_fn=invalid_stance,  # type: ignore[arg-type]
        require_entailment=True,
    )

    assert result == (set(), set())
