#!/usr/bin/env python3
"""Compute a bounded Codex lane count from host CPU and load."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone


def lane_capacity(cpu_count: int, load_1m: float, max_lanes: int, max_load_per_cpu: float) -> int:
    if cpu_count < 1 or max_lanes < 1:
        return 0
    load_budget = cpu_count * max_load_per_cpu
    spare = max(0.0, load_budget - load_1m)
    if load_1m >= cpu_count * 1.5:
        return 0
    return min(max_lanes, max(1, int(spare)))


def load_diagnostics(cpu_count: int, load_1m: float, max_lanes: int, max_load_per_cpu: float) -> dict:
    capacity = lane_capacity(cpu_count, load_1m, max_lanes, max_load_per_cpu)
    load_budget = cpu_count * max_load_per_cpu
    reason = (
        "invalid_capacity_configuration"
        if cpu_count < 1 or max_lanes < 1
        else ("load_at_or_above_hard_limit" if load_1m >= cpu_count * 1.5 else "within_limit")
    )
    diagnostics = {
        "capacity": capacity,
        "cpu_count": cpu_count,
        "load_1m": load_1m,
        "load_budget": load_budget,
        "max_lanes": max_lanes,
        "max_load_per_cpu": max_load_per_cpu,
        "blocked": capacity == 0,
        "reason": reason,
    }
    if capacity == 0:
        diagnostics["denied_reason"] = reason
        diagnostics["retry_at"] = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    return diagnostics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-lanes", type=int, default=4)
    parser.add_argument("--max-load-per-cpu", type=float, default=0.85)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    cpu_count = os.cpu_count() or 1
    try:
        load_1m = os.getloadavg()[0]
    except OSError:
        load_1m = 0.0
    diagnostics = load_diagnostics(cpu_count, load_1m, args.max_lanes, args.max_load_per_cpu)
    print(json.dumps(diagnostics, separators=(",", ":")) if args.json else diagnostics["capacity"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
