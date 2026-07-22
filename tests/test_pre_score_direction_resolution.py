from __future__ import annotations

import builtins
import math
import subprocess
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from trustforge.direction_resolution import (
    DIRECTION_POLICY_VERSION,
    ResolvedDirection,
    resolve_direction,
    resolve_ohlcv_direction,
    semantic_evidence,
)
from trustforge.agent.kernel_mapper import to_kernel_claim, to_kernel_run_resolution
from trustforge.agent.orchestrator import _direction as legacy_direction
from trustforge.ingestion.base import Document
from trustforge.semantic_direction import DirectionVote, analyze_direction
from trustforge.trust.scoring import Claim, ScoredClaim
from trustforge_core import (
    KERNEL_RESOLUTION_VERSION,
    KernelClaim,
    KernelClaimResolution,
    KernelInput,
    run_kernel,
)


PIT = datetime(2026, 7, 22, tzinfo=timezone.utc).timestamp()


def test_direction_resolver_import_boundary_excludes_legacy_scoring_and_ingestion():
    code = (
        "import sys; sys.path.insert(0, 'src'); import trustforge.direction_resolution; "
        "assert 'trustforge.trust.scoring' not in sys.modules; "
        "assert 'trustforge.ingestion.base' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def _app_claim(
    claim_id: str,
    *,
    kind: str = "price",
    coin: str = "BTC",
    text: str = "BTC fact",
    ts: float = PIT - 60,
    meta: dict | None = None,
) -> Claim:
    values = {"coin": coin}
    values.update(meta or {})
    if "ret_pct" in values and "date_range" not in values:
        values["date_range"] = "2026-07-01~2026-07-22"
        values["data_lineage"] = {
            "analysis_window": "2026-07-01~2026-07-22"
        }
    return Claim(
        claim_id,
        text,
        Document(claim_id, kind, "fixture", text, ts=ts, meta=values),
    )


def _claim(*args, **kwargs) -> KernelClaim:
    return to_kernel_claim(_app_claim(*args, **kwargs))


def _raw_price_fact(claim_id: str, *, meta: dict) -> KernelClaim:
    app_claim = Claim(
        claim_id,
        "BTC production-shaped price fact",
        Document(
            claim_id,
            "price",
            "ohlcv-csv",
            "BTC production-shaped price fact",
            ts=PIT - 1,
            meta={"coin": "BTC", **meta},
        ),
    )
    return to_kernel_claim(app_claim)


@pytest.mark.parametrize(
    ("ret_pct", "expected"),
    [(5.0, "bullish"), (-5.0, "bearish"), (0.0, "neutral")],
)
def test_loaded_return_facts_cover_positive_negative_and_zero(ret_pct, expected):
    result = resolve_ohlcv_direction(
        [_claim("p", meta={"ret_pct": ret_pct})], coin="BTC", pit_epoch=PIT
    )
    assert result.value == expected
    assert result.method == "ohlcv-return"
    assert result.input_ids == ("p",)


@pytest.mark.parametrize(
    "value", [None, "bad", [], {}, math.nan, math.inf, -math.inf]
)
def test_invalid_return_facts_fail_closed(value):
    result = resolve_ohlcv_direction(
        [_claim("p", meta={"ret_pct": value})], coin="BTC", pit_epoch=PIT
    )
    assert result.value == "unknown"
    assert result.method == "no-signal"


def test_future_timestamp_and_future_date_fail_closed():
    claims = [
        _claim("future-ts", ts=PIT + 1, meta={"ret_pct": 99}),
        _claim("future-date", meta={"date": "2026-07-23", "close": 99}),
    ]
    assert resolve_ohlcv_direction(claims, coin="BTC", pit_epoch=PIT).value == "unknown"


def test_price_fact_window_schema_accepts_past_and_rejects_unsafe_variants():
    past = _raw_price_fact(
        "past",
        meta={
            "ret_pct": 5,
            "date_range": "2026-07-01~2026-07-22",
            "data_lineage": {"analysis_window": "2026-07-01~2026-07-22"},
        },
    )
    future = _raw_price_fact(
        "future",
        meta={
            "ret_pct": 5,
            "date_range": "2026-07-01~2026-07-23",
            "data_lineage": {"analysis_window": "2026-07-01~2026-07-23"},
        },
    )
    malformed = _raw_price_fact(
        "malformed",
        meta={
            "ret_pct": 5,
            "date_range": "not-a-window",
            "data_lineage": {"analysis_window": "not-a-window"},
        },
    )
    missing = _raw_price_fact("missing", meta={"ret_pct": 5})
    conflict = _raw_price_fact(
        "conflict",
        meta={
            "ret_pct": 5,
            "date_range": "2026-07-01~2026-07-22",
            "data_lineage": {"analysis_window": "2026-07-01~2026-07-23"},
        },
    )
    assert resolve_ohlcv_direction([past], coin="BTC", pit_epoch=PIT).value == "bullish"
    for claim in (future, malformed, missing, conflict):
        assert resolve_ohlcv_direction([claim], coin="BTC", pit_epoch=PIT).value == "unknown"


@pytest.mark.parametrize("pit_epoch", [-1, math.nan, math.inf, 10**30])
def test_invalid_pit_never_invokes_semantic_provider(pit_epoch):
    calls = 0

    def provider(_evidence):
        nonlocal calls
        calls += 1
        return [DirectionVote("news", "bullish", 0.9, "must not run")]

    with pytest.raises(ValueError, match="pit_epoch"):
        resolve_direction(
            [_claim("n", kind="news")],
            coin="BTC",
            pit_epoch=pit_epoch,
            semantic_provider=provider,
        )
    assert calls == 0


@pytest.mark.parametrize("timestamp", [PIT + 1, -1, math.nan, math.inf, "123", object()])
def test_invalid_claim_timestamp_never_reaches_semantic_provider(timestamp):
    claim = _claim("n", kind="news")
    object.__setattr__(claim.document, "timestamp", timestamp)
    calls = 0

    def provider(_evidence):
        nonlocal calls
        calls += 1
        return [DirectionVote("news", "bullish", 0.9, "must not run")]

    result = resolve_direction(
        [claim], coin="BTC", pit_epoch=PIT, semantic_provider=provider
    )
    assert calls == 0
    assert result.value == "unknown"


@pytest.mark.parametrize(
    ("field", "value"),
    [("text", object()), ("kind", 7), ("metadata", (("coin", object()),))],
)
def test_tampered_document_graph_never_reaches_semantic_provider(field, value):
    claim = _claim("n", kind="news")
    object.__setattr__(claim.document, field, value)
    calls = 0

    def provider(_evidence):
        nonlocal calls
        calls += 1
        return [DirectionVote("news", "bullish", 0.9, "must not run")]

    result = resolve_direction(
        [claim], coin="BTC", pit_epoch=PIT, semantic_provider=provider
    )
    assert calls == 0
    assert result.value == "unknown"


def test_daily_closes_take_priority_over_return_facts():
    claims = [
        _claim("old", meta={"date": "2026-07-01", "close": 100}),
        _claim("new", meta={"date": "2026-07-22", "close": 110}),
        _claim("return", meta={"ret_pct": -90}),
    ]
    result = resolve_ohlcv_direction(claims, coin="BTC", pit_epoch=PIT)
    assert (result.value, result.method) == ("bullish", "ohlcv-close")
    assert result.input_ids == ("new", "old")


def test_same_date_duplicate_closes_do_not_form_a_return():
    claims = [
        _claim("a", meta={"date": "2026-07-01", "close": 100}),
        _claim("b", meta={"date": "2026-07-01", "close": 110}),
    ]
    result = resolve_ohlcv_direction(claims, coin="BTC", pit_epoch=PIT)
    assert result.value == "unknown"


def test_conflicting_same_date_close_is_excluded_fail_closed():
    claims = [
        _claim("old-a", meta={"date": "2026-07-01", "close": 100}),
        _claim("old-b", meta={"date": "2026-07-01", "close": 101}),
        _claim("new", meta={"date": "2026-07-22", "close": 110}),
    ]
    result = resolve_ohlcv_direction(claims, coin="BTC", pit_epoch=PIT)
    assert result.value == "unknown"


def test_overflowing_close_change_and_return_mean_fail_closed():
    closes = [
        _claim("old", meta={"date": "2026-07-01", "close": 1e-308}),
        _claim("new", meta={"date": "2026-07-22", "close": 1e308}),
    ]
    returns = [
        _claim("a", meta={"ret_pct": 1e308}),
        _claim("b", meta={"ret_pct": 1e308}),
    ]
    assert resolve_ohlcv_direction(closes, coin="BTC", pit_epoch=PIT).value == "unknown"
    assert resolve_ohlcv_direction(returns, coin="BTC", pit_epoch=PIT).value == "unknown"


def test_coin_scope_excludes_other_coin():
    claims = [
        _claim("eth", coin="ETH", text="ETH", meta={"ret_pct": -99}),
        _claim("btc", coin="BTC", text="BTC", meta={"ret_pct": 6}),
    ]
    result = resolve_ohlcv_direction(claims, coin="BTC", pit_epoch=PIT)
    assert result.value == "bullish"
    assert result.input_ids == ("btc",)


def test_coin_scope_keeps_truly_market_wide_fact_without_explicit_coin():
    app_claim = Claim(
        "market",
        "market-wide price fact",
            Document(
                "market", "price", "fixture", "market-wide price fact",
                ts=PIT - 1,
                meta={
                    "ret_pct": 4,
                    "date_range": "2026-07-01~2026-07-22",
                    "data_lineage": {
                        "analysis_window": "2026-07-01~2026-07-22"
                    },
                },
            ),
    )
    result = resolve_ohlcv_direction(
        [to_kernel_claim(app_claim)], coin="BTC", pit_epoch=PIT
    )
    assert (result.value, result.input_ids) == ("bullish", ("market",))


def test_conflicting_returns_are_neutral_and_permutation_is_deterministic():
    claims = [
        _claim("a", meta={"ret_pct": 8}),
        _claim("b", meta={"ret_pct": -8}),
    ]
    left = resolve_ohlcv_direction(claims, coin="BTC", pit_epoch=PIT)
    right = resolve_ohlcv_direction(list(reversed(claims)), coin="BTC", pit_epoch=PIT)
    assert left == right
    assert left.value == "neutral"


def test_duplicate_ids_fail_closed():
    with pytest.raises(ValueError, match="duplicate claim IDs"):
        resolve_ohlcv_direction(
            [_claim("same", meta={"ret_pct": 5}), _claim("same", meta={"ret_pct": 6})],
            coin="BTC",
            pit_epoch=PIT,
        )


def test_semantic_input_matches_legacy_text_kind_grouping():
    claims = [
        _claim("p", kind="price", text="price text"),
        _claim("r", kind="regulatory", text="reg text"),
        _claim("s", kind="social", text="social text"),
    ]
    assert semantic_evidence(claims, coin="BTC") == {
        "price": ["price text"],
        "news": ["reg text"],
        "sentiment": ["social text"],
    }


def test_single_coin_semantic_provider_input_has_legacy_parity(monkeypatch):
    app_claims = [
        _app_claim("p", kind="price", text="price text"),
        _app_claim("r", kind="regulatory", text="reg text"),
    ]
    claims = [to_kernel_claim(claim) for claim in app_claims]
    scored = [ScoredClaim(claim, 0.8) for claim in app_claims]
    observed = []

    class OnlineClient:
        offline = False

    def legacy_provider(evidence, _client):
        observed.append(evidence)
        return [DirectionVote("price", "bullish", 0.9, "fixture")]

    monkeypatch.setattr("trustforge.agent.orchestrator.BedrockClient", OnlineClient)
    monkeypatch.setattr("trustforge.semantic_direction.analyze_direction", legacy_provider)
    assert legacy_direction(scored, all_scored=scored) == "偏多"

    result = resolve_direction(
        claims,
        coin="BTC",
        pit_epoch=PIT,
        semantic_provider=lambda evidence: (
            observed.append(evidence)
            or [DirectionVote("price", "bullish", 0.9, "fixture")]
        ),
    )
    assert result.value == "bullish"
    assert observed[0] == observed[1]


def test_mixed_coin_characterization_rejects_legacy_pollution(monkeypatch):
    app_claims = [
        _app_claim("btc", kind="news", coin="BTC", text="BTC adoption"),
        _app_claim("eth", kind="news", coin="ETH", text="ETH exploit"),
    ]
    claims = [to_kernel_claim(claim) for claim in app_claims]
    scored = [ScoredClaim(claim, 0.8) for claim in app_claims]
    legacy_inputs = []
    new_inputs = []

    class OnlineClient:
        offline = False

    def legacy_provider(evidence, _client):
        legacy_inputs.append(evidence)
        return [DirectionVote("news", "neutral", 0.5, "fixture")]

    monkeypatch.setattr("trustforge.agent.orchestrator.BedrockClient", OnlineClient)
    monkeypatch.setattr("trustforge.semantic_direction.analyze_direction", legacy_provider)
    legacy_direction(scored, all_scored=scored)
    resolve_direction(
        claims,
        coin="BTC",
        pit_epoch=PIT,
        semantic_provider=lambda evidence: (
            new_inputs.append(evidence)
            or [DirectionVote("news", "neutral", 0.5, "fixture")]
        ),
    )
    assert legacy_inputs == [{"news": ["BTC adoption", "ETH exploit"]}]
    assert new_inputs == [{"news": ["BTC adoption"]}]


def test_provider_completion_count_and_prompt_shape_match_legacy(monkeypatch):
    app_claims = [
        _app_claim("p", kind="price", text="price text"),
        _app_claim("n", kind="news", text="news text"),
        _app_claim("o", kind="onchain", text="onchain text"),
        _app_claim("s", kind="social", text="sentiment text"),
    ]
    claims = [to_kernel_claim(claim) for claim in app_claims]
    scored = [ScoredClaim(claim, 0.8) for claim in app_claims]

    class RecordingClient:
        offline = False

        def __init__(self):
            self.calls = []

        def complete(self, *, system, prompt):
            self.calls.append((system, prompt))
            return SimpleNamespace(
                text='{"direction":"neutral","confidence":0.5,"reasoning":"fixture"}'
            )

    legacy_client = RecordingClient()
    new_client = RecordingClient()
    monkeypatch.setattr(
        "trustforge.agent.orchestrator.BedrockClient", lambda: legacy_client
    )
    legacy_direction(scored, all_scored=scored)
    resolve_direction(
        claims,
        coin="BTC",
        pit_epoch=PIT,
        semantic_provider=lambda evidence: analyze_direction(evidence, new_client),
    )
    # Both paths make exactly the same three prioritized completions.  Identical
    # system/prompt bytes preserve provider-side input token and cost semantics.
    assert len(legacy_client.calls) == len(new_client.calls) == 3
    assert legacy_client.calls == new_client.calls


def test_semantic_callback_called_once_and_lineage_does_not_copy_cost():
    calls = []

    def provider(evidence):
        calls.append(evidence)
        return [DirectionVote("news", "bullish", 0.9, "fixture")]

    result = resolve_direction(
        [_claim("n", kind="news", text="BTC adoption")],
        coin="BTC",
        pit_epoch=PIT,
        semantic_provider=provider,
    )
    assert len(calls) == 1
    assert result.value == "bullish"
    assert result.method == "semantic-provider"
    assert result.input_ids == ("n",)
    assert not hasattr(result, "cost")
    assert not hasattr(result, "tokens")


def test_semantic_failure_falls_back_without_second_provider_call():
    calls = 0

    def provider(_evidence):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider down")

    result = resolve_direction(
        [_claim("n", kind="news"), _claim("p", meta={"ret_pct": -5})],
        coin="BTC",
        pit_epoch=PIT,
        semantic_provider=provider,
    )
    assert calls == 1
    assert (result.value, result.method) == ("bearish", "ohlcv-return")


def test_semantic_generator_failure_during_iteration_falls_back():
    def provider(_evidence):
        yield DirectionVote("news", "bullish", 0.9, "partial")
        raise RuntimeError("stream interrupted")

    result = resolve_direction(
        [_claim("n", kind="news"), _claim("p", meta={"ret_pct": -5})],
        coin="BTC",
        pit_epoch=PIT,
        semantic_provider=provider,
    )
    assert (result.value, result.method) == ("bearish", "ohlcv-return")


def test_provider_vote_iterable_is_bounded_and_overflow_fails_closed():
    produced = 0

    def provider(_evidence):
        nonlocal produced
        for _ in range(5):
            produced += 1
            yield DirectionVote("news", "bullish", 0.9, "unbounded")

    result = resolve_direction(
        [_claim("n", kind="news")],
        coin="BTC",
        pit_epoch=PIT,
        semantic_provider=provider,
    )
    assert produced == 5
    assert result.value == "unknown"


def test_duplicate_semantic_source_type_fails_closed():
    result = resolve_direction(
        [_claim("n", kind="news")],
        coin="BTC",
        pit_epoch=PIT,
        semantic_provider=lambda _evidence: [
            DirectionVote("news", "bullish", 0.8, "one"),
            DirectionVote("news", "bullish", 0.7, "duplicate"),
        ],
    )
    assert result.value == "unknown"


def test_nonfinite_aggregate_confidence_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "trustforge.direction_resolution.aggregate_votes",
        lambda _votes: ("bullish", math.nan),
    )
    result = resolve_direction(
        [_claim("n", kind="news"), _claim("p", meta={"ret_pct": -5})],
        coin="BTC",
        pit_epoch=PIT,
        semantic_provider=lambda _evidence: [
            DirectionVote("news", "bullish", 0.9, "valid vote")
        ],
    )
    assert (result.value, result.method) == ("bearish", "ohlcv-return")


@pytest.mark.parametrize(
    "bad_votes",
    [
        [object()],
        [DirectionVote("news", "sideways", 0.5, "bad")],
        [DirectionVote("news", "bullish", math.nan, "bad")],
        [DirectionVote("unknown", "bullish", 0.5, "bad")],
    ],
)
def test_malformed_semantic_votes_fail_closed_to_ohlcv(bad_votes):
    result = resolve_direction(
        [_claim("n", kind="news"), _claim("p", meta={"ret_pct": 5})],
        coin="BTC",
        pit_epoch=PIT,
        semantic_provider=lambda _evidence: bad_votes,
    )
    assert (result.value, result.method) == ("bullish", "ohlcv-return")


def test_resolver_never_calls_scoring_kernel_io_or_connector(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("forbidden dependency called")

    import trustforge.trust.kernel as legacy_kernel
    import trustforge.trust.scoring as scoring
    import trustforge_core

    monkeypatch.setattr(scoring, "score", forbidden)
    monkeypatch.setattr(scoring, "aggregate", forbidden)
    monkeypatch.setattr(legacy_kernel, "run_kernel", forbidden)
    monkeypatch.setattr(trustforge_core, "run_kernel", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)

    result = resolve_direction(
        [_claim("p", meta={"ret_pct": 4})], coin="BTC", pit_epoch=PIT
    )
    assert result.value == "bullish"


def test_bullish_resolution_does_not_upgrade_kernel_abstain_state():
    direction = resolve_direction(
        [_claim("p", meta={"ret_pct": 10})], coin="BTC", pit_epoch=PIT
    )
    claim = _claim("only", kind="news", text="BTC single-source evidence")
    resolution = to_kernel_run_resolution(
        [KernelClaimResolution(claim.id)], direction
    )
    output = run_kernel(
        KernelInput((claim,), PIT, "BTC", "BTC outlook", resolution=resolution)
    )
    assert direction.value == output.direction == "bullish"
    assert output.abstain is True
    assert output.decision_state == "abstain"


def test_mapper_is_lossless_and_rejects_version_or_enum_mismatch():
    direction = ResolvedDirection(
        "unknown", DIRECTION_POLICY_VERSION, "no-signal", (), "none"
    )
    mapped = to_kernel_run_resolution(
        [KernelClaimResolution("claim")], direction
    )
    assert mapped.resolved_direction == direction.value
    assert mapped.resolution_version == KERNEL_RESOLUTION_VERSION

    with pytest.raises(ValueError, match="unsupported kernel resolution version"):
        to_kernel_run_resolution(
            [KernelClaimResolution("claim")],
            direction,
            resolution_version="future",
        )
    with pytest.raises(ValueError, match="unsupported resolved direction"):
        ResolvedDirection("sideways", DIRECTION_POLICY_VERSION, "no-signal", (), "")

    with pytest.raises(ValueError, match="direction must be an exact"):
        to_kernel_run_resolution([KernelClaimResolution("claim")], object())
    with pytest.raises(ValueError, match="KernelClaimResolution"):
        to_kernel_run_resolution([object()], direction)


def test_mapper_revalidates_object_setattr_tampered_direction():
    direction = ResolvedDirection(
        "bullish", DIRECTION_POLICY_VERSION, "no-signal", (), "valid"
    )
    object.__setattr__(direction, "value", "sideways")
    with pytest.raises(ValueError, match="unsupported resolved direction"):
        to_kernel_run_resolution([KernelClaimResolution("claim")], direction)


def test_policy_version_and_pit_validation_fail_closed():
    with pytest.raises(ValueError, match="policy_version"):
        ResolvedDirection("neutral", "future", "no-signal", (), "")
    with pytest.raises(ValueError, match="pit_epoch"):
        resolve_ohlcv_direction([], coin="BTC", pit_epoch=math.nan)
    with pytest.raises(ValueError, match="reason"):
        ResolvedDirection("neutral", DIRECTION_POLICY_VERSION, "no-signal", (), "bad\n")
    with pytest.raises(ValueError, match="input_ids"):
        ResolvedDirection(
            "neutral", DIRECTION_POLICY_VERSION, "no-signal", ("bad\x00",), ""
        )
