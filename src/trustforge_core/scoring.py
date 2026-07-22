"""Deterministic scoring primitives with no TrustForge application dependencies."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

from .contracts import (
    FIXED_HEURISTIC_VERSION,
    ISOTONIC_VERSION as _ISOTONIC_VERSION,
    KERNEL_CONTRACT_VERSION,
    STRICT_JSON_MAX_INTEGER,
    SUPPORTED_CALIBRATION_MODEL_VERSIONS,
    KernelClaim,
    KernelDocument,
    KernelInput,
    KernelOutput,
    KernelReputationTrace,
    KernelScoredClaim,
    require_supported_contract_version,
)
from .corroboration import canonical_source


# Backwards-compatible scoring-module re-export; canonical value lives in contracts.
ISOTONIC_VERSION = _ISOTONIC_VERSION


DEFAULT_SCORE_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("src", 0.50),
    ("corr", 0.25),
    ("rec", 0.15),
    ("manip", 0.40),
)

DEFAULT_SOURCE_REPUTATIONS: tuple[tuple[str, float], ...] = (
    ("price", 0.95),
    ("onchain", 0.95),
    ("regulatory", 0.90),
    ("hoyabit", 0.85),
    ("news", 0.65),
    ("social", 0.35),
    ("price_live", 0.90),
    ("sentiment", 0.50),
    ("dev_activity", 0.50),
    ("whale_onchain", 0.88),
    ("celebrity_trade", 0.50),
)

DEFAULT_HALF_LIVES: tuple[tuple[str, float], ...] = (
    ("default", 12.0),
    ("whale_onchain", 2.0),
    ("celebrity_trade", 2.0),
)
DEFAULT_STRENGTH_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("trust", 0.35),
    ("indep", 0.30),
    ("diversity", 0.15),
    ("dominance", 0.20),
)
DEFAULT_CALIBRATION_TABLE: tuple[tuple[float, float], ...] = (
    (0.00, 0.00),
    (0.10, 0.03),
    (0.20, 0.08),
    (0.30, 0.20),
    (0.40, 0.40),
    (0.55, 0.55),
    (0.70, 0.70),
    (0.85, 0.85),
    (1.00, 1.00),
)
INDEPENDENT_SOURCE_SATURATION = 4
KIND_DIVERSITY_SATURATION = 3
SUPPORTING_LIMIT = 10
CONTRARIAN_LIMIT = 5
_UNSET = object()

_COIN_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("BTC", ("btc", "bitcoin", "比特幣", "比特")),
    ("ETH", ("eth", "ethereum", "以太坊", "以太")),
    ("SOL", ("sol", "solana")),
    ("BNB", ("bnb", "binance")),
    ("XRP", ("xrp", "ripple", "瑞波")),
)

_MANIP_PATTERNS: tuple[str, ...] = (
    r"to the moon",
    r"暴漲",
    r"翻倍",
    r"\bshill\b",
    r"喊單",
    r"穩賺",
    r"financial advice",
    r"\bpump\b",
    r"快上車",
    r"百倍",
)
_MANIP_NEGATION = re.compile(r"不會|不太|不致|不至|不再|沒有|沒|尚未|未|無法|別|勿|非")


def _exact_number(value: object, *, field: str) -> float:
    if type(value) not in {int, float}:
        raise ValueError(f"{field} must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not finite:
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def _probability(value: object, *, field: str) -> float:
    number = _exact_number(value, field=field)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{field} must be between zero and one")
    return number


def _nonnegative_json_int(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0 or value > STRICT_JSON_MAX_INTEGER:
        raise ValueError(f"{field} must be a nonnegative JSON-safe integer")
    return value


def _validated_table(
    table: tuple[tuple[str, float], ...],
    *,
    field: str,
    required: frozenset[str] | None = None,
    positive: bool = False,
    probability: bool = False,
) -> dict[str, float]:
    if type(table) is not tuple:
        raise ValueError(f"{field} must be an immutable tuple table")
    result: dict[str, float] = {}
    for index, item in enumerate(table):
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
            raise ValueError(f"{field} entries must be exact (str, number) tuples")
        key = item[0]
        if key in result:
            raise ValueError(f"{field} keys must be unique")
        value = _exact_number(item[1], field=f"{field}[{index}].value")
        if positive and value <= 0:
            raise ValueError(f"{field} values must be positive")
        if not positive and value < 0:
            raise ValueError(f"{field} values must be nonnegative")
        if probability and value > 1:
            raise ValueError(f"{field} values must be between zero and one")
        result[key] = value
    if required is not None and frozenset(result) != required:
        raise ValueError(f"{field} must contain exactly the required keys")
    return result


def _validated_numeric_table(
    table: tuple[tuple[float, float], ...],
    *,
    field: str,
    probability: bool = False,
) -> None:
    if type(table) is not tuple:
        raise ValueError(f"{field} must be an immutable tuple table")
    previous_key: float | None = None
    for index, item in enumerate(table):
        if type(item) is not tuple or len(item) != 2:
            raise ValueError(f"{field} entries must be exact (number, number) tuples")
        key = _exact_number(item[0], field=f"{field}[{index}].key")
        value = _exact_number(item[1], field=f"{field}[{index}].value")
        if previous_key is not None and key < previous_key:
            raise ValueError(f"{field} keys must be sorted")
        if probability and (key < 0 or key > 1 or value < 0 or value > 1):
            raise ValueError(f"{field} values must be between zero and one")
        previous_key = key


def _calibration_table(
    version: str,
    table: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], ...]:
    if type(version) is not str or version not in SUPPORTED_CALIBRATION_MODEL_VERSIONS:
        raise ValueError("unsupported calibration model version")
    if type(table) is not tuple:
        raise ValueError("calibration_table must be an exact tuple")
    if version == FIXED_HEURISTIC_VERSION:
        if table:
            raise ValueError(
                "fixed calibration model does not accept calibration_table"
            )
        return DEFAULT_CALIBRATION_TABLE
    if len(table) < 2:
        raise ValueError("isotonic calibration_table must contain at least two points")
    validated: list[tuple[float, float]] = []
    previous_x = -1.0
    previous_y = -1.0
    for index, point in enumerate(table):
        if type(point) is not tuple or len(point) != 2:
            raise ValueError("calibration_table points must be exact tuples")
        x = _probability(point[0], field=f"calibration_table[{index}].x")
        y = _probability(point[1], field=f"calibration_table[{index}].y")
        if x <= previous_x:
            raise ValueError("calibration_table x values must be strictly increasing")
        if y < previous_y:
            raise ValueError("calibration_table y values must be nondecreasing")
        validated.append((x, y))
        previous_x, previous_y = x, y
    return tuple(validated)


def manipulation_hits(text: str) -> tuple[str, ...]:
    """Return manipulation-keyword matches in stable legacy pattern order."""
    if type(text) is not str:
        raise ValueError("text must be an exact string")
    hits: list[str] = []
    for pattern in _MANIP_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if _MANIP_NEGATION.search(text[max(0, match.start() - 4) : match.start()]):
                continue
            hits.append(match.group(0))
    return tuple(hits)


def manipulation_flags(text: str) -> tuple[str, ...]:
    """Return unique manipulation matches without changing their first-seen case."""
    seen: list[str] = []
    for hit in manipulation_hits(text):
        if hit not in seen:
            seen.append(hit)
    return tuple(seen)


def manipulation_penalty(text: str, kind: str, *, extra_hits: int = 0) -> float:
    """Return the bounded legacy keyword penalty from immutable inputs."""
    if type(text) is not str:
        raise ValueError("text must be an exact string")
    if type(kind) is not str:
        raise ValueError("kind must be an exact string")
    if type(extra_hits) is not int or extra_hits < 0:
        raise ValueError("extra_hits must be a nonnegative exact integer")
    weight = 1.5 if kind == "social" else 1.0
    hit_count = len(manipulation_hits(text))
    saturation_count = 2 if kind == "social" else 3
    if extra_hits >= max(0, saturation_count - hit_count):
        return 1.0
    return (hit_count + extra_hits) * 0.4 * weight


def corroboration_score(independent_sources: tuple[str, ...]) -> float:
    """Convert resolved unique source identities to the saturated legacy scalar."""
    if type(independent_sources) is not tuple or not all(
        type(source) is str for source in independent_sources
    ):
        raise ValueError("independent_sources must be an exact tuple of exact strings")
    count = len(set(independent_sources))
    return 1.0 - math.pow(0.5, count) if count else 0.0


def _exact_json_value(value: object, *, field: str) -> None:
    if value is None or type(value) in {bool, str}:
        return
    if type(value) is int:
        if not -STRICT_JSON_MAX_INTEGER <= value <= STRICT_JSON_MAX_INTEGER:
            raise ValueError(f"{field} integer is outside the strict JSON range")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{field} must contain finite JSON values")
        return
    if type(value) is tuple:
        for index, item in enumerate(value):
            _exact_json_value(item, field=f"{field}[{index}]")
        return
    raise ValueError(f"{field} must contain exact immutable JSON values")


def _metadata_coin(claim: KernelClaim) -> str | None:
    metadata = claim.document.metadata
    if type(metadata) is not tuple:
        raise ValueError("claim metadata must be an exact tuple")
    coin: str | None = None
    seen: set[str] = set()
    for index, item in enumerate(metadata):
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
            raise ValueError("claim metadata entries must be exact tuples")
        key, value = item
        if key in seen:
            raise ValueError("claim metadata keys must be unique")
        seen.add(key)
        _exact_json_value(value, field=f"claim metadata[{index}]")
        if key == "coin":
            if type(value) is not str:
                raise ValueError("claim metadata coin must be an exact string")
            coin = value
    return coin


def _validate_scored_claim_graph(item: KernelScoredClaim, *, index: int) -> None:
    prefix = f"scored_claims[{index}]"
    _probability(item.trust, field=f"{prefix}.trust")
    claim = item.claim
    if type(claim) is not KernelClaim:
        raise ValueError(f"{prefix}.claim must be an exact KernelClaim")
    for field, value in (
        ("id", claim.id),
        ("text", claim.text),
        ("claim_type", claim.claim_type),
        ("direction", claim.direction),
    ):
        if type(value) is not str:
            raise ValueError(f"{prefix}.claim.{field} must be an exact string")
    document = claim.document
    if type(document) is not KernelDocument:
        raise ValueError(f"{prefix}.claim.document must be an exact KernelDocument")
    for field, value in (
        ("id", document.id),
        ("kind", document.kind),
        ("source", document.source),
        ("text", document.text),
        ("url", document.url),
    ):
        if type(value) is not str:
            raise ValueError(f"{prefix}.claim.document.{field} must be an exact string")
    _exact_number(document.timestamp, field=f"{prefix}.claim.document.timestamp")
    _metadata_coin(claim)
    if type(item.components) is not tuple:
        raise ValueError(f"{prefix}.components must be an exact tuple")
    for component_index, component in enumerate(item.components):
        if (
            type(component) is not tuple
            or len(component) != 2
            or type(component[0]) is not str
        ):
            raise ValueError(f"{prefix}.components entries must be exact tuples")
        _exact_number(
            component[1], field=f"{prefix}.components[{component_index}].value"
        )
    trace = item.reputation_trace
    if trace is not None:
        if type(trace) is not KernelReputationTrace:
            raise ValueError(f"{prefix}.reputation_trace must be exact")
        if type(trace.source) is not str:
            raise ValueError(f"{prefix}.reputation_trace.source must be exact")
        if type(trace.mode) is not str or trace.mode not in {"entailment", "ds_em"}:
            raise ValueError(
                f"{prefix}.reputation_trace.mode must be entailment or ds_em"
            )
        _exact_number(trace.prior, field=f"{prefix}.reputation_trace.prior")
        _exact_number(trace.final, field=f"{prefix}.reputation_trace.final")
        for field, value in (
            ("agree_n", trace.agree_n),
            ("contradict_n", trace.contradict_n),
            ("iterations_run", trace.iterations_run),
        ):
            _nonnegative_json_int(value, field=f"{prefix}.reputation_trace.{field}")
    for field, values in (
        ("manip_flags", item.manip_flags),
        ("info_flags", item.info_flags),
    ):
        if type(values) is not tuple or not all(type(value) is str for value in values):
            raise ValueError(f"{prefix}.{field} must be an exact tuple of strings")


def _alias_in(alias: str, text: str) -> bool:
    if alias.isascii():
        return (
            re.search(r"\b" + re.escape(alias) + r"\b", text, re.IGNORECASE | re.ASCII)
            is not None
        )
    return alias in text


def _coins_mentioned(text: str) -> frozenset[str]:
    found: set[str] = set()
    for code, aliases in _COIN_ALIASES:
        if any(_alias_in(alias, text) for alias in aliases):
            found.add(code)
    return frozenset(found)


def _normalize(text: str) -> frozenset[str]:
    if type(text) is not str:
        raise ValueError("text must be an exact string")
    return frozenset(
        token for token in re.findall(r"[\w一-鿿]+", text.lower()) if len(token) > 1
    )


def _coin_targets(coin: str) -> frozenset[str]:
    return frozenset(
        part.strip().upper() for part in re.split(r"[,\s]+", coin) if part.strip()
    )


def _matches_coin(scored: KernelScoredClaim, coin: str) -> bool:
    targets = _coin_targets(coin)
    if not targets:
        return True
    explicit = _metadata_coin(scored.claim)
    if explicit:
        return explicit.upper() in targets
    mentioned = _coins_mentioned(
        scored.claim.document.id + " " + scored.claim.document.text
    )
    if not mentioned:
        return True
    return bool(mentioned & targets) and not (mentioned - targets)


def _mentions_coin(scored: KernelScoredClaim, coin: str) -> bool:
    targets = _coin_targets(coin)
    if not targets:
        return False
    explicit = _metadata_coin(scored.claim)
    if explicit:
        return explicit.upper() in targets
    mentioned = _coins_mentioned(
        scored.claim.document.id + " " + scored.claim.document.text
    )
    return bool(mentioned & targets) and not (mentioned - targets)


def evidence_strength(
    supporting: tuple[KernelScoredClaim, ...],
    contrarian: tuple[KernelScoredClaim, ...],
    confidence: float,
    *,
    weights: tuple[tuple[str, float], ...] = DEFAULT_STRENGTH_WEIGHTS,
) -> float:
    """Return the deterministic aggregate confidence-strength signal."""
    if type(supporting) is not tuple or not all(
        type(item) is KernelScoredClaim for item in supporting
    ):
        raise ValueError("supporting must be a tuple of exact KernelScoredClaim values")
    if type(contrarian) is not tuple or not all(
        type(item) is KernelScoredClaim for item in contrarian
    ):
        raise ValueError("contrarian must be a tuple of exact KernelScoredClaim values")
    for field, values in (("supporting", supporting), ("contrarian", contrarian)):
        for index, item in enumerate(values):
            _probability(item.trust, field=f"{field}[{index}].trust")
    confidence_value = _probability(confidence, field="confidence")
    weight_map = _validated_table(
        weights,
        field="strength_weights",
        required=frozenset({"trust", "indep", "diversity", "dominance"}),
    )
    n_indep = len({canonical_source(sc.claim.document.source) for sc in supporting})
    n_kinds = len({sc.claim.document.kind for sc in supporting})
    indep_factor = max(
        0.0,
        min((n_indep - 1) / (INDEPENDENT_SOURCE_SATURATION - 1), 1.0),
    )
    diversity_factor = max(
        0.0,
        min((n_kinds - 1) / (KIND_DIVERSITY_SATURATION - 1), 1.0),
    )
    n_contrarian_sources = len(
        {canonical_source(sc.claim.document.source) for sc in contrarian}
    )
    total_sources = n_indep + n_contrarian_sources
    dominance = (n_indep / total_sources) if total_sources > 0 else 0.0
    strength = (
        weight_map["trust"] * confidence_value
        + weight_map["indep"] * indep_factor
        + weight_map["diversity"] * diversity_factor
        + weight_map["dominance"] * dominance
    )
    return max(0.0, min(1.0, strength))


def _infer_decision_direction(supporting: tuple[KernelScoredClaim, ...]) -> str:
    bullish = 0
    bearish = 0
    for scored in supporting:
        direction = scored.claim.direction
        if direction == "bullish":
            bullish += 1
        elif direction == "bearish":
            bearish += 1
    if bullish == 0 and bearish == 0:
        return "不明"
    if bullish > bearish + 1:
        return "偏多"
    if bearish > bullish + 1:
        return "偏空"
    return "中性"


def _decision_codes(
    *,
    low_calibrated: bool,
    insufficient_sources: bool,
    decision_state: str,
) -> tuple[str, ...]:
    if decision_state == "abstain":
        return tuple(
            reason
            for condition, reason in (
                (low_calibrated, "low_calibrated_confidence"),
                (insufficient_sources, "insufficient_independent_sources"),
            )
            if condition
        )
    if decision_state == "low_confidence":
        return ("below_normal_confidence",)
    return ()


def aggregate_scored_claims(
    scored_claims: tuple[KernelScoredClaim, ...],
    *,
    query: str,
    support_threshold: float = 0.50,
    coin: str = "",
    calibration_model_version: str | object = _UNSET,
    calibration_table: tuple[tuple[float, float], ...] | object = _UNSET,
    resolved_direction: str | object = _UNSET,
    contract_version: str = KERNEL_CONTRACT_VERSION,
) -> KernelOutput:
    """Aggregate scored claims into the versioned kernel result.

    Calibration provenance is fail-closed.  Omitting
    ``calibration_model_version`` is a compatibility path for the built-in
    fixed heuristic only: ``calibration_table`` must also be omitted or equal
    the canonical ``DEFAULT_CALIBRATION_TABLE``.  A custom table requires the
    explicit ``ISOTONIC_VERSION``.  Explicit ``None`` is invalid.  Explicit
    ``FIXED_HEURISTIC_VERSION`` accepts an omitted/canonical default table and
    rejects every custom table.

    Omitting ``resolved_direction`` temporarily preserves legacy deterministic
    inference for existing ``run_kernel`` callers.  An explicitly supplied
    exact string is passed through unchanged.  Issue #453 will own production
    direction routing; this function performs no external callback or I/O work.
    """
    require_supported_contract_version(contract_version)
    if type(scored_claims) is not tuple or not all(
        type(item) is KernelScoredClaim for item in scored_claims
    ):
        raise ValueError(
            "scored_claims must be a tuple of exact KernelScoredClaim values"
        )
    for index, item in enumerate(scored_claims):
        _validate_scored_claim_graph(item, index=index)
    if type(query) is not str:
        raise ValueError("query must be an exact string")
    threshold = _probability(support_threshold, field="support_threshold")
    if type(coin) is not str:
        raise ValueError("coin must be an exact string")
    if resolved_direction is _UNSET:
        output_direction = _infer_decision_direction
    elif type(resolved_direction) is str:
        output_direction = None
    else:
        raise ValueError("resolved_direction must be an exact string")
    if calibration_model_version is _UNSET:
        resolved_calibration_version = FIXED_HEURISTIC_VERSION
        if calibration_table is _UNSET:
            resolved_calibration_input: tuple[tuple[float, float], ...] = ()
        elif type(calibration_table) is tuple:
            _validated_numeric_table(
                calibration_table, field="calibration_table", probability=True
            )
            if calibration_table != DEFAULT_CALIBRATION_TABLE:
                raise ValueError(
                    "calibration model version is required for a custom table"
                )
            resolved_calibration_input = ()
        else:
            raise ValueError("calibration_table must be an exact tuple")
    else:
        resolved_calibration_version = calibration_model_version
        if calibration_table is _UNSET:
            resolved_calibration_input = ()
        elif type(calibration_table) is tuple:
            if calibration_model_version == FIXED_HEURISTIC_VERSION:
                _validated_numeric_table(
                    calibration_table, field="calibration_table", probability=True
                )
                if calibration_table and calibration_table != DEFAULT_CALIBRATION_TABLE:
                    raise ValueError(
                        "fixed calibration model does not accept a custom table"
                    )
                resolved_calibration_input = ()
            else:
                resolved_calibration_input = calibration_table
        else:
            raise ValueError("calibration_table must be an exact tuple")
    resolved_calibration_table = _calibration_table(
        resolved_calibration_version,
        resolved_calibration_input,
    )

    query_tokens = _normalize(query)
    if coin:
        relevant = tuple(
            scored
            for scored in sorted(
                scored_claims,
                key=lambda sc: (0 if _mentions_coin(sc, coin) else 1, -sc.trust),
            )
            if _matches_coin(scored, coin)
        )
    else:
        relevant = (
            tuple(
                scored
                for scored in scored_claims
                if not query_tokens or (_normalize(scored.claim.text) & query_tokens)
            )
            or scored_claims
        )
        relevant = tuple(sorted(relevant, key=lambda sc: sc.trust, reverse=True))

    supporting_all = tuple(sc for sc in relevant if sc.trust >= threshold)
    contrarian_all = tuple(sc for sc in relevant if sc.trust < threshold)
    raw_confidence = (
        sum(sc.trust for sc in supporting_all) / len(supporting_all)
        if supporting_all
        else 0.0
    )
    strength = evidence_strength(
        supporting_all,
        contrarian_all,
        raw_confidence,
    )
    calibrated = interpolate_calibration(strength, resolved_calibration_table)
    supporting = supporting_all[:SUPPORTING_LIMIT]
    contrarian = contrarian_all[:CONTRARIAN_LIMIT]
    independent_sources = len(
        {canonical_source(sc.claim.document.source) for sc in supporting}
    )
    low_calibrated = calibrated < 0.35
    insufficient_sources = independent_sources < 2
    abstain = low_calibrated or insufficient_sources
    decision_state = (
        "abstain" if abstain else "low_confidence" if calibrated < 0.5 else "normal"
    )
    return KernelOutput(
        raw_confidence,
        calibrated,
        abstain,
        _infer_decision_direction(supporting)
        if output_direction is _infer_decision_direction
        else resolved_direction,
        _decision_codes(
            low_calibrated=low_calibrated,
            insufficient_sources=insufficient_sources,
            decision_state=decision_state,
        ),
        len(supporting),
        independent_sources,
        contract_version=contract_version,
        query=query,
        scored_claims=scored_claims,
        supporting=supporting,
        contrarian=contrarian,
        decision_state=decision_state,
    )


def run_kernel(inp: KernelInput) -> KernelOutput:
    """Run the public deterministic kernel entrypoint for one versioned input."""
    if type(inp) is not KernelInput:
        raise ValueError("inp must be an exact KernelInput")
    require_supported_contract_version(inp.contract_version)
    scored_claims = tuple(score_claim(claim, now=inp.pit_epoch) for claim in inp.claims)
    return aggregate_scored_claims(
        scored_claims,
        query=inp.query,
        coin=inp.coin,
        contract_version=inp.contract_version,
    )


def score_claim(
    claim: KernelClaim,
    *,
    now: float,
    weights: tuple[tuple[str, float], ...] = DEFAULT_SCORE_WEIGHTS,
    reputations: tuple[tuple[str, float], ...] = DEFAULT_SOURCE_REPUTATIONS,
    half_lives: tuple[tuple[str, float], ...] = DEFAULT_HALF_LIVES,
    independent_sources: tuple[str, ...] = (),
    dynamic_reputation: float | None = None,
    reputation_trace: KernelReputationTrace | None = None,
    info_flags: tuple[str, ...] = (),
) -> KernelScoredClaim:
    """Score one claim using only resolved, provider-free deterministic values."""
    if type(claim) is not KernelClaim:
        raise ValueError("claim must be an exact KernelClaim")
    now_value = _exact_number(now, field="now")
    weight_map = _validated_table(
        weights,
        field="weights",
        required=frozenset({"src", "corr", "rec", "manip"}),
    )
    reputation_map = _validated_table(
        reputations, field="reputations", probability=True
    )
    half_life_map = _validated_table(half_lives, field="half_lives", positive=True)
    if "default" not in half_life_map:
        raise ValueError("half_lives must contain a default entry")
    if type(info_flags) is not tuple or not all(
        type(flag) is str for flag in info_flags
    ):
        raise ValueError("info_flags must be an exact tuple of exact strings")
    if (
        reputation_trace is not None
        and type(reputation_trace) is not KernelReputationTrace
    ):
        raise ValueError(
            "reputation_trace must be an exact KernelReputationTrace or None"
        )

    metadata: dict[str, object] = {}
    for key, value in claim.document.metadata:
        if key in metadata:
            raise ValueError("claim metadata keys must be unique")
        metadata[key] = value
    verified = metadata.get("verified_onchain")
    if verified is not None and type(verified) is not bool:
        raise ValueError("verified_onchain metadata must be an exact boolean")
    override = metadata.get("reputation")
    if override is not None:
        validated_override = _exact_number(override, field="metadata reputation")
        if not 0.0 <= validated_override <= 1.0:
            raise ValueError("metadata reputation must be between zero and one")
    resolved_dynamic: object = _NO_DYNAMIC_REPUTATION
    if dynamic_reputation is not None:
        resolved_dynamic = _exact_number(dynamic_reputation, field="dynamic_reputation")
        if not 0.0 <= resolved_dynamic <= 1.0:  # type: ignore[operator]
            raise ValueError("dynamic_reputation must be between zero and one")
    reputation = _resolve_source_reputation(
        kind=claim.document.kind,
        metadata=metadata,
        reputations=reputation_map,
        dynamic_value=resolved_dynamic,
    )

    corroboration = corroboration_score(independent_sources)
    half_life = half_life_map.get(claim.document.kind, half_life_map["default"])
    recency = recency_decay(
        timestamp=claim.document.timestamp,
        now=now_value,
        half_life_hours=half_life,
    )
    manipulation = manipulation_penalty(claim.text, claim.document.kind)
    raw = (
        weight_map["src"] * reputation
        + weight_map["corr"] * corroboration
        + weight_map["rec"] * recency
        - weight_map["manip"] * manipulation
    )
    trust = max(0.0, min(1.0, raw))
    return KernelScoredClaim(
        claim=claim,
        trust=trust,
        components=(
            ("reputation", reputation),
            ("corroboration", corroboration),
            ("recency", recency),
            ("manipulation", manipulation),
        ),
        reputation_trace=reputation_trace,
        manip_flags=manipulation_flags(claim.text),
        info_flags=info_flags,
    )


def reputation_floor(
    kind: str, reputations: Mapping[str, float], *, unknown: float = 0.35
) -> float:
    """Return the bounded dynamic-reputation floor for a source kind."""
    return round(0.3 * reputations.get(kind, unknown), 4)


def source_reputation(
    *,
    kind: str,
    source_key: str,
    metadata: Mapping[str, object],
    reputations: Mapping[str, float],
    dynamic: Mapping[str, float] | None = None,
) -> float:
    """Resolve static or dynamic source reputation from plain immutable inputs."""
    prior = _resolve_source_reputation(
        kind=kind,
        metadata=metadata,
        reputations=reputations,
    )
    dynamic_value = prior if dynamic is None else dynamic.get(source_key, prior)
    return _resolve_source_reputation(
        kind=kind,
        metadata=metadata,
        reputations=reputations,
        dynamic_value=dynamic_value,
    )


_NO_DYNAMIC_REPUTATION = object()


def _resolve_source_reputation(
    *,
    kind: str,
    metadata: Mapping[str, object],
    reputations: Mapping[str, float],
    dynamic_value: object = _NO_DYNAMIC_REPUTATION,
) -> float:
    """Canonical legacy-compatible source-reputation resolver."""
    base = reputations.get(kind, 0.5)
    unverified_celebrity = kind == "celebrity_trade" and not metadata.get(
        "verified_onchain", False
    )
    if unverified_celebrity:
        base = reputations.get("social", 0.35)
    override = metadata.get("reputation")
    prior = float(override) if override is not None else base
    if dynamic_value is _NO_DYNAMIC_REPUTATION:
        return prior
    if unverified_celebrity:
        return min(dynamic_value, reputations.get("social", 0.35))  # type: ignore[type-var]
    return dynamic_value  # type: ignore[return-value]


def recency_decay(*, timestamp: float, now: float, half_life_hours: float) -> float:
    """Return exponential recency decay; invalid/unknown time is neutral (0.5)."""
    if not timestamp:
        return 0.5
    if not math.isfinite(timestamp) or not math.isfinite(now):
        return 0.5
    age_hours = (now - timestamp) / 3600.0
    if not math.isfinite(age_hours) or age_hours < 0:
        return 0.5
    return math.pow(0.5, age_hours / half_life_hours)


def stable_sigmoid(value: float, *, clamp: float = 30.0) -> float:
    """Return a numerically stable sigmoid with a bounded exponent."""
    bounded = max(-clamp, min(clamp, value))
    return 1.0 / (1.0 + math.exp(-bounded))


def interpolate_calibration(raw: float, table: Sequence[tuple[float, float]]) -> float:
    """Clamp and linearly interpolate a deterministic calibration table."""
    x = max(0.0, min(1.0, raw))
    if not table:
        return round(x, 4)
    if x <= table[0][0]:
        return table[0][1]
    if x >= table[-1][0]:
        return table[-1][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            ratio = (x - x0) / (x1 - x0)
            return round(y0 + ratio * (y1 - y0), 4)
    return round(x, 4)


__all__ = [
    "CONTRARIAN_LIMIT",
    "DEFAULT_CALIBRATION_TABLE",
    "DEFAULT_HALF_LIVES",
    "DEFAULT_SCORE_WEIGHTS",
    "DEFAULT_SOURCE_REPUTATIONS",
    "DEFAULT_STRENGTH_WEIGHTS",
    "INDEPENDENT_SOURCE_SATURATION",
    "KIND_DIVERSITY_SATURATION",
    "SUPPORTING_LIMIT",
    "aggregate_scored_claims",
    "corroboration_score",
    "evidence_strength",
    "interpolate_calibration",
    "manipulation_flags",
    "manipulation_hits",
    "manipulation_penalty",
    "recency_decay",
    "reputation_floor",
    "score_claim",
    "source_reputation",
    "stable_sigmoid",
]
