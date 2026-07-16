#!/usr/bin/env python3
"""Create a non-networked ModelHub training package from replay outcome labels."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.modelhub_training import build_calibrator_training_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, nargs="+", required=True, help="one or more label JSON artifacts")
    parser.add_argument("--horizon", type=int, choices=(1, 7, 14), default=1)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    documents = []
    for path in args.labels:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            documents.extend(payload)
        elif isinstance(payload, dict):
            documents.append(payload)
        else:
            raise ValueError(f"{path} must contain a label document or a list of label documents")
    package = build_calibrator_training_package(documents, horizon=args.horizon)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": package["status"], "rows": package["dataset"]["row_count"], "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
