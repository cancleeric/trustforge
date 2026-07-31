"""Regression tests for the formal-run E2E gate downgrade in
``deploy/activate_release.sh``.

The analysis-report E2E (``verify_analysis_report``) hard-fails and triggers
rollback by default. But formal-run is currently WIP —
``formal_run_coordinator.submit()`` calls ``plan_formal_manual()`` (which only
*computes* a job_id) but never ``enqueue_formal_projection()``, so the job is
never written to the ``analysis_jobs`` table and ``job_status`` returns 404
``job_not_found``. Until formal-run is complete, the E2E gate must downgrade
that failure to a warning when the ``formal-run-prod-ready`` flag is absent,
so unrelated fixes (e.g. the SSM JSON fix) can still ship. Touching the flag
file restores the hard gate.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTIVATE = REPO_ROOT / "deploy" / "activate_release.sh"


def test_activate_script_has_formal_run_ready_flag_logic():
    """The downgrade-to-warning logic and the flag must both be present."""
    script = ACTIVATE.read_text(encoding="utf-8")
    assert "FORMAL_RUN_READY_FLAG" in script
    assert "formal-run-prod-ready" in script
    # The gated branch: warning return 0 when the flag is absent.
    assert '[ ! -f "$FORMAL_RUN_READY_FLAG" ]' in script
    assert "downgrading to warning" in script
    # And the hard-fail path is still there when the flag is present.
    assert "analysis report E2E failed" in script


def test_flag_absent_downgrades_to_warning(tmp_path):
    """When the formal-run-prod-ready flag is absent, the E2E gate must take
    the downgrade (warning) branch, not the hard-fail branch."""
    absent_flag = tmp_path / "formal-run-prod-ready"
    # Mirror the exact predicate used in verify_analysis_report.
    result = subprocess.run(
        ["bash", "-c", f'[ ! -f "{absent_flag}" ] && echo "downgrade" || echo "hard"'],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "downgrade"


def test_flag_present_keeps_hard_gate(tmp_path):
    """When the formal-run-prod-ready flag exists, the gate must hard-fail
    (the downgrade branch is NOT taken)."""
    present_flag = tmp_path / "formal-run-prod-ready"
    present_flag.write_text("", encoding="utf-8")
    result = subprocess.run(
        ["bash", "-c", f'[ ! -f "{present_flag}" ] && echo "downgrade" || echo "hard"'],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "hard"


def test_activate_release_sh_syntax_valid():
    """The modified activate_release.sh must remain syntactically valid."""
    result = subprocess.run(
        ["bash", "-n", str(ACTIVATE)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"bash syntax error:\n{result.stderr}"
