# Task Skill Loader、Frozen Manifest 與 Approval Governance

> Issue: #920 | Epic: #914
> Depends on: #917
> Labels: agent-os, governance, P0

## 背景

#917 建立了 skill persistence 層。本 issue 實作 runtime 行為：skill discovery、
dependency resolution、per-run frozen revision、stale detection 與
proposal/sandbox/approval activation governance。

## 範圍

實作 Skill Loader 模組，負責：
- 根據 trigger/family/risk/status 篩選可用 skill
- 每次 run 凍結 exact revision hashes（frozen manifest）
- Active pointer 變更不影響既有 run
- 高風險 skill activation governance

**不包含**：Registry schema（#917）、Context Builder（#921）、Runtime wiring（#922）。

## 功能需求

### FR-1: Skill Discovery

```python
def discover_skills(
    *,
    trigger: str | None = None,
    family: str | None = None,
    risk_class: str | None = None,
    lifecycle: str = "active",
) -> list[TaskSkill]:
    """Find skills matching criteria. Only active lifecycle by default."""
```

### FR-2: Dependency Resolution

```python
def resolve_dependencies(skill_id: str) -> list[SkillRevision]:
    """Resolve all transitive `requires` dependencies, returning ordered revisions.
    
    Raises if any dependency is stale/broken/missing active revision.
    """
```

### FR-3: Frozen Manifest

每次 run 開始時，凍結所有選定 skill 的 exact revision hash：

```python
@dataclass
class FrozenSkillManifest:
    run_id: str
    created_at: str
    entries: list[FrozenSkillEntry]  # skill_id + revision_hash pairs

@dataclass
class FrozenSkillEntry:
    skill_id: str
    revision_hash: str
    reason: str  # why this skill was selected
```

凍結後，即使 active pointer 被切換，該 run 仍使用原始 revision。

### FR-4: Stale Detection

Skill 被視為 stale 的條件：
- lifecycle = frozen 或 retired
- active revision 為 None
- 任何 `requires` dependency 為 stale

Stale skill → fail closed（不可被選擇進入 manifest）。

### FR-5: Activation Governance

高風險 skill（`external_write` / `deploy_or_release`）activation 流程：
1. **Proposal**: 建立 activation proposal（skill_id, revision_hash, reason）
2. **Sandbox**: 在隔離環境測試（本 MVP 為 dry-run flag）
3. **Approval**: 人工 approve/reject

未經 approval 的高風險 skill 不可出現在 frozen manifest 中。

### FR-6: Output Boundary

Skill outputs 不可覆寫：
- Trust Kernel（weights, formula, evidence binding）
- Security policy
- Cost / budget limits
- Deployment / activation config

Loader 在 build manifest 時檢查 skill 的 `side_effect_class`，
若為 `external_write` / `deploy_or_release` 且無 approval → reject。

### FR-7: Existing Outer Policy Compatibility

既有 outer policy family（`skills.py::SKILL_FAMILIES`）的 hash/revision 不回歸。
Task Skill Loader 不修改 `run_skill_manifest()` 的行為。

## 非功能需求

- **NFR-1: 零第三方依賴**
- **NFR-2: fail-closed** — stale/broken/unapproved → reject
- **NFR-3: 既有 outer policy 不受影響**

## 驗收條件

1. Discovery 正確按 trigger, family, risk, status 篩選
2. 每次 run 凍結 exact skill/dependency revision hashes
3. Active pointer 變更不影響既有 run
4. 高風險 skills 不可未經 sandbox + approval 就 activate
5. Stale/broken dependencies fail closed
6. Skill outputs 不可 override Trust Kernel, security, cost, deployment
7. 既有 outer policy hashes/revisions 不回歸
8. 完整 pre-push 通過
