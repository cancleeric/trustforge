#!/usr/bin/env python3
"""Generate or verify the versioned TrustForge core data-contract artifact."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trustforge.data_contracts import contract_schemas  # noqa: E402

ARTIFACT = ROOT / "docs" / "contracts" / "trustforge-data-contracts-v1.json"


def rendered_contracts() -> str:
    return json.dumps(contract_schemas(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="update the committed artifact")
    args = parser.parse_args()
    expected = rendered_contracts()
    if args.write:
        ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
        ARTIFACT.write_text(expected, encoding="utf-8")
        print(f"wrote {ARTIFACT.relative_to(ROOT)}")
        return 0
    if not ARTIFACT.exists() or ARTIFACT.read_text(encoding="utf-8") != expected:
        print("data contract artifact is stale; run scripts/check_data_contracts.py --write")
        return 1
    print("data contract artifact is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
