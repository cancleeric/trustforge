#!/usr/bin/env python3
"""Run original competition prompt-bank cases and report quality/latency gaps.

Defaults to a bounded offline smoke run.  Use --all for the full 240-case run;
--online is explicit because it can consume provider quota and crawler time.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.pipeline import run, run_comparison
from trustforge.question_bank import QuestionCase, all_cases


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, round((len(values) - 1) * percentile))]


def _validate(report, evidence, log) -> list[str]:
    gaps: list[str] = []
    if not report.market_judgment or not report.key_basis or not report.limits or not report.could_flip:
        gaps.append("report_required_fields")
    required_evidence = {"source", "fetched_at", "content_reference", "related_claim"}
    if not evidence or any(not required_evidence <= set(item.to_dict()) for item in evidence):
        gaps.append("evidence_required_fields")
    source_events = [event for event in log.events if event["tool"] == "ingestion.source"]
    if not source_events:
        gaps.append("source_execution_events")
    else:
        required_source_fields = {"source", "kind", "duration_ms", "document_count", "outcome"}
        if any(not required_source_fields <= set(event.get("params", {})) for event in source_events):
            gaps.append("source_execution_event_contract")
        if any(event["params"].get("outcome") not in {"ok", "empty", "failed"} for event in source_events):
            gaps.append("source_execution_event_outcome")
    if log.elapsed() >= 900:
        gaps.append("15_minute_budget")
    return gaps


def _run_case(case: QuestionCase, offline: bool) -> tuple[dict, list[dict]]:
    started = time.perf_counter()
    if case.question_type.value == "comparison":
        report_a, evidence_a, report_b, evidence_b, log = run_comparison(
            case.coin_a, case.coin_b, case.query, offline=offline,
            run_scope_id=f"question-bank-{case.id}-{time.time_ns()}",
        )
        gaps = _validate(report_a, evidence_a, log) + _validate(report_b, evidence_b, log)
        evidence_count = len(evidence_a) + len(evidence_b)
    else:
        report, evidence, log = run(case.coin or "BTC", case.query, case.question_type, offline=offline,
            run_scope_id=f"question-bank-{case.id}-{time.time_ns()}")
        gaps = _validate(report, evidence, log)
        evidence_count = len(evidence)
    source_events = [event for event in log.events if event["tool"] == "ingestion.source"]
    return ({
        "id": case.id, "type": case.question_type.value, "coins": case.coin or f"{case.coin_a}/{case.coin_b}",
        "elapsed_sec": round(time.perf_counter() - started, 4), "pipeline_elapsed_sec": log.elapsed(),
        "evidence_count": evidence_count, "gaps": sorted(set(gaps)), "pass": not gaps,
    }, source_events)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="run all 240 generated cases")
    parser.add_argument("--limit", type=int, default=24, help="bounded case count unless --all")
    parser.add_argument("--online", action="store_true", help="explicitly use cache/live and provider settings")
    parser.add_argument("--out", type=Path, default=REPO / "docs" / "qa" / "QUESTION-BANK-RESULTS.json")
    args = parser.parse_args()
    cases = all_cases() if args.all else all_cases()[:args.limit]
    results: list[dict] = []
    source_samples: dict[str, list[float]] = defaultdict(list)
    source_outcomes: Counter[str] = Counter()
    for index, case in enumerate(cases, start=1):
        try:
            result, events = _run_case(case, offline=not args.online)
        except Exception as exc:
            result, events = ({"id": case.id, "type": case.question_type.value, "pass": False, "gaps": [f"exception:{type(exc).__name__}"], "elapsed_sec": 0.0}, [])
        results.append(result)
        for event in events:
            params = event["params"]
            source = str(params.get("source", "unknown"))
            source_samples[source].append(float(params.get("duration_ms", 0.0)))
            source_outcomes[f"{source}:{params.get('outcome', 'unknown')}"] += 1
        print(f"[{index}/{len(cases)}] {case.id}: {'PASS' if result['pass'] else ', '.join(result['gaps'])}")
    summary = {
        "mode": "online" if args.online else "offline",
        "case_count": len(results), "passed": sum(item["pass"] for item in results),
        "failed": sum(not item["pass"] for item in results),
        "latency_sec": {"p50": _percentile([item["elapsed_sec"] for item in results], .5), "p95": _percentile([item["elapsed_sec"] for item in results], .95)},
        "source_latency_ms": {name: {"p50": _percentile(values, .5), "p95": _percentile(values, .95), "samples": len(values)} for name, values in sorted(source_samples.items())},
        "source_outcomes": dict(sorted(source_outcomes.items())), "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("case_count", "passed", "failed", "latency_sec", "source_latency_ms")}, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
