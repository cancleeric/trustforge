# Live API fixtures

`live-overview.json`、`live-analyze.json` 是從執行中後端 API 完整擷取的
回應快照，不以手工替換局部欄位。`live-analyze.json` 使用目前分支的本機
後端與 `sample=1`，因此不依賴 production 狀態或產生模型費用。

用途：`apiClient.test.ts` 拿它們驗證 runtime validator（`validators.ts`）
不會誤殺合法後端資料——codex code review 指出「過嚴驗證比不驗更糟」
（`isOverviewCoin` 曾經誤把可選的 `reputation_trace` 缺席當成畸形）。

如果後端 payload 形狀改版，請啟動目前分支後端並重新擷取整份回應；不要只
手改 JSON 的個別欄位。
