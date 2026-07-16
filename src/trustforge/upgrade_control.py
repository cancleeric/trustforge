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

from . import __version__
from .skill_changes import change_history
from .skills import run_skill_manifest


MODULES = (
    # id, label, plane, upgrade channel, outer family, implementation paths, proposal areas
    ("connectors", "來源連接器", "DATA PLANE", "sandbox-policy", "source", ("src/trustforge/ingestion",), ("data-acquisition", "execution-efficiency")),
    ("connector-routing", "連接器路由與 fallback", "DATA PLANE", "sandbox-policy", "source", ("src/trustforge/ingestion/safe_fetch.py",), ("data-acquisition",)),
    ("source-frequency", "來源頻率與 timeout", "DATA PLANE", "sandbox-policy", "source", ("src/trustforge/scheduler_log.py",), ("execution-efficiency",)),
    ("normalization-dedup", "正規化與去重", "DATA PLANE", "reviewed-release", None, ("src/trustforge/ingestion/base.py", "src/trustforge/ingestion/cache.py"), ()),
    ("snapshots", "不可變快照生成", "DATA PLANE", "reviewed-release", None, ("src/trustforge/analysis_flow.py", "src/trustforge/replay.py"), ()),
    ("cache-freshness", "快取與鮮度策略", "DATA PLANE", "reviewed-release", None, ("src/trustforge/freshness.py", "src/trustforge/ingestion/cache.py"), ()),
    ("scheduler", "併發、backpressure 與 DLQ", "DATA PLANE", "sandbox-policy", "improvement", ("src/trustforge/analysis_flow.py",), ("analysis-orchestration",)),
    ("prompt-templates", "Prompt 與任務模板", "INTELLIGENCE", "sandbox-policy", "analysis", ("src/trustforge/agent/orchestrator.py",), ()),
    ("tool-routing", "Agent 工具與技能路由", "INTELLIGENCE", "sandbox-policy", "analysis", ("src/trustforge/hermes.py", "src/trustforge/skills.py"), ()),
    ("question-rag", "題目 RAG 與對話記憶", "INTELLIGENCE", "reviewed-release", None, ("src/trustforge/analysis_flow.py",), ("question-retrieval",)),
    ("rag-index", "Embedding 與索引策略", "INTELLIGENCE", "model-gate", None, ("src/trustforge/analysis_flow.py",), ("question-retrieval",)),
    ("rag-reranker", "Reranker 與分面生成", "INTELLIGENCE", "model-gate", None, ("src/trustforge/analysis_flow.py",), ("question-retrieval",)),
    ("claim-extraction", "主張抽取", "INTELLIGENCE", "sandbox-policy", "analysis", ("src/trustforge/agent/orchestrator.py",), ()),
    ("contrarian-search", "反方證據搜尋", "INTELLIGENCE", "sandbox-policy", "analysis", ("src/trustforge/agent/orchestrator.py",), ()),
    ("manipulation-detection", "操縱與協同偵測", "INTELLIGENCE", "sandbox-policy", "analysis", ("src/trustforge/trust/insights.py",), ()),
    ("analysis-policy", "分析策略", "INTELLIGENCE", "sandbox-policy", "analysis", ("src/trustforge/skills.py",), ("historical-calibration",)),
    ("model-routing", "模型選擇與 active route", "INTELLIGENCE", "model-gate", None, ("src/trustforge/modelhub_training.py",), ("historical-calibration",)),
    ("calibration-abstain", "校準器與 abstain", "INTELLIGENCE", "model-gate", None, ("src/trustforge/calibration.py", "src/trustforge/calibrator_gate.py"), ("historical-calibration",)),
    ("evidence-assembly", "Evidence 組裝", "DELIVERY", "sandbox-policy", "report", ("src/trustforge/pipeline.py", "src/trustforge/execlog.py"), ("report-evidence-log",)),
    ("reporting", "報告敘事與交付", "DELIVERY", "sandbox-policy", "report", ("src/trustforge/pipeline.py",), ("report-evidence-log",)),
    ("citation-localization", "引用、語言與格式", "DELIVERY", "sandbox-policy", "report", ("src/trustforge/pipeline.py",), ()),
    ("evaluation", "評測題庫與品質 gate", "DELIVERY", "sandbox-policy", "evaluation", ("src/trustforge/question_bank.py",), ()),
    ("historical-replay", "歷史回放與回歸門檻", "DELIVERY", "sandbox-policy", "evaluation", ("src/trustforge/historical_replay.py", "src/trustforge/replay.py"), ("historical-calibration",)),
    ("cost-governance", "成本與預算治理", "OPERATIONS", "reviewed-release", None, ("src/trustforge/budget_guard.py", "src/trustforge/ledger.py"), ()),
    ("rate-resource-policy", "速率限制與資源配額", "OPERATIONS", "reviewed-release", None, ("src/trustforge/rate_limit_store.py", "src/trustforge/budget_counter.py"), ()),
    ("observability-ui", "觀測與管理介面", "OPERATIONS", "reviewed-release", None, ("frontend/src/pages/HermesDashboard.tsx",), ()),
    ("alert-policy", "告警與操作流程", "OPERATIONS", "reviewed-release", None, ("src/trustforge/cloudwatch_metrics.py",), ()),
    ("memory-policy", "h-obsidian 記憶策略", "OPERATIONS", "reviewed-release", None, ("docs/architecture/HERMES-CONTINUOUS-INTELLIGENCE-2026-07-16.md",), ()),
    ("security-masking", "權限、審計與遮罩", "OPERATIONS", "core-adjacent-release", None, ("src/trustforge/web.py", "src/trustforge/admin_config.py"), ()),
    ("schema-compatibility", "Schema migration 與相容性", "OPERATIONS", "reviewed-release", None, ("src/trustforge/schema.py",), ()),
    ("improvement", "改善診斷器", "OPERATIONS", "sandbox-policy", "improvement", ("src/trustforge/improvement.py",), ()),
)

CORE_CONTROLS = (
    "Point-in-time 時間邊界", "Evidence 必填契約", "正式 run 隔離",
    "可重現性與稽核鏈", "歷史結論不得冒充新 Evidence", "禁止自動部署與遞回升級",
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


def _llm_review() -> dict[str, Any]:
    path = _root() / "out" / "hermes-upgrade-review-latest.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "not_run", "reviews": [], "can_activate": False}
    return value if isinstance(value, dict) else {"status": "invalid", "reviews": [], "can_activate": False}


def upgrade_status() -> dict[str, Any]:
    """Project versioned core/outer modules and approval-gated candidates."""
    manifest = run_skill_manifest()
    outer = {row["family"]: row for row in manifest["outer_skills"]}
    history = change_history()
    diagnostic = _diagnostic()
    llm_review = _llm_review()
    try:
        from .analysis_flow import AnalysisFlow
        with AnalysisFlow() as flow:
            measurements = flow.improvement_history()
    except Exception:
        measurements = {}
    try:
        from .upgrade_queue import UpgradeQueue
        durable_queue = UpgradeQueue().status()
    except Exception:
        durable_queue = {"durable": False, "proposal_count": 0, "proposals": [], "reviews": []}
    proposals = diagnostic.get("proposals") if isinstance(diagnostic.get("proposals"), list) else []
    modules = []
    for module_id, name, plane, channel, family, paths, areas in MODULES:
        related = [p for p in proposals if isinstance(p, dict) and p.get("area") in areas]
        if family is not None:
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
            "family": family or "release-artifact",
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
        }, "coverage": {"registered": len(modules), "complete": True},
        "automation": {
            "mode": "continuous_data_driven_outer_tuning",
            "measurements": measurements,
            "llm_review": llm_review,
            "durable_queue": durable_queue,
            "stages": [
                {"id": "observe", "state": "running"},
                {"id": "measure", "state": "ready" if measurements else "waiting_data"},
                {"id": "propose", "state": "candidate" if proposals else "monitoring"},
                {"id": "llm-review", "state": llm_review.get("status", "not_run")},
                {"id": "sandbox", "state": "waiting_candidate" if proposals else "idle"},
                {"id": "human-gate", "state": "locked"},
            ],
        },
        "core_package": {
            "id": "trust-kernel", "name": "TRUST KERNEL PACKAGE",
            "version": f"v{__version__}", "revision": _core_hash(), "state": "release-locked",
            "controls": list(CORE_CONTROLS), "upgrade_channel": "reviewed-core-release",
            "external_upgrade": {"status": "reserved", "adapter": None, "automatic_activation": False},
        },
        "planes": ["DATA PLANE", "INTELLIGENCE", "DELIVERY", "OPERATIONS"],
        "modules": modules,
    }
