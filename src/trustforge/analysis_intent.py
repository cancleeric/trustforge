"""DB-free open-intent compiler and answer-coverage contracts.

The public question is compiled into typed operations.  LLM output is treated as
untrusted input: it may propose a plan, but this module's closed registry and
validation rules decide what can execute.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, Mapping
import re


class IntentValidationError(ValueError):
    """The proposed intent cannot be executed safely or truthfully."""


@dataclass(frozen=True)
class Capability:
    name: str
    output: str
    allowed_targets: frozenset[str]
    min_targets: int = 0
    max_targets: int | None = None
    dependencies: tuple[str, ...] = ()
    requires_llm: bool = False
    incurs_external_cost: bool = False


CAPABILITY_REGISTRY: dict[str, Capability] = {
    "sentiment_analysis": Capability(
        name="sentiment_analysis",
        output="sentiment",
        allowed_targets=frozenset({"news", "social"}),
        min_targets=1,
        max_targets=1,
    ),
    "compare": Capability(
        name="compare",
        output="alignment",
        allowed_targets=frozenset({"news_sentiment", "social_sentiment", "asset"}),
        min_targets=2,
    ),
    "freshness_assessment": Capability(
        name="freshness_assessment",
        output="freshness",
        allowed_targets=frozenset({"news", "social", "price", "onchain", "regulatory"}),
        min_targets=1,
    ),
    "manipulation_risk": Capability(
        name="manipulation_risk",
        output="manipulation_risk",
        allowed_targets=frozenset({"news", "social"}),
        min_targets=1,
    ),
    "market_synthesis": Capability(
        name="market_synthesis",
        output="market_summary",
        allowed_targets=frozenset({"price", "onchain", "news", "social", "regulatory"}),
    ),
    "hypothesis_test": Capability(
        name="hypothesis_test",
        output="hypothesis_verdict",
        allowed_targets=frozenset({"evidence"}),
        min_targets=1,
    ),
}


@dataclass(frozen=True)
class IntentOperation:
    id: str
    type: str
    targets: tuple[str, ...]
    output: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnalysisIntent:
    assets: tuple[str, ...]
    operations: tuple[IntentOperation, ...]
    deliverables: tuple[str, ...]
    time_window: str | None = None
    matched_official_template: str | None = None
    parse_confidence: float = 1.0
    parse_mode: str = "deterministic"
    unsupported_reasons: tuple[str, ...] = ()

    @property
    def supported(self) -> bool:
        return not self.unsupported_reasons

    def to_dict(self) -> dict:
        return asdict(self) | {"supported": self.supported}


@dataclass(frozen=True)
class CoverageItem:
    deliverable: str
    status: str
    reason: str = ""
    evidence_claim_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnswerCoverage:
    items: tuple[CoverageItem, ...]

    @property
    def status(self) -> str:
        statuses = {item.status for item in self.items}
        if statuses == {"answered"}:
            return "complete"
        if "failed" in statuses:
            return "failed"
        if "unsupported" in statuses:
            return "unsupported" if statuses == {"unsupported"} else "partial"
        return "partial"

    def to_dict(self) -> dict:
        return {"status": self.status, "items": [asdict(item) for item in self.items]}


_SAFE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ASSET = re.compile(r"^[A-Z0-9]{2,12}$")
_VALID_COVERAGE_STATUSES = {"answered", "insufficient_data", "unsupported", "failed"}


def validate_intent(intent: AnalysisIntent) -> AnalysisIntent:
    if not intent.assets or any(not _ASSET.fullmatch(asset) for asset in intent.assets):
        raise IntentValidationError("assets must contain canonical uppercase symbols")
    if not intent.operations:
        raise IntentValidationError("operations must not be empty")
    if not intent.deliverables or len(set(intent.deliverables)) != len(intent.deliverables):
        raise IntentValidationError("deliverables must be non-empty and unique")
    if not 0.0 <= intent.parse_confidence <= 1.0:
        raise IntentValidationError("parse_confidence must be between 0 and 1")

    seen: set[str] = set()
    outputs: set[str] = set()
    for operation in intent.operations:
        if not _SAFE_ID.fullmatch(operation.id) or operation.id in seen:
            raise IntentValidationError(f"invalid or duplicate operation id: {operation.id!r}")
        capability = CAPABILITY_REGISTRY.get(operation.type)
        if capability is None:
            raise IntentValidationError(f"unknown capability: {operation.type!r}")
        if not _SAFE_ID.fullmatch(operation.output):
            raise IntentValidationError(f"operation {operation.id!r} has an invalid output")
        if operation.output != capability.output and not operation.output.startswith(
            capability.output + "_"
        ):
            raise IntentValidationError(
                f"operation {operation.id!r} output is not owned by {operation.type!r}"
            )
        targets = set(operation.targets)
        if len(targets) != len(operation.targets):
            raise IntentValidationError(f"operation {operation.id!r} has duplicate targets")
        if not targets.issubset(capability.allowed_targets):
            raise IntentValidationError(
                f"operation {operation.id!r} contains unsupported targets"
            )
        if len(operation.targets) < capability.min_targets:
            raise IntentValidationError(f"operation {operation.id!r} has too few targets")
        if capability.max_targets is not None and len(operation.targets) > capability.max_targets:
            raise IntentValidationError(f"operation {operation.id!r} has too many targets")
        if any(dependency not in seen for dependency in operation.depends_on):
            raise IntentValidationError(
                f"operation {operation.id!r} has an unknown or forward dependency"
            )
        seen.add(operation.id)
        outputs.add(operation.output)

    if not set(intent.deliverables).issubset(outputs):
        raise IntentValidationError("every deliverable must be produced by the operation plan")
    return intent


def evaluate_answer_coverage(
    intent: AnalysisIntent,
    results: Mapping[str, Mapping[str, object]],
) -> AnswerCoverage:
    """Evaluate requested outputs without turning missing data into fake success."""
    items: list[CoverageItem] = []
    for deliverable in intent.deliverables:
        result = results.get(deliverable)
        if result is None:
            items.append(CoverageItem(deliverable, "failed", "missing_result"))
            continue
        status = result.get("status")
        if status not in _VALID_COVERAGE_STATUSES:
            items.append(CoverageItem(deliverable, "failed", "invalid_result_status"))
            continue
        claim_ids = result.get("evidence_claim_ids", ())
        if not isinstance(claim_ids, (list, tuple)) or any(
            not isinstance(value, str) for value in claim_ids
        ):
            items.append(CoverageItem(deliverable, "failed", "invalid_evidence_binding"))
            continue
        if status == "answered" and not claim_ids:
            items.append(CoverageItem(deliverable, "failed", "answered_without_evidence"))
            continue
        items.append(
            CoverageItem(
                deliverable=deliverable,
                status=str(status),
                reason=str(result.get("reason", "")),
                evidence_claim_ids=tuple(claim_ids),
            )
        )
    return AnswerCoverage(tuple(items))


IntentParser = Callable[[str, tuple[str, ...]], Mapping[str, object]]


def _news_social_intent(
    assets: tuple[str, ...],
    *,
    include_freshness: bool,
    include_manipulation_risk: bool,
    parse_mode: str,
) -> AnalysisIntent:
    operations: list[IntentOperation] = [
        IntentOperation("news_sentiment", "sentiment_analysis", ("news",), "sentiment_news"),
        IntentOperation("social_sentiment", "sentiment_analysis", ("social",), "sentiment_social"),
        IntentOperation(
            "sentiment_alignment",
            "compare",
            ("news_sentiment", "social_sentiment"),
            "alignment",
            ("news_sentiment", "social_sentiment"),
        ),
    ]
    deliverables = ["sentiment_news", "sentiment_social", "alignment"]
    if include_freshness:
        operations.append(IntentOperation(
            "source_freshness",
            "freshness_assessment",
            ("news", "social"),
            "freshness",
        ))
        deliverables.append("freshness")
    if include_manipulation_risk:
        operations.append(IntentOperation(
            "source_manipulation_risk",
            "manipulation_risk",
            ("news", "social"),
            "manipulation_risk",
        ))
        deliverables.append("manipulation_risk")
    return AnalysisIntent(
        assets=assets,
        operations=tuple(operations),
        deliverables=tuple(deliverables),
        matched_official_template="multi_source",
        parse_confidence=0.98,
        parse_mode=parse_mode,
    )


def deterministic_compile(question: str, assets: Iterable[str]) -> AnalysisIntent:
    """Fail-safe parser for known compositional structures; never calls an LLM."""
    canonical_assets = tuple(dict.fromkeys(asset.strip().upper() for asset in assets if asset.strip()))
    lowered = question.casefold()
    has_news = "新聞" in question or "news" in lowered
    has_social = "社群" in question or "social" in lowered
    comparison_words = ("比對", "比較", "是否一致", "差異", "背離", "compare")
    if has_news and has_social and any(word in lowered for word in comparison_words):
        freshness_words = ("時效", "新鮮", "最新", "資料年齡", "freshness", "recency")
        manipulation_words = ("操弄", "操縱", "水軍", "造假", "manipulation")
        return validate_intent(
            _news_social_intent(
                canonical_assets,
                include_freshness=any(word in lowered for word in freshness_words),
                include_manipulation_risk=any(
                    word in lowered for word in manipulation_words
                ),
                parse_mode="deterministic",
            )
        )

    if any(word in question for word in ("假設", "觀點", "支持與反對", "維持盤整")):
        intent = AnalysisIntent(
            assets=canonical_assets,
            operations=(
                IntentOperation("hypothesis", "hypothesis_test", ("evidence",), "hypothesis_verdict"),
            ),
            deliverables=("hypothesis_verdict",),
            matched_official_template="hypothesis",
            parse_confidence=0.85,
        )
        return validate_intent(intent)

    targets = tuple(
        target
        for target, tokens in {
            "price": ("價格", "price"),
            "onchain": ("鏈上", "on-chain", "onchain"),
            "news": ("新聞", "news"),
            "social": ("社群", "social"),
            "regulatory": ("監管", "regulatory"),
        }.items()
        if any(token in lowered for token in tokens)
    )
    intent = AnalysisIntent(
        assets=canonical_assets,
        operations=(
            IntentOperation("market", "market_synthesis", targets, "market_summary"),
        ),
        deliverables=("market_summary",),
        matched_official_template="multi_source" if targets else None,
        parse_confidence=0.7 if targets else 0.45,
    )
    return validate_intent(intent)


def compile_analysis_intent(
    question: str,
    assets: Iterable[str],
    *,
    llm_parser: IntentParser | None = None,
) -> AnalysisIntent:
    """Compile with an optional untrusted LLM parser and deterministic fallback.

    Phase A intentionally does not ship a production Bedrock implementation.
    Callers may inject a parser; malformed/unsafe output falls back rather than
    gaining execution authority.
    """
    canonical_assets = tuple(dict.fromkeys(asset.strip().upper() for asset in assets if asset.strip()))
    if llm_parser is None:
        return deterministic_compile(question, canonical_assets)
    try:
        proposed = llm_parser(question, canonical_assets)
        operations = tuple(
            IntentOperation(
                id=str(item["id"]),
                type=str(item["type"]),
                targets=tuple(str(target) for target in item.get("targets", ())),
                output=str(item["output"]),
                depends_on=tuple(str(dep) for dep in item.get("depends_on", ())),
            )
            for item in proposed["operations"]
        )
        intent = AnalysisIntent(
            assets=tuple(str(asset).upper() for asset in proposed.get("assets", canonical_assets)),
            operations=operations,
            deliverables=tuple(str(item) for item in proposed["deliverables"]),
            time_window=(str(proposed["time_window"]) if proposed.get("time_window") else None),
            matched_official_template=(
                str(proposed["matched_official_template"])
                if proposed.get("matched_official_template")
                else None
            ),
            parse_confidence=float(proposed.get("parse_confidence", 0.5)),
            parse_mode="llm",
        )
        if intent.assets != canonical_assets:
            raise IntentValidationError("LLM parser cannot replace caller-authorized assets")
        return validate_intent(intent)
    except (IntentValidationError, KeyError, TypeError, ValueError):
        fallback = deterministic_compile(question, canonical_assets)
        return AnalysisIntent(**{**fallback.__dict__, "parse_mode": "deterministic_fallback"})
