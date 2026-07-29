# 實作任務：研究方法論與治理缺口修復（Epic）

> Issue: #749
> Status: Open (awaiting develop→main promotion)

## Task 1: 子 Issue #750 — Sample Contract 安全修復

- [x] PR #754 從 `fix/750-sample-contract-pit` branch 開發
- [x] 連回 issue #750
- [x] Named reviewer + harper CISO + Eye + /codex-review + full pre-push
- [x] Merged to develop as `bfb16e9d`

## Task 2: 子 Issue #751 — 誠實指標修復

- [x] PR #756 從 `fix/751-honest-metrics` branch 開發
- [x] 連回 issue #751
- [x] Named reviewer + Eye + /codex-review（7 rounds）+ full pre-push
- [x] Merged to develop as `93d5390e`

## Task 3: 子 Issue #752 — Chronological Conformal 修復

- [x] PR #755 從 `fix/752-chronological-conformal` branch 開發
- [x] 連回 issue #752
- [x] Named reviewer + Eye + /codex-review + full pre-push
- [x] Merged to develop as `0309d3ea`

## Task 4: 子 Issue #753 — 治理 Retrospective

- [x] PR #759 從 `docs/753-retrospective-governance` branch 開發
- [x] 連回 issue #753
- [x] Named reviewer + Eye + /codex-review + full pre-push
- [x] Merged to develop as `58dbfa0f`
- [x] 歷史 PR #739/#743/#744/#745/#746 留 retrospective comment

## Task 5: Knowledge Artifacts 驗證

- [x] h-obsidian `project_trustforge_session_20260727.md` 建立 + read-back PASS
- [x] SkillHub `milestone-pipeline-honest-research-state` — search/load PASS
- [x] SkillHub `dependency-unblock-guard` — search/load PASS
- [x] Dependency graph: guard→milestone edge PASS
- [x] Wiki.js page 3145 — published-state/read-back PASS

## Task 6: CEO 親測 CLI Artifacts

- [ ] `build_historical_samples.py` → verify output JSONL format + PIT enforcement
- [ ] `train_source_reliability.py` → verify artifact schema v2.0.0 + provenance
- [ ] `conformal_on_samples.py` → verify chronological split + honest report
- [ ] Confirm research-only boundary maintained（no production config change）

## Task 7: develop→main Promotion

- [ ] Merge develop to main（or promotion PR）
- [ ] Full pre-push on main
- [ ] Close #750, #751, #752, #753
- [ ] Close #749 (this epic)
