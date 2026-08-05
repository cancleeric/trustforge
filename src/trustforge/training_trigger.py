"""Fail-closed scheduled training trigger orchestration.

This module intentionally stops at candidate proposal generation. It never
activates a proposal or changes the human approval flags returned by the
training backends.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .sagemaker_submit import COIN_POOL

Provider = Literal["modelhub", "sagemaker"]
Submitter = Callable[..., Mapping[str, Any]]

TERMINAL_STATUSES = (
    "candidate",
    "dry_run",
    "blocked",
    "no_improvement",
    "unavailable",
    "timeout",
    "error",
    "skipped",
)
FAILURE_STATUSES = {"error", "timeout", "unavailable"}
NON_FATAL_STATUSES = {"blocked", "no_improvement", "skipped"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manual_approval_preserved(result: Mapping[str, Any]) -> bool:
    return (
        result.get("automatic_apply") is False
        and result.get("requires_human_approval") is True
    )


def _summary_result(
    *,
    provider: Provider,
    coin: str,
    status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "coin": coin,
        "status": status,
        "reason": reason,
        "automatic_apply": False,
        "requires_human_approval": True,
    }


def _submit_one(
    *,
    provider: Provider,
    coin: str,
    training_dir: Path,
    out_dir: Path,
    dry_run: bool,
    req_no_map: Mapping[str, str],
    modelhub_submitter: Submitter,
    sagemaker_submitter: Submitter,
) -> dict[str, Any]:
    try:
        if provider == "modelhub":
            req_no = req_no_map.get(coin)
            if not dry_run and not req_no:
                return _summary_result(
                    provider=provider,
                    coin=coin,
                    status="blocked",
                    reason="missing ModelHub req_no for live trigger",
                )
            raw = dict(
                modelhub_submitter(
                    coin,
                    training_dir=training_dir,
                    out_dir=out_dir,
                    req_no=req_no,
                    dry_run=dry_run,
                )
            )
        else:
            raw = dict(
                sagemaker_submitter(
                    coin,
                    training_dir=training_dir,
                    out_dir=out_dir,
                    dry_run=dry_run,
                )
            )
    except Exception as exc:
        return _summary_result(
            provider=provider,
            coin=coin,
            status="error",
            reason=f"{type(exc).__name__}: {exc}",
        )

    raw.setdefault("coin", coin)
    raw["provider"] = provider
    status = str(raw.get("status", "error"))
    if status not in TERMINAL_STATUSES:
        raw["status"] = "error"
        raw["reason"] = f"unknown training status: {status}"
    if not _manual_approval_preserved(raw):
        raw["status"] = "error"
        raw["reason"] = "training result did not preserve manual approval governance"
        raw["automatic_apply"] = False
        raw["requires_human_approval"] = True
    return raw


def parse_req_no_map(items: Sequence[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"invalid req-no-map item: {item}")
        coin, req_no = item.split("=", 1)
        coin = coin.strip().upper()
        req_no = req_no.strip()
        if coin not in COIN_POOL or not req_no:
            raise ValueError(f"invalid req-no-map item: {item}")
        mapping[coin] = req_no
    return mapping


def run_training_trigger(
    *,
    provider: Provider,
    coins: Sequence[str] = COIN_POOL,
    training_dir: Path = Path("data/training"),
    out_dir: Path | None = None,
    dry_run: bool = True,
    enable_live: bool = False,
    req_no_map: Mapping[str, str] | None = None,
    modelhub_submitter: Submitter | None = None,
    sagemaker_submitter: Submitter | None = None,
) -> dict[str, Any]:
    """Run a bounded batch trigger and return an auditable JSON report."""

    normalized = [coin.upper() for coin in coins]
    invalid = [coin for coin in normalized if coin not in COIN_POOL]
    if invalid:
        raise ValueError(f"unsupported coins: {', '.join(invalid)}")

    if not dry_run:
        env_enabled = os.getenv("TRUSTFORGE_TRAINING_TRIGGER_ENABLED") == "1"
        if not enable_live or not env_enabled:
            return {
                "schema_version": 1,
                "provider": provider,
                "generated_at": _utc_now(),
                "dry_run": False,
                "enabled": False,
                "status": "blocked",
                "reason": (
                    "live training trigger requires --enable-live and "
                    "TRUSTFORGE_TRAINING_TRIGGER_ENABLED=1"
                ),
                "automatic_apply": False,
                "requires_human_approval": True,
                "results": [
                    _summary_result(
                        provider=provider,
                        coin=coin,
                        status="blocked",
                        reason="live training trigger disabled",
                    )
                    for coin in normalized
                ],
                "summary": {"blocked": len(normalized)},
            }

    if modelhub_submitter is None:
        from .modelhub_submit import submit_calibrator_training as modelhub_submitter
    if sagemaker_submitter is None:
        from .sagemaker_submit import submit_sagemaker_training as sagemaker_submitter

    resolved_out_dir = out_dir or Path(f"out/{provider}-scheduled-proposals")
    results = [
        _submit_one(
            provider=provider,
            coin=coin,
            training_dir=training_dir,
            out_dir=resolved_out_dir,
            dry_run=dry_run,
            req_no_map=req_no_map or {},
            modelhub_submitter=modelhub_submitter,
            sagemaker_submitter=sagemaker_submitter,
        )
        for coin in normalized
    ]

    summary: dict[str, int] = {}
    for result in results:
        status = str(result.get("status", "error"))
        summary[status] = summary.get(status, 0) + 1

    overall = "ok"
    if any(result.get("status") in FAILURE_STATUSES for result in results):
        overall = "error"
    elif all(result.get("status") in NON_FATAL_STATUSES for result in results):
        overall = "no_action"

    return {
        "schema_version": 1,
        "provider": provider,
        "generated_at": _utc_now(),
        "dry_run": dry_run,
        "enabled": dry_run or enable_live,
        "status": overall,
        "automatic_apply": False,
        "requires_human_approval": True,
        "results": results,
        "summary": summary,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run scheduled TrustForge training triggers")
    parser.add_argument("--provider", choices=("modelhub", "sagemaker"), required=True)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--coin", choices=COIN_POOL)
    target.add_argument("--all", action="store_true")
    parser.add_argument("--training-dir", type=Path, default=Path("data/training"))
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--live", action="store_false", dest="dry_run")
    parser.add_argument("--enable-live", action="store_true")
    parser.add_argument("--req-no-map", action="append", default=[], metavar="COIN=REQ")
    return parser


def exit_code(report: Mapping[str, Any]) -> int:
    if report.get("status") == "error":
        return 1
    if report.get("status") in {"blocked", "no_action"}:
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        req_no_map = parse_req_no_map(args.req_no_map)
        coins = COIN_POOL if args.all or not args.coin else (args.coin,)
        report = run_training_trigger(
            provider=args.provider,
            coins=coins,
            training_dir=args.training_dir,
            out_dir=args.out_dir,
            dry_run=args.dry_run,
            enable_live=args.enable_live,
            req_no_map=req_no_map,
        )
    except ValueError as exc:
        report = {
            "schema_version": 1,
            "status": "error",
            "reason": str(exc),
            "automatic_apply": False,
            "requires_human_approval": True,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
