# #809 設計：由 Claim Extraction 起保存五路 Context

## Job context contract

在 `submit_multi_angle()` 產生不可變的 per-angle request：

```text
{ snapshot_id, coin, mode, question, locale, origin="multi_angle" }
```

`question` 是每個 mode 的明確問題：若使用者提供 base question，模板必須以 mode-specific instruction 擴寫；若未提供，使用 mode-specific default。不允許五個 job 共用一段沒有角度語意的 question。

## Pipeline propagation

在 enqueue payload、job record、Claim Extraction input/stage event、後續 stage context、report payload 和 `analysis_results` 都保存相同 mode/question。Claim Extraction 是驗證的第一個 gate；若 context 不完整，寫入 structured failure，不得繼續成為 synthesizable result。

## Synthesis gate

`_maybe_trigger_synthesis(snapshot_id, coin)` 先讀五個 expected modes 的 durable job records，逐一驗證：snapshot、mode、question、完整 stage sequence、report success 與 provenance。缺任一項則寫入 pending reason 並返回。僅 gate 通過才取得結果、呼叫 #808 synthesis、寫入 `mode='multi_angle'`。

## Production acceptance harness

提供可重跑的 acceptance command，從 durable production store 讀取 `snap-btc-eca5b069d33ea8ac`。輸出 machine-readable audit：五個 mode、job IDs、question digest、claim-extraction context、stage sequence、synthesis status。此 harness 禁止 construct synthetic jobs/results；找不到 snapshot 視為未取得驗收，不可稱為 pass。

## Failure modes

- 缺 mode/question：job failure，synthesis pending。
- mode 與 result 不一致：reject result，記錄 lineage violation。
- 五路尚未完整：200/pending API response，非偽 completed report。
- synthesis exception：report delivery 不回滾，但記錄 fail-soft error。
