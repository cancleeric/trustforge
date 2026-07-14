"""Hermes Agent manifest and bounded autonomous-research contract.

This is intentionally data, not a dependency on another agent project.  It
states which tools Hermes may use, which skills constrain it, and the safety
boundary between continuous research and a reproducible formal analysis run.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .execlog import RUNTIME_BUDGET_SEC
from .schema import COIN_POOL
from .skills import run_skill_manifest


@dataclass(frozen=True)
class HermesTool:
    name: str
    purpose: str
    mode: str
    max_calls_per_cycle: int
    writes_execution_log: bool = True


HERMES_SKILLS = (
    {
        "name": "five-year-ohlcv-lineage",
        "rule": "Every price fact carries safe filename, SHA-256, coverage and analysis window.",
    },
    {
        "name": "evidence-contract",
        "rule": "Every conclusion is linked to Evidence source, fetched_at, content_reference and related_claim.",
    },
    {
        "name": "contrarian-evidence",
        "rule": "Contradictory and low-trust evidence remains visible; it is never silently discarded.",
    },
    {
        "name": "report-contract",
        "rule": "A report contains judgment, key basis, calibrated confidence, limits and reversal conditions.",
    },
    {
        "name": "bounded-self-improvement",
        "rule": "Diagnose durable failures and outcomes, propose sandbox experiments, and require human approval before any production change.",
    },
)

HERMES_TOOLS = (
    HermesTool("refresh_sources", "Refresh bounded crawler sources into timestamped cache.", "autonomous", 1),
    HermesTool("archive_source_snapshot", "Persist source documents with published_at, fetched_at and snapshot_at.", "autonomous", 5),
    HermesTool("build_snapshots", "Build one trust snapshot per allowed coin from cache only.", "autonomous", 1),
    HermesTool("cache_freshness_dashboard", "Publish durable cache age, gaps and scheduler-health state.", "autonomous", 1),
    HermesTool("measure_connector_reliability", "Measure per-source failure rate and seven-attempt success gates.", "autonomous", 1),
    HermesTool("measure_quality", "Run bounded offline regression and replay measurements for improvement diagnostics.", "autonomous", 1),
    HermesTool("read_snapshot", "Read a snapshot at or before formal-run start time.", "formal", 5),
    HermesTool("replay_history", "Join point-in-time decisions to later official OHLCV outcomes.", "offline", 5),
    HermesTool("diagnose_improvement", "Turn QA, scheduler, and replay evidence into approval-gated experiments.", "autonomous", 1),
    HermesTool("extract_claims", "Use Bedrock to extract structured claims from selected evidence.", "formal", 1),
    HermesTool("classify_stance", "Use Bedrock for bounded semantic stance classification.", "formal", 1),
    HermesTool("assemble_report", "Use Bedrock only to narrate pipeline-derived findings with citations.", "formal", 1),
    HermesTool("export_deliverables", "Export report, Evidence and JSONL execution log.", "formal", 1),
)


def manifest() -> dict:
    """Return the stable, serializable Hermes agent declaration."""
    return {
        "agent": "hermes",
        "autonomy": {
            "mode": "bounded_scheduled_research",
            "coin_pool": list(COIN_POOL),
            "max_cycle_budget_sec": RUNTIME_BUDGET_SEC,
            "cross_run_memory": "research snapshots only; formal conclusions are run-isolated",
            "formal_run_rule": "select only snapshots and source records at or before run_started_at",
            "no_unbounded_network_access": True,
        },
        "tools": [asdict(tool) for tool in HERMES_TOOLS],
        "skills": list(HERMES_SKILLS),
        "skill_revisions": run_skill_manifest(),
    }


def autonomous_cycle_plan(coins: tuple[str, ...] | list[str] | None = None) -> dict:
    """Produce the bounded action plan used by cron/systemd, without side effects."""
    selected = list(coins or COIN_POOL)
    invalid = sorted(set(selected) - set(COIN_POOL))
    if invalid:
        raise ValueError(f"unsupported coins: {invalid}")
    return {
        "agent": "hermes",
        "budget_sec": RUNTIME_BUDGET_SEC,
        "coins": selected,
        "actions": [
            {"tool": "refresh_sources", "argv": ["scripts/fetch_scheduler.py", "--parallelism", "4", "--total-budget-sec", str(RUNTIME_BUDGET_SEC), *sum((["--coin", coin] for coin in selected), [])]},
            {"tool": "build_snapshots", "argv": ["scripts/fetch_scheduler.py", "--snapshot", *sum((["--coin", coin] for coin in selected), [])]},
            {"tool": "cache_freshness_dashboard", "argv": ["scripts/cache_freshness_dashboard.py"]},
            {"tool": "measure_connector_reliability", "argv": ["scripts/connector_reliability_report.py"]},
            {"tool": "measure_quality", "argv": ["scripts/run_question_bank.py", "--limit", "24", "--out", "out/question-bank-latest.json"]},
            *[
                {"tool": "replay_history", "argv": ["scripts/run_historical_replay.py", "--coin", coin, "--out", f"out/historical-replay-{coin.lower()}.json"]}
                for coin in selected
            ],
            {"tool": "diagnose_improvement", "argv": ["scripts/diagnose_hermes.py"]},
        ],
        "formal_run_boundary": "A formal run reads only source/snapshot records at or before its run_started_at.",
    }
