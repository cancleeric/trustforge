"""Subprocess guard against reintroducing the misleading AUC proxy."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "conformal_on_samples.py"


def test_conformal_cli_does_not_report_or_promote_on_auc_proxy(tmp_path: Path):
    samples_path = tmp_path / "samples.jsonl"
    report_path = tmp_path / "report.json"
    samples = [
        {
            "sample_id": f"sample-{index}",
            "as_of": f"2026-07-{(index % 27) + 1:02d}T00:00:00Z",
            "outcome_observed_at": (
                f"2026-07-{(index % 27) + 2:02d}T00:00:00Z"
            ),
            "coin": "BTC",
            "source_family": "sentiment" if index % 2 else "onchain",
            "evidence_strength": 0.8 if index % 2 else 0.2,
            "claim_direction": "bullish" if index % 3 else "bearish",
            "outcome_direction": "bullish",
        }
        for index in range(60)
    ]
    samples_path.write_text(
        "".join(json.dumps(sample) + "\n" for sample in samples),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--samples",
            str(samples_path),
            "--out",
            str(report_path),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report_text = report_path.read_text(encoding="utf-8")
    assert "auc_proxy" not in report_text
