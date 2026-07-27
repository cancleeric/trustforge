#!/usr/bin/env python3
"""Check kernel canary health.

Usage: python scripts/kernel_canary_health.py
Exit 0 = healthy, 1 = unhealthy (for monitoring integration).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone


def _try_import_shadow_diagnostics() -> dict | None:
    try:
        from trustforge.agent.shadow import shadow_diagnostics
        return shadow_diagnostics()
    except ImportError:
        return None


def main() -> int:
    now = datetime.now(timezone.utc).isoformat()
    report = {
        "timestamp": now,
        "canary_ratio": float(os.getenv("KERNEL_CANARY_RATIO", "0")),
        "checks": {},
    }

    # Check 1: Canary ratio is set
    ratio = report["canary_ratio"]
    report["checks"]["canary_ratio_set"] = {
        "passed": ratio > 0,
        "value": ratio,
    }

    # Check 2: Shadow parity health (optional — module may not exist yet)
    parity = _try_import_shadow_diagnostics()
    if parity is not None:
        report["checks"]["shadow_parity"] = {
            "passed": parity.get("parity_rate", 0) >= 0.85,
            "details": parity,
        }
    else:
        report["checks"]["shadow_parity"] = {
            "passed": True,
            "note": "shadow module not available (expected before #732 merge)",
        }

    # Check 3: Canary window state
    if ratio > 0 and ratio < 1:
        report["checks"]["canary_window"] = {
            "passed": True,
            "note": "canary mode active — check canary diagnostics for error rate",
        }
    elif ratio >= 1:
        report["checks"]["canary_window"] = {
            "passed": True,
            "note": "full kernel promotion active",
        }
    else:
        report["checks"]["canary_window"] = {
            "passed": True,
            "note": "canary not active (ratio=0)",
        }

    print(json.dumps(report, indent=2, ensure_ascii=False))
    all_passed = all(c["passed"] for c in report["checks"].values())
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
