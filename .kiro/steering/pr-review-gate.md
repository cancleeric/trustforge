---
inclusion: auto
---

# PR 審查閘門規範（PR Review Gate）

每個 PR 從開立到合併必須通過以下閘門，缺一不可：

## 開立 PR 時

1. **指定 reviewer** — 每個 PR 必須有至少一位具名 reviewer（不可留空）。單人 repo 使用 commit-bound reviewer attestation 替代 GitHub approval。

## 合併前

2. **eye 掃描** — 對該分支的實際畫面（桌面 + 手機）執行視覺驗收，檢查：
   - 佈局正確性（無溢出、無截斷）
   - 資料真實性（顯示內容與 API 回傳一致）
   - 狀態轉換（loading → success → error 三態）
   - 無 overflow / 無空白頁

3. **/codex-review 對抗審** — 執行對抗式程式碼審查，修正所有 finding 並重跑受影響的 local gates。

4. **pre-push-only evidence** — 所有 PR 必須在 PR body 或 comment 記錄 commit-bound pre-push evidence：commit SHA、執行時間、`.githooks/pre-push` 的 exact gate list、實際執行結果，以及任何未執行 gate 的明確原因。

## 合併條件

- GitHub Actions workflow 保持 disabled，不作為 required checks，也不可用來替代本機 gate。
- `.githooks/pre-push` 必須通過（lint + test + build + diff check 等 hook 內列出的 gates）
- 所有 reviewer finding 已解決
- eye 掃描證據記錄在 PR comment 中
- /codex-review 無未修正的 HIGH/MEDIUM finding
- security 相關修改需 harper (CISO) + gray (CPO) review；cost 相關修改至少需 harper review。

## 禁止事項

- 禁止使用 admin override 繞過保護
- 禁止 `--no-verify` 跳過 hooks
- 禁止在無 reviewer 的情況下合併
