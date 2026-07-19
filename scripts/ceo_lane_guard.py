#!/usr/bin/env python3
"""Compute a bounded Codex lane count from host CPU and load."""
from __future__ import annotations

import argparse
import os


def lane_capacity(cpu_count: int, load_1m: float, max_lanes: int, max_load_per_cpu: float) -> int:
    if cpu_count < 1 or max_lanes < 1:
        return 0
    load_budget = cpu_count * max_load_per_cpu
    spare = max(0.0, load_budget - load_1m)
    if load_1m >= cpu_count * 1.5:
        return 0
    return min(max_lanes, max(1, int(spare)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-lanes", type=int, default=4)
    parser.add_argument("--max-load-per-cpu", type=float, default=0.85)
    args = parser.parse_args()
    cpu_count = os.cpu_count() or 1
    try:
        load_1m = os.getloadavg()[0]
    except OSError:
        load_1m = 0.0
    print(lane_capacity(cpu_count, load_1m, args.max_lanes, args.max_load_per_cpu))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
