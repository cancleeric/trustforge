# 15 — 使用者手冊

[← 14 排錯 FAQ ](14-troubleshooting-faq.md)[文件首頁 ](README.md)[Evidence Map ](00-evidence-map.md)

## 15 — 使用者手冊

User Manual · 給一般使用者、客戶窗口、評審與非工程角色的操作指南

Evidence-first user guide

## 先會用，再深入看技術文件

本手冊只描述 TrustForge 目前可由 repo 或 production live smoke 佐證的使用方式。需要 token、Admin Token 或尚未在 live API 開放的能力，會明確標示限制，不把「repo 支援」寫成「production 已可用」。

Production： `https://trustforge.hurricanesoft.com.tw `live health： `/api/health 200 `目前 Bedrock live： `bedrock_capable=false `

**目錄 **

- [適用對象 ](#who)

- [使用前確認 ](#before)

- [功能地圖 ](#map)

- [日常使用流程 ](#daily)

- [各頁操作說明 ](#pages)

- [如何正確解讀結果 ](#truth)

- [限制與不可誤用事項 ](#limits)

- [回報問題時要附什麼 ](#support)

### 1. 適用對象

#### 一般使用者

查看市場信任分、方向、操縱風險、歷史趨勢與成本狀態。

#### 客戶窗口／主管

用 Dashboard、Status、Costs 與 History 判斷系統是否可用、資料是否新鮮。

#### 分析師／評審

查看 Analyze、Compare、Evidence 與限制說明，避免把信任分誤當投資保證。

#### 管理者

需要 Admin Token 才能進行 runtime config 或管理面操作；本手冊不揭露任何 token。

### 2. 使用前確認

- 開啟 [TrustForge production site ](https://trustforge.hurricanesoft.com.tw/)。

- 若頁面無法載入，先檢查 `/api/health `是否正常；本輪 live smoke 回 200 。

- 若分析結果看起來過舊，先到 [Status ](https://trustforge.hurricanesoft.com.tw/status)看 freshness，不要直接假設模型失準。

- 若要觸發 live Bedrock 分析，需要額外 Live Token；目前 production `/api/status `顯示 bedrock_capable=false ，所以一般使用流程以已發布 snapshot / cache / offline-safe 資料為主。

### 3. 功能地圖

| 入口 | 用途 | 目前佐證 | 注意事項 |
| --- | --- | --- | --- |
| `/ ` | Hermes 旗艦 Dashboard／總覽入口 | SPA route live 200；source route 存在 | 首頁已取代舊 `/home `， `/home `會 redirect。 |
| `/analyze ` | 單幣分析報告 | `/api/analyze `與 snapshot flow 在 source/live API 中存在 | live 模式需要 token；不要把低信心結果當交易指令。 |
| `/compare ` | 雙幣比較 | source route 存在；API 使用 comparison contract | 比較結果是分析輔助，不是投資建議。 |
| `/history ` | 歷史趨勢 | `/api/history `live 存在 | 呼叫 API 時必須帶合法幣種，例如 BTC / ETH / SOL / BNB / XRP。 |
| `/status ` | 系統狀態、cache backend、資料鮮度 | `/api/status `live 200 | 先看 freshness 與 cache backend，再判斷分析新鮮度。 |
| `/costs ` | 成本帳本 | `/api/costs `live 200 | 本輪 live 顯示 offline / 0 cost，不能講成 live LLM 成本實測。 |
| `/help ` | Help Center | source route 與 live SPA route 存在 | 適合先查名詞、操作與常見問題。 |
| `/admin ` | 管理控制台 | source route 存在；admin API 需 token | 未授權會 fail closed，不要把 token 放 URL 或文件。 |

**repo 支援／待部署驗證： **`/asset-context `、 `/eco-link `、 `/peer-metrics `前端 route live 可載入 SPA，但本輪 production public API 對應 `/api/asset-context `、 `/api/eco-link `、 `/api/peer-metrics `仍回 404；使用者手冊只能寫成「頁面入口存在、後端 production API 待部署驗證」。

### 4. 日常使用流程

- **先看 Dashboard： **確認目前幣種總覽、整體狀態與可用入口。

- **再看 Status： **確認 cache backend、freshness、Bedrock capability 與 dedup 狀態。

- **需要單幣觀點時進 Analyze： **選幣種、閱讀方向、信任分、關鍵依據與限制。

- **需要對照時進 Compare： **比較兩個幣種，不把單一分數當唯一依據。

- **需要趨勢時進 History： **看 point-in-time 走勢，確認資料時間點。

- **需要成本狀態時進 Costs： **確認目前是否有 live LLM 成本與模型分佈。

- **看不懂名詞或錯誤時進 Help / FAQ： **先查說明，再回報問題。

### 5. 各頁操作說明

#### 5.1 Dashboard（ `/ `）

- 用途：作為一般使用者的主入口，查看 TrustForge / Hermes 目前提供的模組與狀態。

- 看到異常時：不要直接重整多次；先前往 `/status `看 API 與 freshness。

#### 5.2 Analyze（ `/analyze `）

- 用途：閱讀單一幣種的分析報告、方向、信任分、Evidence 與限制。

- 正確讀法：先看 **decision_state **；若是 `abstain `或低信心，不應視為明確方向。

- live token：若沒有 Live Token，系統不應觸發真 Bedrock live 分析。

#### 5.3 Compare（ `/compare `）

- 用途：雙幣比較與相對風險閱讀。

- 正確讀法：比較是輔助判斷，不代表任一幣種必然優於另一幣種。

#### 5.4 History（ `/history `）

- 用途：查看幣種歷史信任分與 snapshot 趨勢。

- API 限制：合法幣種包含 BTC、ETH、SOL、BNB、XRP；缺少幣種參數時 API 會回 bad request。

#### 5.5 Status（ `/status `）

- 用途：確認 backend version、cache backend、freshness、Bedrock capability、dedup。

- 本輪 live 狀態： `DynamoDBCache connected=true `、 `bedrock_capable=false `、 `live_token_set=true `。

#### 5.6 Costs（ `/costs `）

- 用途：查看成本帳本摘要。

- 本輪 live 狀態： `total_cost_usd=0.0 `且模型分類為 offline；只能代表目前查到的成本帳本狀態。

#### 5.7 Admin（ `/admin `）

- 用途：runtime config 與管理操作。

- 限制：需要 Admin Token；token 不能貼在截圖、文件、聊天或 URL query。

### 6. 如何正確解讀結果

| 欄位／概念 | 正確解讀 | 不要這樣說 |
| --- | --- | --- |
| TrustScore | 信任加權後的分析分數，用來輔助排序與閱讀。 | 不要說成「準確率」或「保證上漲」。 |
| calibrated_confidence | 校準式信心參考。 | 不要包裝成嚴格統計覆蓋率承諾。 |
| decision_state | 判斷狀態； `abstain `代表證據不足。 | 不要把 abstain 解讀成看空或看多。 |
| Evidence | 可追溯依據清單。 | 不要只截結論，不附 Evidence 與限制。 |
| bedrock_capable | live LLM capability 旗標。 | 目前為 false 時，不要宣稱 production live Bedrock 已開。 |

### 7. 限制與不可誤用事項

- TrustForge 是信任情報與分析輔助系統，不是投資顧問，也不是下單系統。

- 沒有 Evidence 或 freshness 不足時，要標示「未評估／資料不足」，不能補 0 當安全。

- 成本顯示 offline / 0 cost 時，只能代表目前帳本查詢結果，不代表所有歷史成本都為 0。

- Admin Token、Live Token、AWS credentials 不得貼進使用者手冊或客服訊息。

- repo 最新但 production API 尚未部署的功能，不得列為客戶已驗收功能。

### 8. 回報問題時要附什麼

**最小回報格式： **
1. 使用頁面 URL；2. 操作步驟；3. 預期結果；4. 實際結果；5. 時間；6. 畫面截圖；7. 若可取得，附 `/api/health `與 `/api/status `的非機敏摘要。

```text
範例：
頁面：https://trustforge.hurricanesoft.com.tw/status
時間：2026-07-26 17:20 Asia/Taipei
操作：開啟 Status 頁後等待 10 秒
預期：顯示 cache backend 與 freshness
實際：freshness 區塊空白
補充：/api/health 200；/api/status 200；未附任何 token
```

[看真實佐證矩陣 ](00-evidence-map.md)[查 API 參考 ](05-api.md)[排錯 FAQ ](14-troubleshooting-faq.md)[客戶交接總表 ](12-customer-handover.md)

TrustForge by HurricaneSoft（颶風軟體）· 使用者手冊
文件版本：v0.18.5 · 最後更新：2026-07-26
