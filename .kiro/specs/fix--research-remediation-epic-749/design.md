# 設計：研究方法論與治理缺口修復（Epic）

> Issue: #749
> Status: Open (awaiting develop→main promotion)

## 架構決策

### AD-1: Epic 不產程式碼，只做總控

本 epic 是純治理層面的控制 issue：
- 不修改 `src/` 或 `scripts/`
- 不新增測試
- 職責是確保子 issues 的交付順序、gate 通過、knowledge artifact 完整

### AD-2: 子 Issue 分工與相依

| Issue | 範圍 | 核心交付 | PR |
|-------|------|----------|-----|
| #750 | 安全 + PIT + 同日多源 | `build_historical_samples.py` 重構 | #754 ✅ |
| #751 | 誠實指標 | `train_source_reliability.py` + `calibration_runner.py` | #756 ✅ |
| #752 | 時序 split + leakage | `conformal_on_samples.py` + `backtest_conformal.py` | #755 ✅ |
| #753 | 治理文件 + knowledge | Retrospective doc + PR comments + Obsidian + SkillHub | #759 ✅ |

所有四張 PR 已 merged to develop。本 epic 剩餘工作：develop→main promotion。

### AD-3: Promotion Gate 設計

develop→main merge 前必須滿足：

1. All four child issues verified on develop（✅ done）。
2. CEO 親測 CLI artifacts：
   - `python3 scripts/build_historical_samples.py --cutoff 2026-07-27 ...`
   - `python3 scripts/train_source_reliability.py --cutoff 2026-07-27 ...`
   - `python3 scripts/conformal_on_samples.py ...`
   - Verify artifact schema/version/provenance
3. Full pre-push on merged develop state（✅ 4,834 backend）。
4. No production config changes（research-only boundary maintained）。

### AD-4: Knowledge Artifact 驗證清單

| Artifact | 建立方式 | 驗證方式 |
|----------|----------|----------|
| h-obsidian `project_trustforge_session_20260727.md` | `write_note` | `read_note` exact read-back |
| SkillHub `milestone-pipeline-honest-research-state` | `upsert_skill` | `search_skills` + `load_skill` |
| SkillHub `dependency-unblock-guard` | `upsert_skill` | `search_skills` + `load_skill` + `skill_dependency_graph` |
| Wiki.js page 3145 | API update | Published state + read-back |

### AD-5: Close Condition

```
IF all child issues acceptance criteria satisfied on develop
AND develop→main promotion merged
AND full pre-push on main passes
THEN close #749
```

## 風險與邊界

- **風險**：develop 與 main 之間累積的其他 feature PRs 可能在 merge 時產生衝突。
  - 緩解：promotion 使用 merge commit（保留歷史），衝突時逐一 rebase。
- **邊界**：本 epic 完成**不**意味 source reputation / conformal 可用於 production。
  - Production promotion 需獨立 issue 與 promotion gate。
