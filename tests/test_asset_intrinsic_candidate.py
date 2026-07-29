"""Canonical scorer candidate (shadow-only) contract tests for #876.

Verifies:
* AC5: flag-off byte-for-byte parity of run_kernel output + projected Report
  across a >=8-case corpus (the candidate never feeds back into KernelOutput
  or the Report).
* delta=0 control: zero intrinsic delta leaves calibrated confidence and
  decision state unchanged.
* AC6: missing/stale/conflicted/insufficient views collapse to exact-zero
  total_delta.
* cap: candidate_raw in [0, 1] and |total_delta| <= 0.08.
* direction invariance: CandidateShadow exposes no direction field.
* fail-closed: malformed view/object never raises and yields zero delta.
* AC7: trustforge_core never imports trustforge.asset_intrinsic* (the core
  purity boundary is intact).
"""

from __future__ import annotations

import ast
import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trustforge.agent.kernel_projection import project
from trustforge.asset_intrinsic import (
    ASSET_INTRINSIC_SCHEMA_VERSION as _unused,  # noqa: F401  (sanity import)
    AssetIntrinsicView,
    IntrinsicDimension,
    IntrinsicDimensionName,
    IntrinsicFactStatus,
    IntrinsicProvenance,
    STALE_WINDOW_DAYS,
)
from trustforge.asset_intrinsic_candidate import (
    CandidateShadow,
    CANDIDATE_SCHEMA_VERSION,
    compute_candidate_shadow,
)
from trustforge_core import (
    KernelClaim,
    KernelDocument,
    KernelInput,
    KernelOutput,
    run_kernel,
)

CANDIDATE_FLAG = "TRUSTFORGE_SHADOW_INTRINSIC_CANDIDATE_ENABLED"
REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# KernelInput corpus + serialization helpers
# ---------------------------------------------------------------------------


def _doc(doc_id: str, kind: str, source: str, text: str, ts: float) -> KernelDocument:
    return KernelDocument(doc_id, kind, source, text, ts)


def _claim(
    claim_id: str,
    text: str,
    doc: KernelDocument,
    *,
    ctype: str = "fact",
    direction: str = "neutral",
) -> KernelClaim:
    return KernelClaim(claim_id, text, doc, ctype, direction)


# >=8 varied corpus cases. Each exercises a different kernel decision path so
# byte-parity is proven across normal / abstain / low-confidence / direction /
# multi-coin shapes, not just one happy path.
CORPUS: list[tuple[str, tuple[KernelClaim, ...], float, str, str]] = [
    (
        "support_multi",
        (
            _claim(
                "c1",
                "BTC ETF inflows expanded",
                _doc("d1", "news", "reuters.com", "BTC ETF inflows expanded", 900.0),
                direction="bullish",
            ),
            _claim(
                "c2",
                "BTC exchange reserves fell",
                _doc(
                    "d2",
                    "onchain",
                    "glassnode.com",
                    "BTC exchange reserves fell",
                    910.0,
                ),
                direction="bullish",
            ),
            _claim(
                "c3",
                "BTC price broke resistance",
                _doc(
                    "d3", "price", "coingecko.com", "BTC price broke resistance", 920.0
                ),
                direction="bullish",
            ),
        ),
        1000.0,
        "BTC",
        "BTC outlook",
    ),
    (
        "abstain_single_social",
        (
            _claim(
                "c1",
                "BTC pump guaranteed",
                _doc(
                    "d1", "social", "anon.example", "BTC pump guaranteed profit", 900.0
                ),
                direction="bullish",
            ),
        ),
        1000.0,
        "BTC",
        "BTC outlook",
    ),
    (
        "single_high_trust_news",
        (
            _claim(
                "c1",
                "BTC ETF approved",
                _doc("d1", "news", "reuters.com", "BTC ETF approved", 900.0),
                direction="bullish",
            ),
        ),
        1000.0,
        "BTC",
        "BTC outlook",
    ),
    (
        "reason_codes_same_source",
        (
            _claim(
                "c1",
                "BTC old news",
                _doc("d1", "news", "reuters.com", "BTC old news", 100.0),
                direction="bullish",
            ),
            _claim(
                "c2",
                "BTC fresh news",
                _doc("d2", "news", "reuters.com", "BTC fresh news", 999.0),
                direction="bullish",
            ),
        ),
        1000.0,
        "BTC",
        "BTC outlook",
    ),
    (
        "contrarian_heavy",
        (
            _claim(
                "c1",
                "BTC ETF inflows",
                _doc("d1", "news", "reuters.com", "BTC ETF inflows", 900.0),
                direction="bullish",
            ),
            _claim(
                "c2",
                "BTC whale dumping",
                _doc("d2", "social", "anon.example", "BTC whale dumping now", 910.0),
                direction="bearish",
            ),
            _claim(
                "c3",
                "BTC hack rumor",
                _doc("d3", "social", "spam.example", "BTC hack rumor sell", 920.0),
                direction="bearish",
            ),
        ),
        1000.0,
        "BTC",
        "BTC outlook",
    ),
    (
        "multi_coin_eth",
        (
            _claim(
                "c1",
                "ETH staking inflows",
                _doc("d1", "onchain", "glassnode.com", "ETH staking inflows", 900.0),
                direction="bullish",
            ),
            _claim(
                "c2",
                "ETH dev activity up",
                _doc("d2", "news", "reuters.com", "ETH dev activity up", 910.0),
                direction="bullish",
            ),
            _claim(
                "c3",
                "ETH burn rate elevated",
                _doc("d3", "price", "coingecko.com", "ETH burn rate elevated", 920.0),
                direction="bullish",
            ),
        ),
        1000.0,
        "ETH",
        "ETH outlook",
    ),
    (
        "bearish_dominant",
        (
            _claim(
                "c1",
                "BTC regulatory crackdown",
                _doc("d1", "regulatory", "sec.gov", "BTC regulatory crackdown", 900.0),
                direction="bearish",
            ),
            _claim(
                "c2",
                "BTC exchange outflow stalled",
                _doc(
                    "d2",
                    "onchain",
                    "glassnode.com",
                    "BTC exchange outflow stalled",
                    910.0,
                ),
                direction="bearish",
            ),
            _claim(
                "c3",
                "BTC ETF outflows",
                _doc("d3", "news", "reuters.com", "BTC ETF outflows", 920.0),
                direction="bearish",
            ),
        ),
        1000.0,
        "BTC",
        "BTC outlook",
    ),
    (
        "mixed_kinds_four_sources",
        (
            _claim(
                "c1",
                "BTC news positive",
                _doc("d1", "news", "reuters.com", "BTC news positive", 900.0),
                direction="bullish",
            ),
            _claim(
                "c2",
                "BTC onchain healthy",
                _doc("d2", "onchain", "glassnode.com", "BTC onchain healthy", 910.0),
                direction="bullish",
            ),
            _claim(
                "c3",
                "BTC price stable",
                _doc("d3", "price", "coingecko.com", "BTC price stable", 920.0),
                direction="bullish",
            ),
            _claim(
                "c4",
                "BTC regulation clear",
                _doc("d4", "regulatory", "sec.gov", "BTC regulation clear", 930.0),
                direction="bullish",
            ),
        ),
        1000.0,
        "BTC",
        "BTC outlook",
    ),
]


def _snapshot(ko: KernelOutput, judgment) -> str:
    """Canonical JSON snapshot of KernelOutput + projected Report."""
    payload = {
        "kernel_output": dataclasses.asdict(ko),
        "report": dataclasses.asdict(judgment),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Intrinsic view builders (pure, self-contained)
# ---------------------------------------------------------------------------


_AS_OF = datetime(2026, 7, 29, tzinfo=timezone.utc)


def _provenance() -> IntrinsicProvenance:
    return IntrinsicProvenance(
        source_urls=("https://a.example/source", "https://b.example/record"),
        methodology="test methodology",
        content_hash="a" * 64,
        coverage="test coverage",
        evidence_path="data/asset_intrinsic_evidence/btc-issuance-v30.txt",
        source_revision="test-revision",
        evidence_kind="upstream_excerpt",
        source_coordinates="test coordinates",
    )


def _known_dim(
    name: IntrinsicDimensionName, value: float, *, as_of: datetime
) -> IntrinsicDimension:
    return IntrinsicDimension(
        name=name,
        status=IntrinsicFactStatus.KNOWN,
        value=value,
        as_of=as_of,
        valid_from=as_of,
        valid_until=None,
        fetched_at=as_of,
        provenance=_provenance(),
    )


def _unknown_dim(
    name: IntrinsicDimensionName, *, as_of: datetime
) -> IntrinsicDimension:
    return IntrinsicDimension(
        name=name,
        status=IntrinsicFactStatus.UNKNOWN,
        value=None,
        as_of=as_of,
        valid_from=as_of,
        valid_until=None,
        fetched_at=as_of,
        provenance=_provenance(),
    )


def _conflicted_dim(
    name: IntrinsicDimensionName, *, as_of: datetime
) -> IntrinsicDimension:
    return IntrinsicDimension(
        name=name,
        status=IntrinsicFactStatus.CONFLICTED,
        value=None,
        as_of=as_of,
        valid_from=as_of,
        valid_until=None,
        fetched_at=as_of,
        provenance=_provenance(),
    )


_KNOWN_DIM_NAMES = (
    IntrinsicDimensionName.ISSUANCE_PREDICTABILITY,
    IntrinsicDimensionName.CONTROL_DISPERSION,
    IntrinsicDimensionName.SUPPLY_VERIFIABILITY,
)


def _positive_delta_view() -> AssetIntrinsicView:
    """Gate-passing view with three known high-value dims -> total_delta > 0."""
    return AssetIntrinsicView(
        asset_id="asset:btc",
        as_of=_AS_OF,
        dimensions=tuple(_known_dim(n, 1.0, as_of=_AS_OF) for n in _KNOWN_DIM_NAMES),
    )


def _missing_view() -> AssetIntrinsicView:
    """All-unknown view: no known facts -> exact-zero total_delta."""
    return AssetIntrinsicView(
        asset_id="asset:btc",
        as_of=_AS_OF,
        dimensions=tuple(_unknown_dim(n, as_of=_AS_OF) for n in _KNOWN_DIM_NAMES),
    )


def _insufficient_view() -> AssetIntrinsicView:
    """Only one known dim: coverage gate fails (known<3) -> exact-zero delta."""
    dims = [_unknown_dim(IntrinsicDimensionName.CONTROL_DISPERSION, as_of=_AS_OF)]
    dims.append(
        _known_dim(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, 1.0, as_of=_AS_OF)
    )
    dims.append(_unknown_dim(IntrinsicDimensionName.SUPPLY_VERIFIABILITY, as_of=_AS_OF))
    return AssetIntrinsicView(
        asset_id="asset:btc", as_of=_AS_OF, dimensions=tuple(dims)
    )


def _stale_view() -> AssetIntrinsicView:
    """Known dims whose as_of is older than STALE_WINDOW_DAYS -> stale -> 0."""
    stale_as_of = _AS_OF - timedelta(days=STALE_WINDOW_DAYS + 40)
    return AssetIntrinsicView(
        asset_id="asset:btc",
        as_of=_AS_OF,
        dimensions=tuple(
            _known_dim(n, 1.0, as_of=stale_as_of) for n in _KNOWN_DIM_NAMES
        ),
    )


def _conflicted_view() -> AssetIntrinsicView:
    """A conflicted dim contributes 0; remaining unknown -> exact-zero delta."""
    dims = [
        _conflicted_dim(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, as_of=_AS_OF),
        _unknown_dim(IntrinsicDimensionName.CONTROL_DISPERSION, as_of=_AS_OF),
        _unknown_dim(IntrinsicDimensionName.SUPPLY_VERIFIABILITY, as_of=_AS_OF),
    ]
    return AssetIntrinsicView(
        asset_id="asset:btc", as_of=_AS_OF, dimensions=tuple(dims)
    )


# ---------------------------------------------------------------------------
# AC5: byte-for-byte flag-off parity across the corpus
# ---------------------------------------------------------------------------


class TestByteParity:
    """AC5: toggling the candidate flag never changes KernelOutput or Report."""

    @pytest.mark.parametrize("case_id, claims, now, coin, query", CORPUS)
    def test_flag_off_vs_on_snapshot_is_identical(
        self,
        monkeypatch,
        case_id,
        claims,
        now,
        coin,
        query,
    ):
        ki = KernelInput(claims=claims, pit_epoch=now, coin=coin, query=query)

        monkeypatch.delenv(CANDIDATE_FLAG, raising=False)
        ko_off = run_kernel(ki)
        report_off = project(ko_off, coin=coin)
        snapshot_off = _snapshot(ko_off, report_off)

        # Flag ON: also exercise compute_candidate_shadow on each corpus to
        # prove the candidate executes yet the official output is untouched.
        monkeypatch.setenv(CANDIDATE_FLAG, "1")
        _ = compute_candidate_shadow(ko_off, _positive_delta_view(), query=query)
        ko_on = run_kernel(ki)
        report_on = project(ko_on, coin=coin)
        snapshot_on = _snapshot(ko_on, report_on)

        # The snapshot diff must be empty.
        assert snapshot_off == snapshot_on, (
            f"byte-parity broken for {case_id}: candidate must be shadow-only"
        )

    def test_corpus_has_at_least_eight_cases(self):
        # AC5 explicitly requires >=8 corpus cases.
        assert len(CORPUS) >= 8


# ---------------------------------------------------------------------------
# delta=0 control
# ---------------------------------------------------------------------------


class TestDeltaZeroControl:
    """Zero intrinsic delta leaves calibrated confidence and state unchanged."""

    def test_zero_delta_keeps_calibrated_and_state_identical(self):
        claims = CORPUS[0][1]  # support_multi: small capped==uncapped corpus
        ki = KernelInput(
            claims=claims, pit_epoch=1000.0, coin="BTC", query="BTC outlook"
        )
        ko = run_kernel(ki)

        shadow = compute_candidate_shadow(ko, _missing_view(), query="BTC")
        # missing view -> total_delta is exact zero
        assert shadow.total_delta == 0.0
        assert shadow.candidate_raw == shadow.baseline_raw
        assert shadow.calibrated_delta == 0.0
        assert shadow.candidate_calibrated == shadow.baseline_calibrated
        assert shadow.decision_state_changed is False
        assert shadow.candidate_decision_state == shadow.baseline_decision_state


# ---------------------------------------------------------------------------
# AC6: exact-zero for missing / stale / conflicted / insufficient views
# ---------------------------------------------------------------------------


class TestExactZero:
    """AC6: missing/stale/conflicted/insufficient views yield total_delta == 0.0."""

    @pytest.mark.parametrize(
        ("label", "view_fn"),
        [
            ("missing", _missing_view),
            ("stale", _stale_view),
            ("conflicted", _conflicted_view),
            ("insufficient_coverage", _insufficient_view),
        ],
    )
    def test_total_delta_is_exact_zero(self, label, view_fn):
        claims = CORPUS[0][1]
        ki = KernelInput(
            claims=claims, pit_epoch=1000.0, coin="BTC", query="BTC outlook"
        )
        ko = run_kernel(ki)
        shadow = compute_candidate_shadow(ko, view_fn(), query="BTC")
        assert shadow.total_delta == 0.0, f"{label} view must produce exact-zero delta"
        assert shadow.calibrated_delta == 0.0


# ---------------------------------------------------------------------------
# Cap invariants
# ---------------------------------------------------------------------------


class TestCaps:
    """candidate_raw in [0,1] and |total_delta| <= 0.08 for every case."""

    @pytest.mark.parametrize("case_id, claims, now, coin, query", CORPUS)
    def test_candidate_raw_and_delta_are_bounded(
        self, case_id, claims, now, coin, query
    ):
        ki = KernelInput(claims=claims, pit_epoch=now, coin=coin, query=query)
        ko = run_kernel(ki)
        shadow = compute_candidate_shadow(ko, _positive_delta_view(), query=query)
        assert 0.0 <= shadow.candidate_raw <= 1.0
        assert abs(shadow.total_delta) <= 0.08
        assert 0.0 <= shadow.baseline_raw <= 1.0
        assert 0.0 <= shadow.baseline_calibrated <= 1.0
        assert 0.0 <= shadow.candidate_calibrated <= 1.0

    def test_positive_delta_view_actually_moves_raw(self):
        # Guard against the corpus silently producing zero deltas, which would
        # make the cap test vacuous.
        claims = CORPUS[0][1]
        ki = KernelInput(
            claims=claims, pit_epoch=1000.0, coin="BTC", query="BTC outlook"
        )
        ko = run_kernel(ki)
        shadow = compute_candidate_shadow(ko, _positive_delta_view(), query="BTC")
        assert shadow.total_delta > 0.0
        assert shadow.candidate_raw > shadow.baseline_raw


# ---------------------------------------------------------------------------
# Direction invariance
# ---------------------------------------------------------------------------


class TestDirectionInvariance:
    """CandidateShadow must not carry a direction field (constraint #2)."""

    def test_candidate_shadow_has_no_direction_field(self):
        claims = CORPUS[0][1]
        ki = KernelInput(
            claims=claims, pit_epoch=1000.0, coin="BTC", query="BTC outlook"
        )
        ko = run_kernel(ki)
        shadow = compute_candidate_shadow(ko, _positive_delta_view(), query="BTC")
        assert isinstance(shadow, CandidateShadow)
        assert not hasattr(shadow, "direction")
        # Ensure the candidate never reports a direction-like derived value.
        for forbidden in ("candidate_direction", "baseline_direction", "direction"):
            assert not hasattr(shadow, forbidden)

    def test_direction_of_kernel_output_is_independent_of_candidate(self):
        claims = CORPUS[6][1]  # bearish_dominant
        ki = KernelInput(
            claims=claims, pit_epoch=1000.0, coin="BTC", query="BTC outlook"
        )
        ko = run_kernel(ki)
        before = ko.direction
        _ = compute_candidate_shadow(ko, _positive_delta_view(), query="BTC")
        # Candidate is read-only; the kernel output direction is unchanged.
        assert ko.direction == before


# ---------------------------------------------------------------------------
# Fail-closed
# ---------------------------------------------------------------------------


class TestFailClosed:
    """Any malformed input collapses to a zero-delta CandidateShadow, no raise."""

    def test_non_view_object_yields_zero_delta_without_raising(self):
        claims = CORPUS[0][1]
        ki = KernelInput(
            claims=claims, pit_epoch=1000.0, coin="BTC", query="BTC outlook"
        )
        ko = run_kernel(ki)
        shadow = compute_candidate_shadow(ko, object(), query="BTC")  # type: ignore[arg-type]
        assert shadow.total_delta == 0.0
        assert shadow.calibrated_delta == 0.0
        assert shadow.candidate_raw == shadow.baseline_raw
        assert shadow.candidate_calibrated == shadow.baseline_calibrated
        assert shadow.decision_state_changed is False
        assert shadow.facts_hash == ""

    def test_none_view_yields_zero_delta_without_raising(self):
        claims = CORPUS[0][1]
        ki = KernelInput(
            claims=claims, pit_epoch=1000.0, coin="BTC", query="BTC outlook"
        )
        ko = run_kernel(ki)
        shadow = compute_candidate_shadow(ko, None, query="BTC")  # type: ignore[arg-type]
        assert shadow.total_delta == 0.0
        assert shadow.decision_state_changed is False

    def test_malformed_kernel_output_like_object_fail_closes(self):
        class _Broken:
            trust_score = "not-a-number"  # type: ignore[assignment]
            confidence = 0.4
            independent_sources = 1
            decision_state = "normal"
            supporting = ()
            contrarian = ()

        shadow = compute_candidate_shadow(
            _Broken(), _positive_delta_view(), query="BTC"
        )  # type: ignore[arg-type]
        assert shadow.total_delta == 0.0
        assert shadow.calibrated_delta == 0.0
        assert shadow.decision_state_changed is False


# ---------------------------------------------------------------------------
# Structural / schema sanity
# ---------------------------------------------------------------------------


class TestSchema:
    def test_frozen_dataclass(self):
        claims = CORPUS[0][1]
        ki = KernelInput(
            claims=claims, pit_epoch=1000.0, coin="BTC", query="BTC outlook"
        )
        ko = run_kernel(ki)
        shadow = compute_candidate_shadow(ko, _positive_delta_view(), query="BTC")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            shadow.total_delta = 99.0  # type: ignore[misc]

    def test_facts_hash_is_stable_and_prefixed(self):
        claims = CORPUS[0][1]
        ki = KernelInput(
            claims=claims, pit_epoch=1000.0, coin="BTC", query="BTC outlook"
        )
        ko = run_kernel(ki)
        view = _positive_delta_view()
        a = compute_candidate_shadow(ko, view, query="BTC")
        b = compute_candidate_shadow(ko, view, query="different query")
        # query must not influence the candidate computation or facts hash.
        assert a.facts_hash == b.facts_hash
        assert a.facts_hash.startswith("sha256:")

    def test_schema_version_constant_exposed(self):
        assert isinstance(CANDIDATE_SCHEMA_VERSION, str)
        assert CANDIDATE_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# AC7: core purity boundary
# ---------------------------------------------------------------------------


class TestCoreImportBoundary:
    """AC7: trustforge_core must never import trustforge.asset_intrinsic*."""

    @pytest.mark.parametrize(
        "path",
        sorted((REPO_ROOT / "src" / "trustforge_core").rglob("*.py")),
    )
    def test_no_core_module_imports_app_asset_intrinsic(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        offending = {
            name
            for name in imported
            if name == "trustforge" or name.startswith("trustforge.")
        }
        # The core package must be entirely dependency-free: no app imports at
        # all, which subsumes the asset_intrinsic* prohibition.
        assert not offending, (
            f"{path.relative_to(REPO_ROOT)} imports app-layer modules: {offending}"
        )

    def test_candidate_composition_is_owned_by_core(self):
        import trustforge_core

        assert hasattr(trustforge_core, "compose_intrinsic_candidate")
        assert hasattr(trustforge_core, "CandidateShadow")
