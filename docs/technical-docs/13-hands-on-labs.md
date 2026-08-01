# 13 — Hands-on Labs 實作手冊

[← 12 客戶交接總表 ](12-customer-handover.md)[文件首頁 ](README.md)[14 Troubleshooting FAQ → ](14-troubleshooting-faq.md)

## 13 — Hands-on Labs 實作手冊

Hands-on Labs · 環境檢查、AWS/SSM、部署、API smoke、成本與回滾

**目錄 **

- [Lab 0：環境檢查 ](#lab0)

- [Lab 1：AWS / IAM / SSM ](#lab1)

- [Lab 2：部署與健康檢查 ](#lab2)

- [Lab 3：API 與 UI Smoke ](#lab3)

- [Lab 4：成本、監控與回滾 ](#lab4)

- [完成條件 ](#done)

**安全提醒： **本 Lab 不要求在公開文件填入任何 secret。所有 token 只應透過客戶安全通道或 SSM Parameter Store 設定。

### Lab 0：環境檢查

#### 目標

確認客戶工程師具備 repo、AWS、CLI、Node/Python 工具。

git --version python3 --version node --version aws --version curl --version git ls-remote https://github.com/cancleeric/trustforge.git HEAD

**Expected： **所有版本命令可執行；repo 讀取成功。若 repo 失敗，先處理 GitHub 權限，不進部署。

### Lab 1：AWS / IAM / SSM

#### 目標

建立最小權限 runtime role、SSM token 位置、DynamoDB 表與 Bedrock model access。

- 確認 region： `ap-southeast-2 `。

- 確認 Bedrock Anthropic model access 已啟用。

- 建立 SSM prefix： `/trustforge/runtime/ `。

- 寫入 live/admin token 到 SSM SecureString。

- 建立 DynamoDB tables：cache、budget、rate-limit、idempotency。

aws sts get-caller-identity aws bedrock list-foundation-models --region ap-southeast-2 aws ssm get-parameter --name /trustforge/runtime/live-token --query 'Parameter.Name' --output text

### Lab 2：部署與健康檢查

#### 目標

部署 backend 到 EC2，nginx 只公開 80/443，Python 只聽 127.0.0.1:8080。

REGION=ap-southeast-2 BEDROCK_MODEL_ID="au.anthropic.claude-sonnet-4-6" TRUSTFORGE_BEDROCK_DAILY_USD_CAP=1 ./deploy/deploy_ec2.sh curl -fsS https://trustforge.hurricanesoft.com.tw/healthz

**Expected： **`/healthz `回 200；systemd service running；80 除 health/acme 外導向 443。

### Lab 3：API 與 UI Smoke

#### 目標

確認 API envelope、資料新鮮度、成本查詢與前端基本互動。

BASE=https://trustforge.hurricanesoft.com.tw curl -fsS "$BASE/api/health" curl -fsS "$BASE/api/status" curl -fsS "$BASE/api/costs"

**Expected： **JSON 使用 `{ok,data,error} `信封；若 connector stale，狀態要誠實標示 stale/partial。

### Lab 4：成本、監控與回滾

#### 目標

客戶能調整 daily cap、看 CloudWatch、停用 Bedrock、回滾前端。

# 查服務狀態 systemctl status trustforge.service journalctl -u trustforge.service -n 100 --no-pager # 回滾前端切換 ./deploy/cutover_switch.sh legacy ./deploy/cutover_switch.sh react

**Expected： **客戶知道如何在成本異常時先降 cap 或清空 `BEDROCK_MODEL_ID `，而不是讓服務繼續燒錢。

### 完成條件

- Repo / AWS / Bedrock / SSM / DynamoDB 權限確認。

- Production URL health check 200。

- API smoke 三項通過。

- 客戶可說明如何查日誌、調 cap、回滾。

- 完成 12 — 客戶交接總表簽收清單。

[排錯 FAQ ](14-troubleshooting-faq.md)[測試驗收 ](11-testing-qa.md)[交接總表 ](12-customer-handover.md)

TrustForge by HurricaneSoft（颶風軟體）· AWS Workshop-grade 技術文件
文件版本：v0.18.5 · 最後更新：2026-07-26
