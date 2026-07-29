# 設計：追溯五張 PR 治理證據與知識沉澱

> Issue: #753
> PR: #759 (merged to develop)

## 架構決策

### AD-1: 純文件變更——不改 runtime 程式碼

本 issue 的 deliverable 全為文件與外部 knowledge artifacts：
- Repository doc: `docs/audit/RESEARCH-REMEDIATION-RETROSPECTIVE-2026-07-27.md`
- GitHub PR comments: finding→fix→disposition on historical PRs
- h-obsidian note: project status 記錄
- SkillHub skills: reusable workflow knowledge

不修改任何 `src/` 或 `scripts/` 程式碼。

### AD-2: Retrospective 文件結構

```markdown
# Research remediation retrospective — 2026-07-27

## Disposition
REMEDIATION REQUIRED

## Historical scope and findings
| Item | Actual merge/commit | Finding | Disposition |

## Remediation evidence verified from GitHub
| Remediation | State | Gate evidence |

## #753 completion boundary
(self-referential: own gates recorded after commit exists)

## Non-retroactivity
(explicit non-fabrication statement)

## Release boundary
(no production promotion authorized)
```

### AD-3: Historical PR Comment 格式

在每張舊 PR 留一則 comment（非 review）：

```
## Retrospective finding (2026-07-27)

**Finding**: This PR was merged without any GitHub reviewer, Eye, or /codex-review.
**Fix**: Revalidated by #{remediation_issue} / PR #{remediation_pr}, merged as {sha}.
**Disposition**: The missing historical approval remains missing. Current evidence
applies only to the remediation head.
```

### AD-4: Knowledge Artifact Strategy

#### h-obsidian note
```yaml
filename: project_trustforge_session_20260727.md
type: project
name: TrustForge 研究可信度 remediation session
description: 追溯 #749 四張修復 PR 與治理 retrospective
```

Body 記錄 session 內容、四張 PR 狀態、gate evidence。

#### SkillHub skills
1. `milestone-pipeline-honest-research-state` — 記錄如何誠實標記研究實驗結論狀態
2. `dependency-unblock-guard` — 記錄 dependency 之間的 unblock 順序管理
   - depends_on: milestone skill（形成 graph edge）

### AD-5: Gate 自引用問題

本 retrospective 文件的 /codex-review 和 pre-push 結果無法在寫入 commit 前記錄（因 commit SHA 尚不存在）。因此文件中設 "completion boundary" 章節，指明這些 gate 記錄在 PR 上（而非 commit 中）。

## 測試策略

本 issue 無 runtime 程式碼變更，不需新增 unit tests。

驗證方式：
- Full pre-push gate 必須通過（驗證文件不破壞 lint/build/data 檢查）。
- h-obsidian `read_note` → 內容正確。
- SkillHub `search_skills` / `load_skill` / `skill_dependency_graph` → 正確結果。

## 影響範圍

- `docs/audit/RESEARCH-REMEDIATION-RETROSPECTIVE-2026-07-27.md` — 新增
- GitHub PR #739/#743/#744/#745/#746 — 各留 1 則 retrospective comment
- h-obsidian vault — 1 note
- SkillHub — 2 skills + dependency edge
