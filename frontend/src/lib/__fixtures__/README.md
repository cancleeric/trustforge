# Live API fixtures

`live-overview.json`、`live-analyze.json` 是用 curl 直接打 LIVE
`http://13.211.110.218/api/overview`、`/api/analyze?...&sample=1` 存下的
真實回應快照（非人工手造）。

用途：`apiClient.test.ts` 拿它們驗證 runtime validator（`validators.ts`）
不會誤殺合法後端資料——codex code review 指出「過嚴驗證比不驗更糟」
（`isOverviewCoin` 曾經誤把可選的 `reputation_trace` 缺席當成畸形）。

如果後端 payload 形狀改版，重新用同樣的 curl 指令更新這兩個檔案即可，不需
要手改 JSON。
