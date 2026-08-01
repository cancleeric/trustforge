# 11 — 測試、QA 與驗收

[← 10 安全 ](10-security-handover.md)[文件首頁 ](README.md)[12 客戶交接總表 → ](12-customer-handover.md)

## 11 — 測試、QA 與驗收

Testing & QA · backend/frontend gates、smoke、acceptance criteria

**目錄 **

- [測試策略 ](#strategy)

- [後端測試 ](#backend)

- [前端測試 ](#frontend)

- [Production Smoke ](#smoke)

- [客戶驗收準則 ](#acceptance)

- [已知邊界 ](#known)

### 1. 測試策略

TrustForge 的驗證分成五層：靜態檢查、單元測試、整合測試、前端建置／互動測試、production smoke。交接時以「可重跑、可解讀、可追溯」為準。

| 層級 | 目的 | 典型命令 | 通過條件 |
| --- | --- | --- | --- |
| Static | 格式、型別、trailing whitespace | `git diff --check ` | 無 whitespace error |
| Backend unit | TrustScore、cache、rate limit、budget guard | `env PYTHONPATH=src python -m pytest -q ` | 測試通過且 coverage ≥ 75% |
| Integration | API envelope、Bedrock fallback、DynamoDB store、data contract | `python scripts/check_data_contracts.py ` | 不依賴真 secret；可用 mock/local backend |
| Release gate | stub scan、question bank、diff check | `.githooks/pre-push `（含 `scripts/scan_source_stubs.py `、 `scripts/run_question_bank.py `、 `git diff --check `） | local gate 全部通過；GitHub Actions 不是 release gate |
| Frontend | React test/lint/build、型別與 UI smoke | `npm test -- --run && npm run lint && npm run build ` | 測試、lint、TypeScript + Vite build 成功 |
| Production smoke | 線上健康、API、成本狀態 | `curl /healthz ` | HTTP 200 且 JSON envelope 正常 |

### 2. 後端測試

python -m venv .venv . .venv/bin/activate python -m pip install -e '.[dev]' python -m pytest --cov=trustforge --cov-fail-under=75

- 核心測試重點：信任分數公式、來源佐證、操縱懲罰、成本預留、idempotency lease。

- Bedrock 真實呼叫不應是預設測試前提；需要額外 smoke token 與預算 cap。

- 所有外部來源 connector 應支援 cache/mock 模式。

### 3. 前端測試

cd frontend npm ci npm run build npm run test -- --run

前端驗收重點：Analyze composer、報告卡片、來源引用、錯誤／429 顯示、手機版版面。

### 4. Production Smoke

BASE=https://trustforge.hurricanesoft.com.tw curl -fsS "$BASE/healthz" curl -fsS "$BASE/api/health" curl -fsS "$BASE/api/status" curl -fsS "$BASE/api/costs" curl -fsS "$BASE/llms.txt" | head -40 curl -fsS "$BASE/api/openapi.yaml" | head -40

**注意： **若 smoke 需要 live analyze，必須先確認 daily cap 與 token，避免交接測試造成額外 Bedrock 成本。

### 5. 客戶驗收準則

- 文件首頁能引導客戶找到部署、API、運維、安全與測試。

- 後端測試報告與 coverage 門檻可重跑。

- 前端 build 可重跑並產出可部署 dist。

- production smoke 在客戶環境回 200，且 `/llms.txt `、 `/api/openapi.yaml `可讀。

- Bedrock 成本上限已設定，超額時拒絕請求而非繼續燒錢。

### 6. 已知邊界

- 公開技術文件不承諾包含私有 repo 的所有最新 commit；交接前需以客戶可存取 repo 的 HEAD 再跑一次驗收。

- Live token、AWS secret、GitHub token 不放文件，交接時另走安全通道。

[API 參考 ](05-api.md)[前端架構 ](09-frontend.md)[安全交接 ](10-security-handover.md)[交接總表 ](12-customer-handover.md)

TrustForge by HurricaneSoft（颶風軟體）· 技術文件區 · 客戶交接版
文件版本：v0.18.5 · 最後更新：2026-07-26
