# 05 — API 參考

[← 04 環境變數參考 ](04-configuration.md)[文件首頁 ](README.md)[06 資料流 → ](06-data-flow.md)

## 05 — API 參考

API Reference · JSON REST 端點清單、速率限制、{ok,data,error} 信封格式、誠實設計鐵律

**目錄 **

- [統一信封格式 {ok, data, error} ](#envelope)

- [誠實設計鐵律（必讀） ](#invariants)

- [端點清單 ](#endpoints)

- [速率限制分組 ](#rate-limit)

- [/api/analyze 詳解 ](#analyze)

- [/api/status 詳解 ](#status)

- [/api/overview 詳解 ](#overview)

- [Admin API 端點 ](#admin)

- [OpenAPI Spec ](#openapi)

### 1. 統一信封格式

所有 `/api/* `端點使用固定 JSON 信封：

```text

**成功：**
{
  "ok": true,
  "data": { ... }
}

**失敗：**
{
  "ok": false,
  "error": {
    "code": "rate_limited",
    "message": "請求過於頻繁，請等待 30 秒後重試。"
  }
}

```

**message 語意 **：通用中文訊息，刻意不透傳例外 stacktrace 或 DynamoDB 錯誤細節，避免洩露內部依賴資訊給大量掃描/爬取的請求。

### 2. 誠實設計鐵律

**這些不變量是 API 設計的核心契約。任何消費此 API 的程式（前端 UI、外部 agent、監控儀表板）都必須遵守，否則會產出誤導資訊。 **

#### 2.1 缺鍵 ≠ 零（Missing key is "not evaluated", never zero or safe）

```text
API 中「沒有算過」的欄位會**完全省略該鍵**，不是填 0 或 null。
呼叫端 **不可**用 .get(key, 0) 讀取後當作數值做風險判斷。
```

#### 2.2 `manip_score `（max）才是操縱風險 primary 訊號

```text
15 筆證據只要有 1 筆已確認操縱（manipulation=1.0），平均會被稀釋——
所以 primary 訊號用 max()，不是 mean()。UI/agent 只能顯示一個數字時，一律顯示 manip_score。
```

#### 2.3 信任分是校準式打分，不是統計承諾

```text
calibrated_confidence 是簡化版分位數校準（quantile calibration），**不是**
嚴謹 conformal prediction 的覆蓋率承諾——不可把它包裝成任何形式的正確度百分比。
```

#### 2.4 三態決策（decision_state）

```text
abstain：證據不足，刻意不給方向性結論（direction 固定為 "不明"）
low_confidence：有方向性判斷，但信心偏低
normal：證據強度足以支持有信心的方向性判斷
```

### 3. 端點清單

**本輪同時比對 TrustForge `origin/main `與 production live spec。 **下表是客戶導讀版；完整欄位 schema、錯誤碼與範例仍以線上 `GET /api/openapi.yaml `為準。若 repo 最新 spec 有端點但 live spec 尚未列出，文件只能寫「repo 支援／待部署驗證」，不能當 production 驗收端點。

| 端點 | 方法 | Tag | 說明 |
| --- | --- | --- | --- |
| `/api/health ` | GET | meta | JSON 健康檢查（零 I/O）。回 `{status, version, uptime_seconds} ` |
| `/api/rate-limit-status ` | GET | observability | rate limit bucket 觀測狀態。 |
| `/api/status ` | GET | observability | 系統狀態：cache backend、資料鮮度、dedup、runtime capability。 |
| `/api/costs ` | GET | observability | 成本帳本摘要（日/月/全部，按模型分類）。 |
| `/api/overview ` | GET | observability | 多幣總覽儀表板快照。 |
| `/api/history ` | GET | observability | Point-in-time 歷史分析快照。 |
| `/api/data-plane-status ` | GET | observability | 資料平面狀態。 |
| `/api/budget-governance ` | GET | observability | 預算治理狀態。 |
| `/api/operations-status ` | GET | observability | 運維狀態摘要。 |
| `/api/analyze ` | GET | analysis | **★ 核心端點 **。完整信任加權市場分析。 |
| `/api/analysis-flow ` | GET | analysis | 當前分析 flow 狀態。 |
| `/api/analysis-snapshot ` | GET | analysis | 已發布分析快照檢索。 |
| `/api/analysis-job ` | GET | analysis | 分析 job 狀態。 |
| `/api/analysis-question ` | POST | analysis | 提交分析問題。 |
| `/api/analysis-question-context ` | GET | analysis | 問題相似度與對話上下文檢索。 |
| `/api/analysis-comparison-question ` | POST | analysis | 提交雙幣比較問題。 |
| `/api/comparison-snapshot ` | GET | analysis | 雙幣比較快照檢索。 |
| `/api/analysis-journey ` | GET | analysis | Hermes 執行旅程與 dead-letter 檢視。 |
| `/api/analysis-requeue ` | POST | analysis | 重新排隊卡住的 job。 |
| `/api/improvement-diagnostics ` | GET | diagnostics | 改善診斷。 |
| `/api/evidence-quality ` | GET | diagnostics | 證據品質摘要。 |
| `/api/delivery-status ` | GET | diagnostics | 交付狀態。 |
| `/api/memory-strategy ` | GET | diagnostics | 記憶策略狀態。 |
| `/api/alerts-operations ` | GET | diagnostics | 告警與操作狀態。 |
| `/api/intelligence-status ` | GET | diagnostics | intelligence pipeline 狀態。 |
| `/api/prompt-versions ` | GET | diagnostics | prompt 版本資訊。 |
| `/api/hermes-upgrades ` | GET | Hermes | Hermes 升級控制平面唯讀資料。 |
| `/api/openapi.yaml ` | GET | meta | OpenAPI 3.1 規格檔（讀自 `docs/api/openapi.yaml `）。 |
| `/llms.txt ` | GET | meta | AI agent / 客戶工程師一頁式契約。 |
| `/api/admin/config ` | GET / PUT | admin | 取得 / 更新 runtime config。 |
| `/api/admin/audit ` | GET | admin | Config change log。 |
| `/api/admin/hermes-upgrades ` | GET | admin | 管理端 Hermes 升級資料。 |
| `/api/admin/hermes-upgrade-sandbox ` | POST | admin | 執行 Hermes upgrade sandbox。 |
| `/api/admin/hermes-upgrade-decision ` | POST | admin | 核准／拒絕 Hermes upgrade proposal。 |

**repo 最新但本輪 production live spec 尚未列出的端點： **`/api/asset-context `、 `/api/peer-metrics `、 `/api/eco-link `、 `/api/module-telemetry `、 `/api/training-status `。本輪 live smoke 對這些 public GET 端點回 404；交付時只能說 repo 最新規格支援，不能說 production 已可用。 `/api/admin/backend-providers `live 會因缺 Admin Token 回 401，屬管理面保護端點，不列入匿名 production smoke。

### 4. 速率限制分組

三組獨立 per-IP 滑動視窗 bucket，超過門檻回 `429 {code: "rate_limited"} `：

| 組別 | 端點 | 視窗 | 上限 | 說明 |
| --- | --- | --- | --- | --- |
| **Observability ** | `/api/status `, `/api/costs `, `/api/overview `, `/api/history ` | 30s | 10 req/IP | **四個端點共用同一個 bucket **——合計 10 次/30s，不是各自 10 次 |
| **Analyze (live) ** | `/api/analyze `（ `?live=1 `） | 60s | 5 req/IP | 真 Bedrock 呼叫，緊門檻 |
| **Analyze (default) ** | `/api/analyze `（未帶 `live `/ `sample `） | 60s | 60 req/IP | 純讀 cache，成本 $0，寬鬆門檻 |
| **無限制 ** | `/api/health `, `/api/openapi.yaml `, `/llms.txt ` | — | ∞ | 零 I/O，純讀檔或純記憶體運算 |

**重要： **observability 四個端點共用同一個 per-IP bucket，key 是 IP 不分端點。agent 若同時輪詢多個端點，配額是互相扣抵的。429 回應的訊息文字內含建議等待秒數，但 **不含 `Retry-After `header **。

### 5. /api/analyze 詳解

```text

**Request:**
GET /api/analyze?coin=BTC&type=multi_source&q=BTC 現在適合進場嗎

**參數：**
  coin        = BTC | ETH | SOL | BNB | XRP
  type        = multi_source | hypothesis | comparison
  q           = 分析問題（URL-encoded）
  live=1      = 啟用真 Bedrock（需 X-Live-Token header）
  sample=1    = 離線示範模式（sample data）

**Response (200):**
{
  "ok": true,
  "data": {
    "report": {
      "coin": "BTC",
      "coin_cn": "比特幣",
      "direction": "偏多",
      "decision_state": "normal",
      "calibrated_confidence": 0.72,
      "market_judgment": "...",
      "key_basis": [...],
      "limits": [...],
      "trust_radar": {...},
      "trust_components_aggregate": {...},
      "insight_labels": [...],
      "cross_source_signal": {...}
    },
    "evidence": [ ... ],
    "execution_log": [ ... ],
    "provenance": {
      "snapshot_id": "...",
      "snapshot_at": "...",
      "run_started_at": "..."
    }
  }
}

```

### 6. /api/status 詳解

```text

**Response:**
{
  "ok": true,
  "data": {
    "version": "v0.16.18",
    "uptime_seconds": 191402.507,
    "bedrock_capable": false,
    "live_token_set": true,
    "cache_backend": {
      "name": "DynamoDBCache",
      "connected": true,
      "primary_connected": true,
      "active_backend": "DynamoDBCache",
      "degraded": false
    },
    "freshness": {
      "fresh": 96,
      "stale": 0,
      "missing": 19,
      "entries": [ ... ]
    },
    "dedup": {
      "degraded": false,
      "recent_failures": 0,
      "window_seconds": 300,
      "alert_threshold": 5
    }
  }
}

```

**dedup 監控指引 **：

- **權威訊號 **是 `dedup.degraded `，不是 server log 的 `ALERT: `前綴—— `degraded `即時反映當下滑動視窗真實計數

- **長期低頻非零也該告警 **： `alert_threshold=5 `是「突波」設計，但穩定 4 次/5min 不歸零 **不會 **觸發 degraded——需另建「 `recent_failures `是否長期非零」監控

### 7. /api/overview 詳解

```text

**Response:**
{
  "ok": true,
  "data": {
    "snapshot_id": "...",
    "snapshot_at": "...",
    "coins": [
      {
        "coin": "BTC",
        "direction": "偏多",
        "trust_score": 0.72,
        "manip_score": 0.15,
        "manip_score_mean": 0.08,
        "evidence_count": 15,
        "source_count": 5,
        "reputation_trace": {...}
      },
      ...
    ]
  }
}

```

**缺鍵語意： **`manip_score `/ `manip_score_mean `/ `reputation_trace `在沒有可用操縱訊號或動態信譽 trace 時，該鍵 **完全不回傳 **——UI 應顯示「未評估」，不可預設為 0 或安全。

### 8. Admin API 端點

| 端點 | 說明 | 認證 |
| --- | --- | --- |
| `GET /api/admin/config ` | 取得 runtime config（live_token、bedrock_enabled、daily_cap） | Admin Token |
| `PUT /api/admin/config ` | 更新 runtime config | Admin Token |
| `GET /api/admin/audit ` | Config change log（誰在何時改什麼） | Admin Token |
| `GET /api/admin/hermes-upgrades ` | Hermes 升級審核資料 | Admin Token |
| `POST /api/admin/hermes-upgrade-sandbox ` | 執行 upgrade sandbox | Admin Token |
| `POST /api/admin/hermes-upgrade-decision ` | 核准／拒絕 proposal | Admin Token |

### 9. OpenAPI Spec

完整機器可讀 API 規格位於 `docs/api/openapi.yaml `。本輪取證結果：TrustForge `origin/main `spec 為 2976 行；production live `GET /api/openapi.yaml `為 2560 行。兩者不同時，以 production live spec 作為客戶驗收依據，origin/main 多出的端點標為「repo 支援／待部署驗證」。

該規格是 `src/trustforge/web.py `內 `_handle_api_* `series handler 的 **逐一對照 **——每個欄位都對照過真實回應，不是從手寫 docs 轉寫。

可透過 `GET /api/openapi.yaml `端點在執行期取得；給 AI agent 快速讀取的雙語契約則是 `GET /llms.txt `。

[← 03 配置參考 ](04-configuration.md)[06 資料流 → ](06-data-flow.md)
TrustForge 技術文件 · 05 API 參考 · v0.18.5
