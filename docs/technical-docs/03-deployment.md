# 03 — 部署指南

[← 02 系統架構總覽 ](02-architecture.md)[文件首頁 ](README.md)[04 環境變數參考 → ](04-configuration.md)

## 03 — 部署指南

Deployment Guide · App Runner / EC2+nginx / Lambda 三路徑完整步驟

**目錄 **

- [三條部署路徑總覽 ](#paths)

- [路徑 A：EC2 + nginx（Production 推薦） ](#ec2)

- [路徑 B：AWS App Runner ](#apprunner)

- [路徑 C：AWS Lambda Function URL ](#lambda)

- [各環境變數部署注意事項 ](#env-vars)

- [TLS / HTTPS 設定 ](#tls)

- [Blue/Green 切換 ](#cutover)

- [IAM 權限需求清單 ](#iam)

- [部署檢查清單 ](#checklist)

### 1. 三條部署路徑總覽

| 路徑 | 適用場景 | 腳本 | 優缺點 |
| --- | --- | --- | --- |
| **EC2 + nginx ** | Production、需要 TLS、需要 SPA + API 同域 | `deploy/deploy_ec2.sh ` `deploy/deploy_frontend_nginx.sh ` | 完全控制、無冷啟動、需管理 EC2 |
| **AWS App Runner ** | 快速部署、無需管理主機、自動 scale | `apprunner.yaml `（原始碼模式，從 GitHub 讀取） | 免維運、自動 TLS；容器建置需額外時間 |
| **AWS Lambda ** | 事件驅動、低流量、備援入口 | `deploy/deploy_lambda.sh ` | 按呼叫計費、冷啟動延遲；Function URL 403 gated |

### 2. 路徑 A：EC2 + nginx（Production 推薦）

**部署目標： **EC2 t3.micro @ ap-southeast-2（雪梨），公開 IP
**對外拓樸： **nginx 監聽 :80 和 :443，Python 只監聽 `127.0.0.1:8080 `

#### 2.1 後端部署（deploy_ec2.sh）

```text

# 基本部署（離線模式，$0）
REGION=ap-southeast-2 BEDROCK_MODEL_ID="" ./deploy/deploy_ec2.sh

# 含 Bedrock 啟用 + 日花費上限 $1
BEDROCK_MODEL_ID="au.anthropic.claude-sonnet-4-6" \
  TRUSTFORGE_BEDROCK_DAILY_USD_CAP=1 ./deploy/deploy_ec2.sh

# 含 DynamoDB cost ledger（多實例部署）
CACHE_BACKEND=dynamodb ./deploy/deploy_ec2.sh

```

腳本為冪等設計，會自動完成：

- 建立 / 驗證 Security Group（tcp/80 + tcp/443 公開）

- 建立 / 附加 IAM Instance Role（ `trustforge-ec2 `）

- `run-instances `（帶 `--client-token `防重複）

- 寫入 user-data（systemd unit + 環境變數 + 設時區 + 排程）

- SSM Run Command： `unzip `→ `systemctl restart trustforge `

#### 2.2 前端部署（deploy_frontend_nginx.sh）

```text

# 在前端 build 後部署
cd frontend && npm run build
cd .. && ./deploy/deploy_frontend_nginx.sh

```

前端部署腳本會：

- 安裝 / 設定 nginx（ `deploy/nginx.conf `）

- 上傳 React build（Vite `dist/ `）到 EC2

- 設定 Let's Encrypt TLS / HSTS / CSP

- 執行 cutover 切換（ `deploy/cutover_switch.sh react `）

#### 2.3 nginx 對外監聽埠（現況）

| Port | 用途 | 流量語意 |
| --- | --- | --- |
| `:80 ` | 健康檢查 + ACME challenge | /healthz 明碼直通 → 127.0.0.1:8080 /.well-known/acme-challenge/ → 本機檔案 其餘 → 301 redirect → :443 |
| `:443 ` | 主要入口（TLS + HSTS + CSP） | /assets/*、/、/analyze → React 靜態 build（SPA fallback） /api/*、/healthz → proxy_pass 127.0.0.1:8080 |

**注意： **:80 不能用 301 強制轉 :443 /healthz——負載平衡器健康檢查若只支援 HTTP 明碼，301 會讓檢查誤判服務掛掉。

### 3. 路徑 B：AWS App Runner

App Runner 使用原始碼模式，直接從 GitHub 讀取：

| 檔案 | 內容 |
| --- | --- |
| `apprunner.yaml ` | 定義 runtime（Python 3.12）、build command、start command、port（8080） |
| `Dockerfile ` | 備用容器建置（App Runner 原始碼模式優先使用 `apprunner.yaml `） |

App Runner 會自動： - 從 GitHub 拉取原始碼 - 執行 `pip install `（依 `pyproject.toml `） - 綁定 `PORT=8080 `啟動 Python HTTP server - 提供自動 TLS 終端（*.awsapprunner.com 或自訂域名） - 自動 scale（依並發請求數）

**限制： **App Runner 不支援 DynamoDB 本機路徑。若需要 DynamoDB backend（cache/cost-ledger/rate-limit），App Runner 的 instance 必須有 IAM role 允許對應 DynamoDB table 操作。

### 4. 路徑 C：AWS Lambda Function URL

```text

# 部署 Lambda
./deploy/deploy_lambda.sh

```

`lambda_handler.py `是 Lambda Function URL 入口點：

- 接收 Lambda event → 轉換為類 HTTP request object

- 重用 `web.py `管線（不另寫 API handler）

- 回傳 {statusCode, body, headers} 格式的 Lambda 回應

**目前：Function URL 為 403 gated（免費方案限制）， *不是 *公開入口。 **公開入口請用 EC2+nginx。

### 5. 各環境變數部署注意事項

詳細環境變數清單見 [03 配置參考 ](04-configuration.md)。部署時特別注意：

| 變數 | 部署方式 | 注意 |
| --- | --- | --- |
| `BEDROCK_MODEL_ID ` | user-data / systemd Environment | 空白 = 離線模式（不燒 credit） |
| `TRUSTFORGE_LIVE_TOKEN ` | **SSM Parameter Store **（不經 deploy 傳遞） | 機敏值，deploy_ec2.sh 不含此變數傳遞 |
| `TRUSTFORGE_TOKEN_SSM_PREFIX ` | deploy_ec2.sh 傳遞非機敏前綴字串 | app 啟動期自行從 SSM 取 token 值 |
| `CACHE_BACKEND ` | systemd Environment | `dynamodb `（預設）/ `json `/ `sqlite ` |
| `TRUSTFORGE_BEDROCK_DAILY_USD_CAP ` | systemd Environment | DynamoDB config store 有值時優先 |

### 6. TLS / HTTPS 設定

```text

# 設定 TLS（Let's Encrypt certbot）
./deploy/setup_tls.sh

# 自動續簽在 systemd timer：
# certbot renew --no-random-sleep-on-renew --quiet

```

詳細見 `deploy/TLS-SETUP.md `。nginx conf（ `deploy/nginx.conf `）已設定 HSTS（max-age=63072000 includeSubDomains preload）與 CSP（限制資源來源）。

### 7. Blue/Green 切換

```text

# 切換到 React 前端（當前 production）
./deploy/cutover_switch.sh react

# 切回舊版 SSR 前後端一體（緊急回滾）
./deploy/cutover_switch.sh legacy

```

`cutover_switch.sh `支援三態： `react `/ `react-http `/ `legacy `，秒切回滾。nginx symlink 原子替換，無停機時間。

### 8. IAM 權限需求清單

| 服務 | Action | 資源 | 說明 |
| --- | --- | --- | --- |
| Bedrock | `bedrock:InvokeModel ` | `arn:aws:bedrock:*::foundation-model/anthropic.* ` `arn:aws:bedrock:*:*:inference-profile/*anthropic* ` | 唯一 LLM 呼叫 |
| S3 | `s3:GetObject ` | `arn:aws:s3:::trustforge-deploy-*/* ` | 拉取部署 zip |
| SSM | `ssm:GetParameter ` | `arn:aws:ssm:ap-southeast-2:*:parameter/trustforge/runtime/* ` | 讀取 runtime tokens |
| DynamoDB | 讀寫 connector-cache / cost-ledger / rate-limit-leases / budget-guard 表 | 各表 ARN | 需建表腳本（如 `setup_budget_guard_dynamodb.sh `） |
| CloudWatch | `cloudwatch:PutMetricData ` | * | 自訂指標上報 |

### 9. 部署檢查清單

- EC2 Security Group 已開 tcp/80 + tcp/443

- 驗收確認：IAM Instance Role 已附加（ `trustforge-ec2 `）

- 驗收確認： `BEDROCK_MODEL_ID `已設定；若空白則明確標示為離線模式

- 驗收確認： `TRUSTFORGE_LIVE_TOKEN `已寫入 SSM Parameter Store，且文件不輸出 token 值

- 驗收確認：nginx 已安裝且 `deploy/nginx.conf `已套用

- 驗收確認：TLS 憑證已安裝（Let's Encrypt certbot）

- 驗收確認：systemd unit `trustforge.service `已啟用

- `scripts/trustforge_control.sh status `回傳 running

- `curl https://trustforge.hurricanesoft.com.tw/healthz `回 `ok `

- `/api/health `回 `{"ok":true,"data":{"status":"ok"}} `

- `/api/status `回 cache freshness matrix

- `/api/costs `回 cost ledger；若是離線成本狀態需明確註記

[← 02 架構總覽 ](02-architecture.md)[03 配置參考 → ](04-configuration.md)
TrustForge 技術文件 · 03 部署指南 · v0.18.5
