"""Read-only Hermes upgrade control-plane projection.

The ship is a UI metaphor over versioned artifacts, never a second source of
truth.  Core controls are packaged and hashed for visibility but cannot be
staged through the outer-skill mechanism.  Outer modules expose only recorded
baseline/approved/staged state and diagnostic proposals.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .skill_changes import change_history
from .skills import run_skill_manifest


MODULES = (
    ("scan", "掃描陣列", "艦艏 · BOW ARRAY", "source", ("data-acquisition",)),
    ("filter", "過濾矩陣", "前甲板 · FORE DECK", "evaluation", ("question-retrieval",)),
    ("core", "信任核心", "艦體中央 · TRUST CORE", None, ()),
    ("verify", "驗證核心", "艦橋塔 · BRIDGE", "analysis", ("analysis-orchestration", "historical-calibration")),
    ("detect", "偵測砲塔", "上甲板 · TURRET", "improvement", ("execution-efficiency",)),
    ("engine", "報告引擎", "艦尾 · MAIN ENGINE", "report", ("report-evidence-log",)),
)


def _root() -> Path:
    return Path(os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[2])))


def _core_hash() -> str:
    digest = hashlib.sha256()
    for relative in ("src/trustforge/trust/scoring.py", "src/trustforge/skills.py", "src/trustforge/analysis_flow.py"):
        path = _root() / relative
        digest.update(relative.encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _diagnostic() -> dict[str, Any]:
    path = Path(os.getenv("TRUSTFORGE_IMPROVEMENT_REPORT", str(_root() / "out" / "hermes-improvement-latest.json")))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "not_generated", "generated_at": None, "proposals": []}
    return value if isinstance(value, dict) else {"status": "invalid", "generated_at": None, "proposals": []}


def upgrade_status() -> dict[str, Any]:
    """Project versioned core/outer modules and approval-gated candidates."""
    manifest = run_skill_manifest()
    outer = {row["family"]: row for row in manifest["outer_skills"]}
    history = change_history()
    diagnostic = _diagnostic()
    proposals = diagnostic.get("proposals") if isinstance(diagnostic.get("proposals"), list) else []
    modules = []
    for module_id, name, slot, family, areas in MODULES:
        related = [p for p in proposals if isinstance(p, dict) and p.get("area") in areas]
        if family is None:
            revision = _core_hash()
            origin = "packaged-core"
            state = "locked"
            history_rows: list[dict[str, Any]] = []
        else:
            resolved = outer[family]
            revision = str(resolved["revision"])
            origin = str(resolved["origin"])
            history_rows = [r for r in history if r.get("skill_id") == f"outer-{family}"][-8:]
            staged = any(r.get("action") == "staged" and r.get("skill_hash") != revision for r in history_rows)
            state = "candidate" if staged or related else "active"
        modules.append({
            "id": module_id, "name": name, "slot": slot, "family": family or "trust-core",
            "revision": revision, "version": revision[:8], "origin": origin, "state": state,
            "recursive_upgrade": False, "automatic_apply": False,
            "proposals": related, "history": history_rows,
        })
    return {
        "agent": "hermes", "kind": "upgrade_control_plane", "metaphor": "modular_flagship",
        "core_policy": "packaged, versioned, immutable during runs; upgrade only by reviewed release",
        "outer_policy": "diagnose -> sandbox -> validation -> human approval -> activate pointer -> rollback",
        "recursive_upgrade": False, "diagnostic": {
            "status": diagnostic.get("status"), "generated_at": diagnostic.get("generated_at"),
            "proposal_count": len(proposals),
        }, "modules": modules,
    }
