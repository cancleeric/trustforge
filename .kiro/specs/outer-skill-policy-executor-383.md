# Spec：Outer Skill 受限 Runtime Policy Executor (#383)

> Issue: #383
> Priority: P1-core
> Depends: #380, #374; aligns with #386 PolicyProvider contract

---

## Requirements

### R1: 每個 outer skill family 具備 typed policy schema + compiler/loader/consumer

- source / analysis / report / evaluation / improvement 各定義 JSON Schema
- 每 family 有 `compile(raw_artifact) -> TypedPolicy` 把 JSON → 強型別 dataclass
- 每 family 有 `load(revision_hash) -> TypedPolicy` 從磁碟/快取讀取已核准版本
- consumer（pipeline 各階段）透過 typed API 讀取 policy 值，不直接解析 raw JSON
- 契約測試驗證 schema ↔ compiler ↔ consumer 三者一致

### R2: fail-closed 安全邊界

- forbidden keys（trust_weights / core / time_boundary / evidence_binding / security / cost / deploy）寫入時 reject
- 未知 action type → reject（不 fall-through）
- 任意程式碼字串（`exec` / `eval` / `__import__` / template `{{` / `${`）→ reject
- 所有 reject 產生結構化 audit event 並 abort，不 partial-apply

### R3: 核心與部署永不在可自主升級範圍

- `FORBIDDEN_FAMILIES` 加入 `deploy` / `core` / `security` / `cost`
- `upgrade_control.py` 的升級提案路徑加 hard guard：非 SKILL_FAMILIES 的提案被攔截
- 測試覆蓋「嘗試透過 policy executor 修改 trust weights / PIT / evidence binding」均 fail

### R4: 未核准 revision 不影響正式 run

- `resolve_active_skills()` 只讀 approved/rolled_back pointer；staged 不可見
- `run_skill_manifest()` freeze 時 snapshot effective policy → execution log
- approve / rollback / run-freeze 三情境各有整合測試

### R5: execution log 記錄 effective policy

- 每次 run 的 `execution_log.jsonl` 加一條 `{"event": "policy_snapshot", "policies": {...}}`
- 含每 family 的 revision hash + 簡化 rule summary
- 可由 log 完整還原本次 run 的 effective policy 組合

### R6: deploy artifact 清理

- `skills/hermes/deploy/` 目錄移至 `archive/skills-deploy/`（保留 git history）
- 或加 `ARCHIVED.md` 標注「此 family 不納入 runtime，僅作歷史參考」
- `validate_artifact()` 對 `deploy` family 顯式 raise "archived family"

### R7: security / adversarial review section

- PR 描述需包含 adversarial review section
- 覆蓋：injection 嘗試（code / template / path traversal）、race condition（concurrent approve + run）、hash collision 利用

---

## Design

```
src/trustforge/
├── policy/                        ★ 新增 package
│   ├── __init__.py               # PolicyExecutor 公開 API
│   ├── schema.py                 # per-family JSON Schema 定義 (dict literals)
│   ├── compiler.py               # raw artifact → TypedPolicy dataclass
│   ├── loader.py                 # revision hash → validated TypedPolicy
│   ├── executor.py               # apply policy to pipeline context (read-only)
│   └── guards.py                 # injection/forbidden/unknown-action 檢查
├── skills.py                     # 修改：validate_artifact 加 archived family reject
└── execlog.py                    # 修改：加 policy_snapshot event helper
```

### TypedPolicy dataclass（per family）

```python
# src/trustforge/policy/schema.py
from dataclasses import dataclass, field

@dataclass(frozen=True)
class SourcePolicy:
    timeout_sec: int = 30
    max_concurrent: int = 5
    retry_limit: int = 2
    fallback_order: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class AnalysisPolicy:
    claim_extraction_budget: int = 40
    contrarian_search_enabled: bool = True
    max_llm_calls: int = 8

@dataclass(frozen=True)
class ReportPolicy:
    language: str = "zh-TW"
    max_sections: int = 6
    include_contrarian: bool = True

@dataclass(frozen=True)
class EvaluationPolicy:
    min_pass_score: float = 0.6
    replay_sample_size: int = 5

@dataclass(frozen=True)
class ImprovementPolicy:
    proposal_limit: int = 3
    auto_stage: bool = False  # stage only, never auto-approve

FAMILY_SCHEMA: dict[str, type] = {
    "source": SourcePolicy,
    "analysis": AnalysisPolicy,
    "report": ReportPolicy,
    "evaluation": EvaluationPolicy,
    "improvement": ImprovementPolicy,
}
```

### Guards（fail-closed）

```python
# src/trustforge/policy/guards.py
import re

FORBIDDEN_FAMILIES = frozenset({"deploy", "core", "security", "cost"})
FORBIDDEN_KEYS = frozenset({"trust_weights", "core", "time_boundary", "evidence_binding", "security", "cost", "deploy"})
INJECTION_PATTERNS = re.compile(r"(exec\s*\(|eval\s*\(|__import__|(\{\{|\$\{))")

def check_artifact(value: dict) -> None:
    family = value.get("family")
    if family in FORBIDDEN_FAMILIES:
        raise SecurityError(f"family '{family}' is archived/forbidden and cannot be executed")
    if set(value) & FORBIDDEN_KEYS:
        raise SecurityError("outer skills may not override core controls")
    raw = json.dumps(value)
    if INJECTION_PATTERNS.search(raw):
        raise SecurityError("potential code/template injection detected")
    # unknown action → reject
    if "actions" in value:
        for action in value["actions"]:
            if action.get("type") not in ALLOWED_ACTION_TYPES:
                raise SecurityError(f"unknown action type: {action.get('type')}")
```

### Executor（read-only application）

```python
# src/trustforge/policy/executor.py
from .compiler import compile_policy
from .loader import load_approved_policy
from .guards import check_artifact

class PolicyExecutor:
    """Resolves and applies approved outer-skill policies to a run context."""

    def resolve_effective(self, run_context) -> dict[str, TypedPolicy]:
        """Return frozen effective policies; staged revisions excluded."""
        ...

    def snapshot_for_log(self) -> dict:
        """Serializable snapshot for execution_log.jsonl."""
        ...
```

### deploy artifact 清理

```bash
git mv skills/hermes/deploy/ archive/skills-deploy/
# 加 archive/skills-deploy/ARCHIVED.md
```

---

## Tasks

- [ ] 1. 建立 `src/trustforge/policy/` package：schema.py（5 family dataclass）+ guards.py（injection/forbidden/unknown 檢查）
- [ ] 2. 實作 compiler.py：raw artifact dict → frozen TypedPolicy，field validation
- [ ] 3. 實作 loader.py：從 skill artifact 檔案 load + validate + compile
- [ ] 4. 實作 executor.py：PolicyExecutor.resolve_effective() + snapshot_for_log()
- [ ] 5. 修改 skills.py：validate_artifact 加 FORBIDDEN_FAMILIES（deploy/core/security/cost）reject
- [ ] 6. 修改 execlog.py：加 `log_policy_snapshot(effective_policies)` helper
- [ ] 7. 修改 pipeline.py / orchestrator.py：run 開始時呼叫 executor，寫 policy_snapshot event
- [ ] 8. 歸檔 `skills/hermes/deploy/` → `archive/skills-deploy/` + ARCHIVED.md
- [ ] 9. 契約測試：schema ↔ compiler ↔ consumer 一致性（tests/test_policy_contract.py）
- [ ] 10. 安全測試：injection / forbidden key / unknown action / archived family 均 fail-closed
- [ ] 11. 整合測試：approve → run freeze → rollback → 確認 effective policy 變化
- [ ] 12. PR 含 security/adversarial review section
