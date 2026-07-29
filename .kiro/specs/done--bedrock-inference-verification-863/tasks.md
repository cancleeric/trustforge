# 實作任務：Bedrock 推理服務開啟與 claim_id 溯源行文驗證

> Issue: #863

## Task 1: 環境驗證腳本 `scripts/verify_bedrock.py`

- [x] 建立 `scripts/verify_bedrock.py`
- [x] 檢查必要環境變數：`AWS_REGION`、`BEDROCK_MODEL_ID`、`BEDROCK_HAIKU_MODEL_ID`
- [x] 嘗試建立 boto3 `bedrock-runtime` client
- [x] 呼叫 STS `get-caller-identity` 確認 credential 有效（不記錄帳號 ID）
- [x] 嘗試呼叫 `bedrock` client（非 runtime）的 `list_foundation_models` 確認模型存取權
- [x] 輸出結構化 JSON 報告到 stdout（模型、區域、權限狀態）
- [x] 加入 `--dry-run` 選項（只檢查環境變數，不實際呼叫 AWS）
- [x] 確保不輸出任何 credential/token/secret

## Task 2: Smoke Test 腳本 `scripts/smoke_test_bedrock.py`

- [x] 建立 `scripts/smoke_test_bedrock_extended.py`
- [x] 用極短 prompt 呼叫 `BedrockClient.complete()`（如 "回覆 OK"）
- [x] 記錄 model_id、region、response_length、input/output tokens、elapsed_sec
- [x] 用固定 a/b 句對呼叫 `BedrockClient.classify_stance()` 驗證 stance 模型
- [x] 記錄 stance 模型結果與耗時
- [x] 結果寫入 `out/bedrock_smoke_test.json`
- [x] 失敗時分類錯誤類型（credential/permission/model-not-found/timeout/unknown）
- [x] 加入 exit code（0=pass, 1=fail）

## Task 3: claim_id 溯源驗證腳本 `scripts/verify_traceability.py`

- [x] 建立 `scripts/verify_traceability.py`
- [x] 實作 `_build_fixture_docs(coin="BTC")` 產生 ≥5 筆 Document（price/news/onchain）
  - price: 從 `data/` 目錄讀取真實 OHLCV 最近 5 日
  - news: 合成 2 筆有方向性的新聞 Document
  - onchain: 合成 1 筆鏈上指標 Document
- [x] 使用 `BedrockClient(offline=False)` 建立線上 client
- [x] 執行 `run_agent_pipeline(query, coin, qtype, docs, client, log)`
- [x] 從 `Report.inferences` 提取所有 claim_id（regex: `[\w\-]+#(?:llm)?\d+`）
- [x] 驗證 claim_id 數量 ≥ 5
- [x] 驗證每個被引用的 claim_id 可在 evidence list 的 `related_claim` 中追溯
- [x] 結果寫入 `out/bedrock_traceability.json`

## Task 4: 行文層次驗證

- [x] 在 `verify_traceability.py` 中新增行文層次檢查
- [x] 驗證 narrative 包含客觀事實引用（price/onchain 相關 claim_id）
- [x] 驗證 narrative 包含推論性語句（非逐字引用原始事實）
- [x] 驗證 `Report.market_judgment` 包含方向性結論與信心聲明
- [x] 驗證 narrative 不含離線降級字樣：
  - 不含「離線模式未執行線上模型生成」
  - 不含「未執行線上模型」
  - 不含 `_loc.offline_narrative()` 的產出

## Task 5: 降級正確性驗證

- [x] 在 `verify_traceability.py` 中新增降級測試函式 `_test_degraded_mode()`
- [x] 使用故意錯誤的 `model_id` 建立 `BedrockClient`
- [x] 執行 pipeline，驗證不中斷（不 raise）
- [x] 驗證 Report 中含降級標記（`limits` 中有失敗說明）
- [x] 驗證 `execution_log` 記錄了失敗事件
- [x] 結果併入 `out/bedrock_traceability.json` 的 `degradation_test` 區段

## Task 6: 護欄生效驗證

- [x] 在 `verify_traceability.py` 中新增護欄檢查函式 `_verify_guardrails(log)`
- [x] 驗證 execution_log 含 ≥2 筆 `bedrock.complete` 事件（Step 1 + Step 3）
- [x] 驗證 `llm.cost` 事件有 `tokens_in > 0`、`tokens_out > 0`、`cost_usd > 0`
- [x] 驗證 ledger（`ledger.daily_cost_usd()`）反映本次花費
- [x] 驗證 `log.remaining()` 在 pipeline 結束時仍 > 0（未超時）
- [x] 結果併入 `out/bedrock_traceability.json` 的 `guardrails` 區段

## Task 7: claim_id 正則與 fixture 單元測試

- [x] 建立 `tests/test_verify_scripts.py`
- [x] 測試 claim_id 正則匹配各種格式：
  - `"price_btc_001#0"` → 匹配
  - `"news_eth_002#llm3"` → 匹配
  - `"doc-with-dash#llm12"` → 匹配
  - `"no_hash_here"` → 不匹配
- [x] 測試 fixture 建構函式產出正確數量/kind
- [x] 測試降級偵測邏輯（含離線字樣 → True、不含 → False）
- [x] 確認測試通過

## Task 8: 整合執行與證據留存

- [x] 依序執行：verify_bedrock.py → smoke_test_bedrock.py → verify_traceability.py
- [x] 確認所有 `out/` 輸出 JSON 結構完整、overall=pass
- [x] 在環境變數未設定時，腳本 gracefully 報告缺失並 exit 1（不 crash）
- [x] 確認 `out/` 目錄在 `.gitignore`（不 commit 證據到 repo）
- [x] 驗證完整流程耗時 < 5 分鐘

## Task 9: 文件更新與回歸確認

- [x] 在 `README.md` 中補充「如何執行 Bedrock 驗證」段落
- [x] 執行完整 pytest suite，確認新增腳本/測試不造成回歸
- [x] 執行 lint / type-check，確認通過
- [x] 確認不修改核心邏輯檔案（bedrock.py / scoring.py / orchestrator.py）
