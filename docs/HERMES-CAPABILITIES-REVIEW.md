# Hermes 功能、學習機制與生產啟用狀態

調查日期：2026-07-30

適用版本：生產 `v0.27.28`

生產環境：AWS `ap-southeast-2`，EC2 `trustforge-demo`

追蹤工單：#1105

本文件刻意區分三種狀態，避免把「程式碼存在」誤寫成「生產已啟用」：

- **已實作（implemented）**：repo 內已有執行路徑。
- **已啟用（enabled）**：生產服務的旗標與控制面允許執行。
- **已觀測（observed）**：可由正式 API、systemd 或持久化產物證明真的跑過。

---

## 一、結論摘要

| 能力 | 已實作 | 生產已啟用 | 生產已觀測 | 2026-07-30 判定 |
|------|:------:|:----------:|:----------:|----------------|
| Hermes 30 分鐘自主循環 | ✅ | ✅ | ✅ | 最近一次成功完成，約 127 秒 |
| 即時 Analysis Flow | ✅ | ✅ | ✅ | daemon active，API 顯示 continuous |
| AWS Bedrock 推理 | ✅ | ✅ | ✅ | Haiku 4.5 累計有非零 token 與成本 |
| 歷史 Training JSONL | ✅ | — | ✅ | 5 幣共 2,005 筆，來源為離線回填 |
| 即時分析寫入 Training JSONL | ❌ | ❌ | ❌ | 設計上不直接寫舊 training dataset |
| Three-track learning events | ✅ | ❌ | ❌ | hook 已接線，正式旗標未設定 |
| AGOS skill/memory lineage | ✅ | ❌ | ❌ | runtime 已接線，正式旗標未設定 |
| Research snapshot memory | ✅ | ✅ | ✅ | 563 份，覆蓋 6 幣 |
| Backfill daemon | ✅ | ❌ | ❌ | 正式 service inactive，無 timer |
| Training status API | ✅ | ✅ | ⚠️ | 正式 API 回 0，未讀到既有 2,005 筆 |

最重要的邊界：

1. Bedrock 模型有啟用且確實呼叫過，不代表每一個 run 都使用模型；近期大量自動 run 仍是 `offline: true`。
2. `data/training/*.jsonl`、three-track learning events、research snapshots、AGOS memory 是四種不同資料，不可混稱「memory」或「training data」。
3. 生產即時分析目前沒有形成「prediction → delayed outcome → versioned dataset」的持續學習閉環。

---

## 二、校準模型訓練機制

### 2.1 模型與用途

校準器採 Isotonic Regression（等序回歸），將 Agent 的 raw confidence 映射為更接近歷史命中率的 calibrated confidence。它不是 Hermes 的生成模型，也不會取代 Bedrock。

### 2.2 舊版離線資料流

```text
BackfillWorker._persist_to_training_data()
  → data/training/{COIN}.jsonl
  → enrich_training_data_with_ground_truth()（T+7 OHLCV）
  → retrain_calibrator()
  → data/model-artifacts/calibration-model.json
```

`BackfillWorker` 會寫歷史 training JSONL，但不會寫 `TrustFeatureStore`。`TrustFeatureStore` 是即時 `analysis_flow` 在 report-delivery 完成時寫入。

### 2.3 Training Data 格式

```json
{
  "date": "2022-06-19",
  "coin": "BTC",
  "direction": "bullish",
  "trust_score": 0.5969,
  "confidence": 0.2862,
  "evidence_count": 9,
  "sources": ["blockchain-com-charts", "ohlcv-official"],
  "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
  "generated_at": "2026-07-21T12:26:26Z",
  "outcome_pct": 2.26,
  "ground_truth_direction": "neutral",
  "split": "train"
}
```

`model_id` 描述該筆 backfill 是否使用模型；它不等於目前正式服務使用的模型。正式服務目前設定為 `au.anthropic.claude-haiku-4-5-20251001-v1:0`。

### 2.4 Repo／正式機既有資料量

| 幣種 | 筆數 |
|------|-----:|
| BTC | 307 |
| ETH | 489 |
| SOL | 403 |
| BNB | 403 |
| XRP | 403 |
| **合計** | **2,005** |

校準模型產物：

- `data/model-artifacts/calibration-model.json`：1,980 筆樣本、2 個校準點，訓練時間 `2026-07-21T16:04:33Z`。
- `data/model-artifacts/calibration_report.json`：校準品質報告。
- `data/model-artifacts/source_reputation_v1.json`：來源信譽排名。

正式機檔案位於 `/opt/trustforge/data/training/`。檔案部署時間不是資料生成時間；BTC 最後可見資料日期為 `2024-07-02`，相鄰記錄的 `generated_at` 約為 `2026-07-21T15:38Z`，且 `model_id: null` 表示離線生成。

### 2.5 訓練入口

| 入口 | 位置 |
|------|------|
| 獨立腳本 | `scripts/retrain_calibrator.py` |
| CLI | `trustforge train-calibration` |
| 語義回填後重訓 | `scripts/run_semantic_backfill.py → retrain_calibrator()` |

正式環境沒有啟用 backfill service 或 training/backfill timer，因此這些入口目前不是持續運行的生產訓練流程。

---

## 三、生產即時分析與資料去向

### 3.1 Analysis Flow 完成路徑

即時 `analysis_flow` 完成 report-delivery 後會：

- 寫入 `analysis_results`，供 API／前端讀取。
- 寫入 `TrustFeatureStore` 的 `analysis_trust.v1` 特徵。
- 寫入 conversation、execution lineage 與結果 provenance。
- 將真實模型呼叫寫入 cost ledger，供預算閘門使用。
- 呼叫 three-track success/failure hook；但只有旗標開啟時才會持久化 learning event。

它不會呼叫 `BackfillWorker._persist_to_training_data()`，也不會直接 append `data/training/*.jsonl`。

### 3.2 四條不可混淆的資料路徑

| 資料 | 寫入者 | 用途 | 生產現況 |
|------|--------|------|----------|
| `data/training/*.jsonl` | BackfillWorker／語義回填 | 舊校準訓練集 | 有歷史檔案，沒有持續新增 |
| `TrustFeatureStore` | 即時 Analysis Flow | 線上查詢與特徵保存 | 正常寫入 |
| Research snapshots | Hermes cycle／fetch scheduler | 跨 run 研究記憶、PIT 證據 | 正常寫入，563 份 |
| Learning events | Three-track hook | 不可變 prediction／quality event | 程式已接線，生產未啟用 |
| AGOS memory/skill/tool lineage | AGOS runtime | context、skill revision、memory retrieval、tool audit | 程式已接線，生產未啟用 |

### 3.3 Three-track learning

`analysis_flow._worker()` 已在 durable completed／dead-letter 狀態落地後呼叫：

- `emit_for_completed_job()`
- `emit_for_failed_job()`

此路徑受 `TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED` 控制，預設關閉並 fail-soft。2026-07-30 正式 service 未設定此旗標，正式機亦未觀測到 learning event 產物。

因此正確結論不是「即時 pipeline 完全沒有 learning persist 程式」，而是：

> learning persist hook 已實作，但正式環境尚未啟用；舊 training JSONL 也不由即時 pipeline 寫入。

### 3.4 AGOS skill／memory

Agent OS runtime 已在 Analysis Flow 接入：

- context manifest
- frozen skill manifest
- memory retrieval lineage
- tool invocation audit

它受 `TRUSTFORGE_AGOS_ENABLED=1` 控制，預設關閉。正式 service 未設定此旗標，所以目前不能把 AGOS 管理 API 或 repo 內 registry 程式視為「生產已動用」。

### 3.5 Training Status 可觀測缺口

正式 `GET /api/training-status` 在 2026-07-30 回報：

```json
{
  "training_data": {
    "total_records": 0,
    "per_coin": {}
  },
  "upgrade_threshold": {
    "current": 0,
    "met": false
  }
}
```

但正式機 `/opt/trustforge/data/training/` 實際存在 2,005 筆。API 依目前 release package 路徑尋找 `data/training`，沒有讀到共享資料目錄。這是正式觀測面與實體資料不一致的缺口；在修正前，不能以該 API 的 0 筆判定檔案不存在，也不能宣稱 training dashboard 正常。

---

## 四、Bedrock 模型能力

### 4.1 唯一模型入口

`src/trustforge/bedrock.py` 是 TrustForge 的 AWS Bedrock runtime 入口。正式站狀態：

- `bedrock_capable: true`
- 正式模型：`au.anthropic.claude-haiku-4-5-20251001-v1:0`
- 已觀測累計輸入 83,794 tokens
- 已觀測累計輸出 32,691 tokens
- 已觀測累計成本約 USD 0.247249

以上證明模型不是只有設定，確實曾被呼叫。

### 4.2 模型不是每次都執行

每個 run 還會受到模式、每日預算、模型定價與 online-stance 限流控制：

- `llm_mode=off`：不跑敘事模型。
- `llm_mode=bedrock`：通過成本與模型閘門後才呼叫 Bedrock。
- 預算或未定價模型閘門命中時會降級 offline。
- 正式近期大量自動 run 的 ledger 為 `offline: true`、token 為 0。

因此文件與 UI 應使用「Bedrock capable／本次是否實際使用」兩個欄位，不可只看 `BEDROCK_MODEL_ID`。

---

## 五、Hermes Agent 能力

### 5.1 Agent 工具

| 工具 | 用途 | 模式 | 每循環上限 |
|------|------|------|-----------:|
| `refresh_sources` | 刷新限定來源到帶時戳快取 | autonomous | 1 |
| `archive_source_snapshot` | 保存 published/fetched/snapshot 時間 | autonomous | 5 |
| `build_snapshots` | 從快取建立各幣信任快照 | autonomous | 1 |
| `cache_freshness_dashboard` | 發布新鮮度、缺口與排程健康 | autonomous | 1 |
| `measure_connector_reliability` | 量測來源失敗率與成功閘門 | autonomous | 1 |
| `measure_quality` | 執行有界離線 regression/replay | autonomous | 1 |
| `read_snapshot` | 讀取正式 run 開始前的快照 | formal | 5 |
| `replay_history` | 歷史決策對接後續 OHLCV | offline | 5 |
| `diagnose_improvement` | 將量測轉為待批准實驗 | autonomous | 1 |
| `review_upgrades` | Bedrock 對抗審查候選，不批准部署 | autonomous | 1 |
| `extract_claims` | 從選定證據抽取結構化主張 | formal | 1 |
| `classify_stance` | 有界語義立場分類 | formal | 1 |
| `assemble_report` | 將 pipeline 結果加引文敘事化 | formal | 1 |
| `export_deliverables` | 匯出報告、Evidence 與 execution log | formal | 1 |

### 5.2 Agent 技能約束

| Skill | 不可變規則 |
|-------|------------|
| `five-year-ohlcv-lineage` | 每個價格事實附安全檔名、SHA-256、覆蓋範圍與分析窗口 |
| `evidence-contract` | 每個結論連回 source、fetched_at、content_reference、related_claim |
| `contrarian-evidence` | 保留矛盾及低信任證據，不得靜默丟棄 |
| `report-contract` | 報告包含判斷、依據、校準信心、限制與反轉條件 |
| `bounded-self-improvement` | 只提出可量測實驗，生產變更必須人審 |

這五項是 Hermes manifest 的穩定約束；它們不同於 AGOS runtime 的 frozen skill revision。前者已在 manifest／自主計畫中使用，後者正式旗標尚未開啟。

### 5.3 自我改善迴圈

```text
measure_quality + measure_connector_reliability + replay_history
  → diagnose_improvement
  → out/hermes-improvement-latest.json
  → review_upgrades（Bedrock 對抗審查）
  → out/hermes-upgrade-review-latest.json
  → 人工批准後才可變更生產
```

自主循環只會量測、診斷和提出候選，不會自行修改程式、批准、merge 或部署。

---

## 六、生產服務與控制面

### 6.1 2026-07-30 正式驗證

| 元件 | 正式狀態 | 證據 |
|------|----------|------|
| `hermes-cycle.timer` | enabled、active | 每 30 分鐘，最近一次 service success |
| `hermes-cycle.service` | oneshot 正常 | 最近完成約 127 秒，exit 0 |
| `trustforge-analysis-flow.service` | active | `/api/analysis-flow` state=`continuous` |
| `fetch-scheduler.timer` | inactive | 資料仍可由 Hermes cycle／其他正式排程刷新 |
| `trustforge-backfill.service` | inactive | 無 backfill/training timer |

正式 `/api/analysis-flow` 另觀測到 dead-letter 15 筆；這代表 daemon 正常公開失敗狀態，不代表所有分析都成功。

### 6.2 Autonomy 控制優先序

實際 `autonomy_enabled()` 順序：

1. `runtime_control()` 的明確 stop／production guard
2. DynamoDB admin config `hermes_autonomy_enabled`
3. `TRUSTFORGE_HERMES_AUTONOMY_ENABLED`
4. production default（fail-closed）

正式 systemd 目前有 `TRUSTFORGE_HERMES_AUTONOMY_ENABLED=1`，且 timer 最近一輪確實完成。管理面 config 若明確設定，優先於 autonomy env；緊急 runtime stop／production guard 優先於兩者。

### 6.3 公開觀測端點

| 端點 | 用途 | 2026-07-30 狀態 |
|------|------|----------------|
| `GET /api/status` | Bedrock、快取與來源狀態 | 正常 |
| `GET /api/costs` | 模型 token、成本與 run 模式 | 正常 |
| `GET /api/analysis-flow` | daemon、queue、stage、dead letter | 正常 |
| `GET /api/memory-strategy` | snapshot memory 策略與統計 | 正常 |
| `GET /api/training-status` | 舊 training JSONL 統計 | 可回應但資料路徑不一致，回 0 |
| `GET /api/intelligence-status` | Hermes tools／skills／智能模組 | 正常 |

---

## 七、建議處理順序

1. 修正 `/api/training-status` 讀取正式共享 training 目錄的路徑，先恢復可觀測真實性。
2. 另開安全審查工單，在備份、容量、敏感資料與 rollback 驗證後才啟用 three-track learning。
3. 為 learning events 建立 delayed outcome labeler 排程，再產生版本化 dataset；不要直接把未標註即時輸出 append 到舊 training JSONL。
4. AGOS 另案啟用，驗證 frozen skills、memory retrieval 與 tool lineage；不要與 training collection 綁成同一次切換。
5. 校準資料採時間切分並保留 PIT provenance，避免 future leakage 與模型自我訓練污染。

本次 #1105 僅校正文件與驗證生產現況，不變更正式旗標、資料庫 schema 或訓練排程。
