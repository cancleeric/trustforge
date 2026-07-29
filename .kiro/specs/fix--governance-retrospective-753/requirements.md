# 需求：追溯五張 PR 治理證據與知識沉澱

> Issue: #753
> Parent: #749
> Depends on: #750, #751, #752 (all remediation PRs)
> Labels: governance, research-remediation, documentation
> PR: #759 (merged to develop)

## 背景

PR #739/#743/#744/#745/#746 與直接提交 e8817adb/64845295/ffb3e441/9c112f99 合入 develop 時：

- GitHub reviews 全部為空（零 reviewer）。
- #746 明確記錄未跑完整 pre-push gate。
- 直接提交無關聯 PR。
- 多項宣稱的 evidence（Obsidian note、SkillHub skills）實際不存在。

本 issue 的目的是：**如實記錄缺口、連結修復、不偽造歷史**。

## 範圍

產出追溯治理文件、在歷史 PR 留 retrospective comments、並補建知識沉澱 artifacts。

## 功能需求

### FR-1: 追溯 Retrospective 文件

- 建立 `docs/audit/RESEARCH-REMEDIATION-RETROSPECTIVE-2026-07-27.md`。
- 逐項記錄：actual merge SHA、original head、retrospective finding、remediation disposition。
- 涵蓋 PR #739/#743/#744/#745/#746 + 直接提交 e8817adb/64845295/ffb3e441/9c112f99。

### FR-2: 歷史 PR 留 Finding Comment

- 在 PR #739/#743/#744/#745/#746 各自留 finding→fix→disposition comment。
- 不倒填或偽造歷史 approval。
- 只記 retrospective attestation（如實陳述：「此 PR 合入時無 reviewer」）。

### FR-3: 不偽造歷史原則

- 不倒填 GitHub review approval。
- 不產出虛假的 Eye scan 結果。
- 不倒填 /codex-review 結果。
- 後續 evidence 只證明「修復 commit 的正確性」，不代表歷史 commit 有通過。

### FR-4: 建立 Obsidian Note

- 建立 `h-obsidian project_trustforge_session_20260727.md`。
- Read-back 驗證 PASS。

### FR-5: 建立 SkillHub Skills

- 建立 `milestone-pipeline-honest-research-state` skill。
- 建立 `dependency-unblock-guard` skill。
- Exact search/load/diff/commit 驗證。
- Dependency graph PASS（guard→milestone edge）。

### FR-6: Release Boundary 聲明

- 明確聲明：本 remediation 不授權 production promotion。
- Source reputation 與 conformal prediction 仍為 research-only。
- Promotion 需各自的 source-family/temporal/PIT/correctness/error/abstention 與 review gates pass。

## 非功能需求

- **NFR-1: 本 docs PR 也跑完整 pre-push** — 純文件變更仍需 gate 通過。
- **NFR-2: 可稽核性** — 所有 remediation evidence 綁定 exact commit SHA。
- **NFR-3: Non-retroactivity** — 任何後續 test/review 不溯及既往。

## 驗收條件

1. `docs/audit/RESEARCH-REMEDIATION-RETROSPECTIVE-2026-07-27.md` 存在且內容完整。
2. PR #739/#743/#744/#745/#746 各有 finding→fix→disposition comment（可從 GitHub 驗證）。
3. h-obsidian note 建立並 read-back PASS。
4. SkillHub 兩個 skills 存在、search/load PASS、dependency graph 有正確 edge。
5. Named reviewer + Eye + /codex-review + full pre-push 通過。
