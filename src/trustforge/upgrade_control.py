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
    # id, label, plane, upgrade channel, outer family, implementation paths, proposal areas
    ("connectors", "來源連接器", "DATA PLANE", "sandbox-policy", "source", ("src/trustforge/ingestion",), ("data-acquisition", "execution-efficiency")),
    ("snapshots", "不可變快照", "DATA PLANE", "reviewed-release", None, ("src/trustforge/analysis_flow.py", "src/trustforge/replay.py"), ()),
    ("scheduler", "持續排程與佇列", "DATA PLANE", "sandbox-policy", "improvement", ("src/trustforge/analysis_flow.py",), ("analysis-orchestration",)),
    ("question-rag", "題目 RAG 與對話記憶", "INTELLIGENCE", "reviewed-release", None, ("src/trustforge/analysis_flow.py",), ("question-retrieval",)),
    ("claim-extraction", "主張抽取", "INTELLIGENCE", "sandbox-policy", "analysis", ("src/trustforge/agent/orchestrator.py",), ()),
    ("analysis-policy", "分析策略", "INTELLIGENCE", "sandbox-policy", "analysis", ("src/trustforge/skills.py",), ("historical-calibration",)),
    ("model-routing", "模型與校準器", "INTELLIGENCE", "model-gate", None, ("src/trustforge/modelhub_training.py", "src/trustforge/calibrator_gate.py"), ("historical-calibration",)),
    ("time-boundary", "時間邊界", "TRUST KERNEL", "core-release", None, ("src/trustforge/replay.py",), ()),
    ("trust-scoring", "Trust 計分核心", "TRUST KERNEL", "core-release", None, ("src/trustforge/trust/scoring.py",), ()),
    ("evidence-contract", "Evidence 綁定契約", "TRUST KERNEL", "core-release", None, ("src/trustforge/schema.py",), ()),
    ("reporting", "報告與交付契約", "DELIVERY", "sandbox-policy", "report", ("src/trustforge/pipeline.py",), ("report-evidence-log",)),
    ("evaluation", "評測題庫與回放", "DELIVERY", "sandbox-policy", "evaluation", ("src/trustforge/question_bank.py", "src/trustforge/historical_replay.py"), ()),
    ("cost-governance", "成本與預算治理", "OPERATIONS", "reviewed-release", None, ("src/trustforge/budget_guard.py", "src/trustforge/ledger.py"), ()),
    ("observability-ui", "觀測與管理介面", "OPERATIONS", "reviewed-release", None, ("frontend/src/pages/HermesDashboard.tsx",), ()),
    ("improvement", "改善診斷器", "OPERATIONS", "sandbox-policy", "improvement", ("src/trustforge/improvement.py",), ()),
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


def _paths_hash(paths: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for relative in paths:
        path = _root() / relative
        digest.update(relative.encode())
        if path.is_dir():
            for child in sorted(path.rglob("*.py")):
                digest.update(str(child.relative_to(_root())).encode())
                digest.update(child.read_bytes())
        elif path.is_file():
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
    for module_id, name, plane, channel, family, paths, areas in MODULES:
        related = [p for p in proposals if isinstance(p, dict) and p.get("area") in areas]
        if channel == "core-release":
            revision = _paths_hash(paths) or _core_hash()
            origin = "packaged-core"
            state = "locked"
            history_rows: list[dict[str, Any]] = []
        elif family is not None:
            resolved = outer[family]
            revision = str(resolved["revision"])
            origin = str(resolved["origin"])
            history_rows = [r for r in history if r.get("skill_id") == f"outer-{family}"][-8:]
            staged = any(r.get("action") == "staged" and r.get("skill_hash") != revision for r in history_rows)
            state = "candidate" if staged or related else "active"
        else:
            revision = _paths_hash(paths)
            origin = "versioned-release"
            history_rows = []
            state = "candidate" if related else "active"
        modules.append({
            "id": module_id, "name": name, "plane": plane, "channel": channel,
            "family": family or ("trust-core" if channel == "core-release" else "release-artifact"),
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
        }, "planes": ["DATA PLANE", "INTELLIGENCE", "TRUST KERNEL", "DELIVERY", "OPERATIONS"],
        "modules": modules,
    }
