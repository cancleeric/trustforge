# Bedrock 推理服務開啟與 claim_id 溯源行文驗證

> Issue: #863
> 依賴: #851（架構設計拆分母單）
> Labels: enhancement, bedrock, needs-evidence, blocked-external, size:S

## 背景

TrustForge 已具備完整的 Bedrock 呼叫及離線降級骨架（`src/trustforge/bedrock.py`），包含：
- `complete()`：主敘事模型呼叫（Step 3 行文 / Step 4 限制複審）
- `extract_claims_with_llm()`：Step 1 claim 抽取
- `classify_stance()`：W1.5 語意 stance 分類

但目前生產環境尚未真正接通 Bedrock 推理——`BEDROCK_MODEL_ID` 環境變數未設定時一律走離線降級。本 issue 需在真實 AWS 環境中完成接線、驗證推理品質與溯源完整性。

## 範圍

1. **環境設定**：確認 `AWS_REGION`、`BEDROCK_MODEL_ID` 正確設定，執行身分具 IAM 權限
2. **Smoke test**：最小化端對端呼叫，留下模型/區域/成功狀態/耗時證據
3. **推理品質驗證**：
   - Step 3 行文引用具體 claim_id（≥5 條）
   - 行文具有「事實 → 推論 → 結論」層次結構
4. **降級正確性驗證**：
   - Bedrock 成功時不出現離線降級字樣
   - 呼叫失敗時誠實顯示降級狀態
5. **護欄生效確認**：live gate、預算上限、timeout、成本記錄正常運作

## 功能需求

### FR-1: 環境設定驗證腳本

建立 `scripts/verify_bedrock.py`：
- 檢查 `AWS_REGION`、`BEDROCK_MODEL_ID`、`BEDROCK_HAIKU_MODEL_ID` 環境變數
- 嘗試建立 `bedrock-runtime` client 並呼叫 `list_foundation_models`（或等效低成本 API）驗證 IAM 權限
- 輸出結構化結果（模型、區域、權限狀態），不記錄任何 credential/token

### FR-2: Bedrock Smoke Test

建立 `scripts/smoke_test_bedrock.py`：
- 使用極短 prompt 呼叫 `BedrockClient.complete()` 一次
- 記錄：模型 ID、區域、回應長度、input/output tokens、耗時、成功/失敗狀態
- 使用 `BedrockClient.classify_stance()` 呼叫一次（驗 stance 模型可用）
- 結果寫入 `out/bedrock_smoke_test.json`（不含敏感資訊）
- 失敗時輸出明確錯誤類型（credential/permission/model-not-found/timeout）

### FR-3: claim_id 溯源行文驗證

在 smoke test 或獨立腳本中：
- 準備 fixture 資料（≥5 筆 Document，涵蓋 price/news/onchain）
- 執行完整 `run_agent_pipeline`（線上模式）
- 驗證 `Report.inferences` 中的 narrative 文本至少引用 5 條具體 claim_id
- claim_id 格式符合 `{doc.id}#llm{i}` 或 `{doc.id}#{i}`
- 每個被引用的 claim_id 在 evidence.json 的 `related_claim` 或 `supporting_claim_ids` 中可追溯

### FR-4: 行文層次結構驗證

驗證 Step 3 narrative 輸出具備：
- 事實層：引用客觀資料（price/onchain kind）
- 推論層：基於事實的方向性推論
- 結論層：整合判斷（含信心聲明）
- 不出現「離線模式未執行線上模型生成」字樣

### FR-5: 降級正確性驗證

- 線上成功案例：`Report.inferences` 不含 `_loc.offline_narrative()` / `_loc.degraded_narrative()` 相關字樣
- 模型不可用時（故意設錯 `BEDROCK_MODEL_ID`）：
  - pipeline 不中斷
  - 報告誠實顯示降級狀態
  - `execution_log.jsonl` 記錄失敗事件

### FR-6: 護欄生效確認

驗證以下既有機制在線上模式正常運作：
- `budget_guard`：每日成本上限 `$3/day`
- `ledger`：`append_run()` 成功寫入成本記錄
- timeout：`_NARRATIVE_READ_TIMEOUT_SEC`（60s）、`_STANCE_READ_TIMEOUT_SEC`（8s）生效
- `ExecutionLog`：`≥2` 筆 `bedrock.complete` 記錄（Step 1 + Step 3）
- `record_llm_cost`：token 用量與估算成本正確記錄

## 非功能需求

- **NFR-1: 安全** — 不在 issue、log、測試證據、或程式碼中揭露 credential/token/secret
- **NFR-2: 成本控制** — smoke test 單次花費 < $0.01；驗證腳本具 `--dry-run` 選項
- **NFR-3: 可重現** — 證據腳本可在相同環境重複執行、結果一致
- **NFR-4: 不改動核心邏輯** — 驗證腳本是新增檔案，不修改 `bedrock.py` / `scoring.py` / `orchestrator.py` 核心邏輯
- **NFR-5: 15 分鐘窗口** — 完整驗證流程（含 pipeline 端對端）< 5 分鐘

## 驗收條件

1. Bedrock smoke test 成功且有可重現結構化證據（`out/bedrock_smoke_test.json`）
2. 成功案例的推論至少引用 5 條具體 claim_id，格式正確可追溯
3. 成功案例不出現「本次未執行線上模型生成」或其他離線降級字樣
4. 行文具有事實/推論/結論層次（非簡單摘要重述）
5. 模型不可用時管線安全降級、不偽裝線上生成成功
6. 護欄（budget/timeout/cost-record）在線上模式正常生效
7. 不在任何輸出中揭露 credential/token

## 約束

- 不引入額外第三方依賴（純 stdlib + boto3 原則）
- 不修改 `bedrock.py` / `scoring.py` / `orchestrator.py` 核心邏輯
- 不改變信任評分公式與權重
- 若需異動 secret 或 rotation，必須另取得 Eric 明確授權
- 競賽現場（8/1）公告模型後需更新 `BEDROCK_MODEL_ID`；當前使用環境變數可配置設計

## 外部依賴

- AWS IAM 權限與模型開通（`bedrock:InvokeModel` / `bedrock:Converse`）
- `AWS_REGION` 需與模型的地理 profile 相容（如 `au.` prefix 需 `ap-southeast-2/4/6`）
- 執行環境需具備有效 AWS credentials（EC2 instance role / 環境變數）
