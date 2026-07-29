# 需求：研究方法論與治理缺口修復（Epic）

> Issue: #749
> Children: #750, #751, #752, #753
> Labels: epic, research-remediation, governance, security
> Status: Open (awaiting develop→main promotion)

## 背景

追溯審查 PR #739/#743/#744/#745/#746 與後續直接提交，發現以下系統性缺口：

1. **安全**：`build_historical_samples.py` 使用 `eval()` 解析外部輸入。
2. **方法論**：source trainer 報假 AUC；conformal 用 random shuffle 不符時序驗證。
3. **PIT 違規**：宣稱 PIT gate 但未強制；future evidence 可混入。
4. **治理缺失**：五張 PR 全無 GitHub review；#746 跳過 pre-push gate。
5. **知識幽靈**：宣稱的 Obsidian note 與 SkillHub skills 實際不存在。

本 epic 統整四張 remediation issues，確保所有缺口修復後才可關閉。

## 範圍

作為總控 issue，本 epic 不直接產出程式碼。它定義：
- 子 issue 的相依順序
- 全域驗收條件
- CEO 決策記錄
- Promotion boundary

## CEO 決策（已記錄）

1. Source reliability 不報假 AUC，只報 accuracy/balanced accuracy/Brier/support/CI。
2. Calibration report 若保留 ROC AUC，必須是 tie-aware Mann–Whitney，目標限於 confidence 對 correctness 辨識。
3. Missing/invalid evidence timestamp：排除並計數，fail-closed。
4. Cutoff：UTC calendar date inclusive。
5. 舊 artifacts 保留但標 superseded，重生驗證後才可作決策。

## 子 Issue 相依鏈

```
#750 (data: sample contract security, PIT, same-day)
  ↓
#751 (metrics: honest AUC/calibration)     #752 (conformal: chronological split)
  ↓                                          ↓
#753 (governance: retrospective + knowledge artifacts)
  ↓
#749 (epic: 全域驗收 → close)
```

- #750 必須先完成（#751 和 #752 依賴修正後的 sample contract）。
- #753 依賴三張 remediation PR 全部完成。
- #749 在四張子 issue 全部驗收後才關閉。

## 全域驗收條件

- [ ] 四張 remediation PR 各自從 scoped branch 開發並連回子 issue。
- [ ] 每張 PR 有 named reviewer、Eye、/codex-review、commit-bound attestation。
- [ ] 安全/PIT PR (#750) 有 harper（CISO）額外審查。
- [ ] 每張 PR 完整 `.githooks/pre-push` 全綠才 push。
- [ ] 對五張舊 PR 留 retrospective finding/fix/disposition，不偽造歷史 approval。
- [ ] 建立並 read-back Obsidian note 與兩個 SkillHub skills。
- [ ] CEO 親測四個 CLI 與 artifacts 後才能完成。
- [ ] develop→main promotion 完成後才關閉本 epic。

## 非功能需求

- **NFR-1: 不授權 production promotion** — source reputation 與 conformal 仍為 research-only。
- **NFR-2: 不偽造歷史** — 後續 evidence 只證明修復 commit，不溯及歷史 PR。
- **NFR-3: 可稽核** — 所有 gate evidence 綁定 exact commit SHA。
