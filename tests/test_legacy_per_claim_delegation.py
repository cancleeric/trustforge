"""Legacy facade delegation tests for the pure per-claim engine (#451)."""

from __future__ import annotations

import math
from typing import Any

import pytest

from trustforge.ingestion.base import Document
from trustforge.trust import scoring as legacy
from trustforge_core import (
    DEFAULT_HALF_LIVES,
    DEFAULT_SCORE_WEIGHTS,
    DEFAULT_SOURCE_REPUTATIONS,
)


def _claim(claim_id: str, *, timestamp: float = 1_700_000_000.0) -> legacy.Claim:
    text = f"BTC adoption expands {claim_id}"
    document = Document(
        f"doc-{claim_id}",
        "news",
        f"source-{claim_id}",
        text,
        ts=timestamp,
        url=f"https://example.test/{claim_id}",
        meta={"nested": {"tags": ["etf", claim_id]}},
    )
    return legacy.Claim(claim_id, text, document, "fact", "bullish")


def test_legacy_constants_are_compatibility_views_of_core_defaults():
    assert legacy.DEFAULT_WEIGHTS == dict(DEFAULT_SCORE_WEIGHTS)
    assert legacy.KIND_REPUTATION == dict(DEFAULT_SOURCE_REPUTATIONS)
    assert legacy.KIND_HALFLIFE_HOURS == {
        key: value for key, value in DEFAULT_HALF_LIVES if key != "default"
    }


def test_legacy_final_loop_delegates_to_core_exactly_once_per_claim(monkeypatch):
    claims = [_claim("a"), _claim("b"), _claim("c")]
    original = legacy._core_score_claim
    delegated_ids: list[str] = []
    dynamic_values = []

    def spy(claim, **kwargs: Any):
        delegated_ids.append(claim.id)
        dynamic_values.append(kwargs["dynamic_reputation"])
        return original(claim, **kwargs)

    monkeypatch.setattr(legacy, "_core_score_claim", spy)

    actual = legacy.score(
        claims,
        now=1_700_000_000.0,
        dynamic_reputation=False,
        stance_client=object(),
    )

    assert delegated_ids == ["a", "b", "c"]
    assert dynamic_values == [None, None, None]
    assert [item.claim is claim for item, claim in zip(actual, claims)] == [
        True,
        True,
        True,
    ]


def test_private_mapper_excludes_unrelated_metadata_and_normalizes_nonfinite_time_only_in_core(
    monkeypatch,
):
    claim = _claim("nan", timestamp=float("nan"))
    original = legacy._core_score_claim
    delegated = []

    def spy(core_claim, **kwargs: Any):
        delegated.append(core_claim)
        return original(core_claim, **kwargs)

    monkeypatch.setattr(legacy, "_core_score_claim", spy)

    actual = legacy.score(
        [claim],
        now=1_700_000_000.0,
        dynamic_reputation=False,
        stance_client=object(),
    )[0]
    claim.doc.meta["nested"]["tags"].append("mutated")

    assert len(delegated) == 1
    assert delegated[0].document.timestamp == 0.0
    assert delegated[0].document.metadata == ()
    assert actual.claim is claim
    assert math.isnan(actual.claim.doc.ts)
    assert actual.components["recency"] == 0.5


def test_runtime_reputation_and_half_life_facades_feed_core(monkeypatch):
    monkeypatch.setitem(legacy.KIND_REPUTATION, "news", 0.8)
    monkeypatch.setitem(legacy.KIND_HALFLIFE_HOURS, "news", 6.0)
    claim = _claim("runtime", timestamp=1_700_000_000.0)

    actual = legacy.score(
        [claim],
        now=1_700_000_000.0 + 6 * 3_600,
        dynamic_reputation=False,
        stance_client=object(),
    )[0]

    assert actual.components["reputation"] == 0.8
    assert actual.components["recency"] == 0.5


def test_runtime_social_reputation_override_controls_unverified_celebrity_cap(
    monkeypatch,
):
    monkeypatch.setitem(legacy.KIND_REPUTATION, "social", 0.2)
    document = Document(
        "doc-celeb",
        "celebrity_trade",
        "celebrity",
        "BTC trade disclosed",
        ts=1_700_000_000.0,
        meta={"verified_onchain": False},
    )
    claim = legacy.Claim("celeb", document.text, document)

    actual = legacy.score(
        [claim],
        now=1_700_000_000.0,
        dynamic_reputation=False,
        stance_client=object(),
    )[0]

    assert actual.components["reputation"] == 0.2


def test_missing_dynamic_source_delegates_none_for_core_prior_fallback(monkeypatch):
    claim = _claim("missing")
    original = legacy._core_score_claim
    delegated_dynamic = []

    monkeypatch.setattr(legacy, "_iterate_source_reputation", lambda *args, **kwargs: {})

    def spy(core_claim, **kwargs: Any):
        delegated_dynamic.append(kwargs["dynamic_reputation"])
        return original(core_claim, **kwargs)

    monkeypatch.setattr(legacy, "_core_score_claim", spy)

    actual = legacy.score(
        [claim],
        now=1_700_000_000.0,
        dynamic_reputation=True,
        stance_client=object(),
    )[0]

    assert delegated_dynamic == [None]
    assert actual.components["reputation"] == legacy.KIND_REPUTATION["news"]


def test_unrelated_arbitrary_metadata_never_executes_magic_hooks():
    class BadStr(str):
        hooks = 0

        def __hash__(self) -> int:
            type(self).hooks += 1
            return super().__hash__()

        def __eq__(self, other: object) -> bool:
            type(self).hooks += 1
            return super().__eq__(other)

        def __str__(self) -> str:
            type(self).hooks += 1
            raise RuntimeError("str must not run")

        def __repr__(self) -> str:
            type(self).hooks += 1
            raise RuntimeError("repr must not run")

    class BadObject:
        hooks = 0

        def __str__(self) -> str:
            type(self).hooks += 1
            raise RuntimeError("str must not run")

        def __repr__(self) -> str:
            type(self).hooks += 1
            raise RuntimeError("repr must not run")

        def __deepcopy__(self, memo: object) -> object:
            type(self).hooks += 1
            raise RuntimeError("deepcopy must not run")

    key = BadStr("unrelated")
    value = BadObject()
    metadata = {key: value}
    BadStr.hooks = 0
    document = Document(
        "doc-hostile",
        "news",
        "wire",
        "BTC adoption expands",
        ts=1_700_000_000.0,
        meta=metadata,
    )
    claim = legacy.Claim("hostile", document.text, document)

    actual = legacy.score(
        [claim],
        now=1_700_000_000.0,
        dynamic_reputation=False,
        stance_client=object(),
    )[0]

    assert actual.trust == 0.475
    assert BadStr.hooks == 0
    assert BadObject.hooks == 0


def test_private_mapper_rejects_invalid_timestamp_types_and_huge_integers():
    invalid = [True, False, "1700000000", object(), 10**10_000, -(10**10_000)]

    for index, timestamp in enumerate(invalid):
        document = Document(
            f"doc-invalid-{index}",
            "news",
            "wire",
            "BTC adoption expands",
            ts=timestamp,  # type: ignore[arg-type]
            meta={},
        )
        claim = legacy.Claim(f"invalid-{index}", document.text, document)
        with pytest.raises(ValueError, match="document timestamp"):
            legacy.score(
                [claim],
                now=1_700_000_000.0,
                dynamic_reputation=False,
                stance_client=object(),
            )


def test_private_mapper_rejects_numeric_subclasses_without_magic_hooks():
    class BadFloat(float):
        hooks = 0

        def __float__(self) -> float:
            type(self).hooks += 1
            raise RuntimeError("float must not run")

        def __repr__(self) -> str:
            type(self).hooks += 1
            raise RuntimeError("repr must not run")

    class BadInt(int):
        hooks = 0

        def __float__(self) -> float:
            type(self).hooks += 1
            raise RuntimeError("float must not run")

        def __repr__(self) -> str:
            type(self).hooks += 1
            raise RuntimeError("repr must not run")

    class BadStr(str):
        hooks = 0

        def __float__(self) -> float:
            type(self).hooks += 1
            raise RuntimeError("float must not run")

        def __str__(self) -> str:
            type(self).hooks += 1
            raise RuntimeError("str must not run")

        def __repr__(self) -> str:
            type(self).hooks += 1
            raise RuntimeError("repr must not run")

    for index, timestamp in enumerate((BadFloat(1.0), BadInt(1), BadStr("1"))):
        document = Document(
            f"doc-subclass-{index}",
            "news",
            "wire",
            "BTC adoption expands",
            ts=timestamp,  # type: ignore[arg-type]
            meta={},
        )
        claim = legacy.Claim(f"subclass-{index}", document.text, document)
        with pytest.raises(ValueError, match="document timestamp"):
            legacy.score(
                [claim],
                now=1_700_000_000.0,
                dynamic_reputation=False,
                stance_client=object(),
            )
    assert BadFloat.hooks == 0
    assert BadInt.hooks == 0
    assert BadStr.hooks == 0
