# 00 — Evidence Map 真實佐證矩陣

[文件首頁 ](README.md)[01 Workshop 等級導覽 → ](01-workshop-overview.md)

## 00 — Evidence Map 真實佐證矩陣

Evidence Map · 每個技術主張對應 repo 檔案、線上 smoke 或明確邊界

Evidence-first, better than generic workshop docs

## 每個交付主張都要能追到 repo 檔案或線上結果

本頁是 TrustForge 技術文件的真實性索引。凡是沒有 repo 佐證或本輪線上驗證的能力，一律標成「支援／待驗證」，不寫成「已部署」。

專案 HEAD： `bdfaf5e `latest tag： `v0.24.0 `文件版本： `v0.18.5 `驗證時間：2026-07-26 14:20 Asia/Taipei

**目錄 **

- [線上驗證結果 ](#live)

- [repo 佐證矩陣 ](#repo)

- [可量化專案規模 ](#counts)

- [文件真實性規則 ](#truth-rules)

- [交付前 5 分鐘重驗 ](#reverify)

- [不可誇大的邊界 ](#gaps)

### 1. 線上驗證結果

| 檢查 | 結果 | 可寫入文件的結論 |
| --- | --- | --- |
| `/healthz ` | HTTP 200；body `ok ` | production health endpoint 可用 |
| `/api/health ` | HTTP 200； `{"ok":true} `；version `v0.16.18 ` | Backend API 可用 ；注意線上 runtime 版號與文件版號不同。 |
| `/api/status ` | HTTP 200； `DynamoDBCache connected=true `； `bedrock_capable=false `； `live_token_set=true ` | DynamoDB cache 已連線 ； Bedrock live 目前關閉 。 |
| `/api/costs ` | HTTP 200； `total_cost_usd=0.0 `； `offline `model；run_count 5500 | 成本帳本 API 可讀 ；目前線上是 credit-safe/offline 成本狀態。 |

### 2. repo 佐證矩陣

| 能力 | repo 檔案 | 文件措辭規則 |
| --- | --- | --- |
| Bedrock 唯一模型入口 | `src/trustforge/bedrock.py`、`pipeline.py`、`agent/orchestrator.py` | 可寫「程式支援／集中入口」；只有 `bedrock_capable=true` 才能寫 live enabled。 |
| Competition Lambda / RPS / provider cache | `src/trustforge/lambda_handler.py`、`lambda_provider_cache.py`、`lambda_secret.py`、`deploy/competition-lambda-live-contract.json` | 可寫 Lambda contract / distributed Bedrock RPS / provider secret boundary 已有程式與測試；若未讀 AWS Console，不寫「production 已啟用」。 |
| 台灣監管來源 adapters | `src/trustforge/ingestion/taiwan_regulatory.py`、`tw_datetime.py`、`safe_fetch.py` | 可寫 FSC / MOPS / TWSE / TPEx adapter 已接；BlockTempo 仍是待辦。 |
| 外部來源主線 | `ingestion/whale_trades.py`、`etherscan.py`、`cmc.py`、`defillama.py` | 可寫 Whale Alert + Arkham、Etherscan、CoinMarketCap、DefiLlama 已有 connector；key-based source 未配置時降級，不寫成 live 成功。 |
| EC2/nginx/TLS 部署 | `deploy/deploy_ec2.sh`、`deploy/nginx.conf`、`deploy/setup_tls.sh`、`deploy/cutover_switch.sh` | 可寫 production 路徑與回滾腳本存在；線上 health 需重驗後才寫已驗證。 |
| DynamoDB cache / ledger / budget / lease | `ingestion/cache.py`、`ledger.py`、`budget_counter.py`、`rate_limit_store.py`、`idempotency_lease.py` | cache 線上 connected 可寫已驗證；其他 store 若未讀線上表，不寫「全部已啟用」。 |
| SSM token / credential boundary | `ssm_params.py`、`whale_alert_secret.py`、`cmc_secret.py`、`etherscan_secret.py`、`deploy/put_runtime_tokens.sh` | 可寫 token 不進 repo／不進公開文件；不要輸出 token 值。 |
| Hermes production audit / signed evidence | `hermes_audit.py`、`hermes_audit_contracts.py`、`hermes_audit_signing.py`、`scripts/hermes_production_audit.py` | 可寫有 production audit 與簽章證據鏈；若未跑 live audit，只寫 repo 支援。 |
| API envelope / endpoints | `web.py`、`docs/api/openapi.yaml`、`frontend/src/lib/endpoints.ts` | 可寫 API 端點與 `{ok,data,error}`，並以 smoke 結果佐證。 |
| 前端 SPA / 展示 UI | `frontend/src/App.tsx`、`frontend/src/pages/*.tsx`、`frontend/src/hermes/*`、`frontend/package.json` | 可寫 React/Vite 技術棧、右欄固定、圖表化／摺疊；UI 狀態需用 browser eye scan 驗證。 |
| 本機品質 gate | `.githooks/pre-push`、`pyproject.toml`、`frontend/package.json`、`scripts/security_gate_push.py` | 可寫 gate 包含 backend tests、data contracts、stub scan、question bank、frontend test/lint/build、diff check。 |
| Release governance | `docs/governance/PRE_PUSH_RELEASE_GATES.md`、`docs/RELEASE-DEPLOY-GOVERNANCE.md` | 可寫目前 release/deploy gate 是 controlled local process；不要寫 GitHub Actions 是 production release gate。 |
| AI/agent handoff contract | `llms.txt`、`frontend/public/llms.txt`、`docs/api/openapi.yaml` | 可寫有機器可讀的一頁式契約與 OpenAPI；交付前需 live curl 確認端點有部署。 |

### 3. 可量化專案規模

#### 1,837 個 git 追蹤檔

以目前 `develop` 的 `git ls-files` 計算。

#### 約 369,717 行程式／設定／文件

統計常見程式與設定副檔名（Python / TS / TSX / JS / JSON / YAML / shell / HTML / CSS / Rust），排除 `.git`、`.venv`、`node_modules`、build / cache 目錄。

#### 394 個後端測試檔、6,259 個 test functions

以 `tests/test_*.py` 與 AST 掃描計算；實際完整 gate 仍以 `.githooks/pre-push` 為準。

### 4. 文件真實性規則

- **已線上驗證 **：必須有 live curl/browser 結果，例如 `/api/status `。

- **程式支援 **：只有 repo 檔案／測試佐證，但本輪未驗線上。

- **待客戶設定 **：需要客戶 AWS/GitHub/domain/token 才能成立。

- **不可寫 **：沒有 repo 檔案、沒有 live 結果、只是設計想法。

### 5. 交付前 5 分鐘重驗

這一段是客戶會議前最後一次 reality check；只讀、不含 secret、不會觸發 Bedrock 成本。

BASE=https://trustforge.hurricanesoft.com.tw curl -fsS "$BASE/healthz" curl -fsS "$BASE/api/health" curl -fsS "$BASE/api/status" | python -m json.tool curl -fsS "$BASE/api/costs" | python -m json.tool curl -fsS "$BASE/llms.txt" | head -40 curl -fsS "$BASE/api/openapi.yaml" | head -40

- `/api/status `若仍顯示 `bedrock_capable=false `，簡報與文件只能說「Bedrock 程式支援、目前線上關閉」。

- `/api/costs `若顯示 offline / 0 cost，不能把它講成 live LLM 成本實測。

- `/llms.txt `與 `/api/openapi.yaml `是給客戶工程師與 AI agent 的快速契約，若 404，交付前要先修部署。

- 本輪比對發現 source `origin/main `OpenAPI 為 2976 行、production live OpenAPI 為 2560 行；多出的 repo endpoint 只能標示為「repo 支援／待部署驗證」，不能列為 production 驗收通過。

### 6. 不可誇大的邊界

- 目前線上 `bedrock_capable=false `，所以不能說 production live Bedrock 已開；只能說程式與部署參數支援。

- 線上 API version 是 `v0.16.18 `，文件版本是 `v0.18.5 `；兩者語意不同，不能混為產品 runtime 版號。

- CloudWatch alarms、Lambda Function URL、本輪未讀 AWS Console；文件只能標 repo 支援或 gated 備援。

- 主 repo 本機工作樹有既有未提交變更；本輪用 `git fetch --all --prune `讀 `origin/main `取證，未 checkout / pull，避免覆蓋訓練資料。

[Workshop 導覽 ](01-workshop-overview.md)[Hands-on Labs ](13-hands-on-labs.md)[排錯 FAQ ](14-troubleshooting-faq.md)[客戶交接總表 ](12-customer-handover.md)

TrustForge by HurricaneSoft（颶風軟體）· Evidence-first Workshop-grade 技術文件
文件版本：v0.18.5 · 最後更新：2026-07-26
