# #809 五角度後端入口與整合修正

> Issue: #809 (OPEN，退回修正)
> 依賴：#808 remediation 資料契約
> 實證：production snapshot `snap-btc-eca5b069d33ea8ac`
> 完成門檻：五個真實 pipeline 全程通過驗收前不得關閉。

## 目標

讓一次 multi-angle 提交從 Claim Extraction 起就固定並傳遞各自的 `mode` 與 `question`，由真實 pipeline 產生五個結果後，安全觸發 #808 的 synthesis。不得把 mode/question 僅附加在報告或 synthesis 層，也不得用人工 payload 模擬整合成功。

## 功能需求

### FR-1：五路 job context

`submit_multi_angle(coin, question, locale)` 必須只建立一個 snapshot，並為 `risk`、`sentiment`、`fundamentals`、`news`、`catalyst` 各建立一個 job。每個 job 在 enqueue 時都有：固定 `mode`、明確且可稽核的 `question`（使用者 question 或該 mode 專屬 template）、`snapshot_id` 與 locale。此 context 必須持續傳至 Claim Extraction stage input、stage event、analysis result payload 與最終 `AngleResult`。

### FR-2：Claim Extraction 起點驗證

Claim Extraction 不可接收未帶 mode/question 的共享輸入再於後續 stage 分流。每一路的第一個可稽核 stage event 必須表明使用正確的 mode/question；若缺失，job 必須 fail safely，禁止產生可被 synthesis 使用的假完整結果。

### FR-3：真實 pipeline synthesis 觸發

僅當同一 snapshot 的五個 job 都成功走過真實 `source_ingestion → claim_extraction → trust_reasoning → evidence_assembly → report_delivery`，且 payload provenance 完整時，才可呼叫 synthesis。任一路失敗、過期、snapshot 不同或 context 缺失時，回傳可理解的 pending/incomplete 狀態，不產生偽共識。

### FR-4：API 與可觀測性

GET `/api/multi-angle` 回傳 #808 分離的比較結果及每一路的 mode/question/provenance/status。POST 保留五倍預算提示與既有信封格式。lineage/execution log 必須可由 snapshot 過濾，顯示每一路從 Claim Extraction 起的 mode/question。

## 驗收條件

1. 以 `snap-btc-eca5b069d33ea8ac` 的真實 production job records 驗證五個 mode 都存在、同 snapshot、question 非空且互相可辨識。
2. 對五路逐一驗證其 Claim Extraction stage event/input 已帶相同的 mode/question；不得只驗證最後 report payload。
3. 對五路逐一驗證完整 stage sequence，使用真實 durable records/payload；不得 mock `enqueue_job`、直接寫 `analysis_results`、或以手寫 synthesis input 取代。
4. 真實 API/flow 產出的 synthesis 僅在五路 provenance/成功狀態符合時存在，並符合 #808 的分離欄位契約。
5. 缺一路、mode/question 不符、或 snapshot 混用的 regression case 必須不觸發 synthesis。
6. production payload 驗收證據（命令、snapshot、五個 job ID、stage/provenance 核對、輸出）須保存；否則 #809 保持 OPEN。
