# TrustForge — AWS 架構（決賽簡報用）

> **版本**：真實已部署架構（2026-06）
> **競賽硬規則**：僅限 AWS 基礎模型 → 全程走 Amazon Bedrock，不呼叫任何其他供應商
> **公開主機**：EC2 t3.micro @ ap-southeast-2（雪梨）
> ⚠️ 下方架構圖／服務對應表的「公開入口」段落寫於 nginx cutover 之前
> （2026-06，`trustforge.web` 直接聽 port 80）。v0.6.1 起實際拓樸已改為
> nginx 在前（:80 明碼 + :443 TLS 兩個公開 listener，見下）、python 收斂
> 只聽 `127.0.0.1:8080`，見「## 前後端分離對外拓樸」一節（Issue #81 定案，
> 2026-07-06）——此節為目前真實現況，上方圖表為歷史部署基礎
> （EC2/Bedrock/S3/IAM/SSM 本身沒變，只是多了 nginx 這層公開入口 + 拆出
> React 靜態資產）。

---

## 架構圖

```mermaid
flowchart TB
    User(["使用者 / 評審"])

    subgraph AWS["AWS  ap-southeast-2  Sydney"]
        subgraph EC2Block["EC2 t3.micro  |  SG: TCP 80 public  |  無 SSH key pair，走 SSM"]
            Web["trustforge.web\n純 stdlib HTTP  ·  port 80\nsystemd trustforge.web"]
        end

        subgraph PL["TrustForge Pipeline  in-process on EC2"]
            Ingest["Ingestion Layer\n官方 OHLCV CSV  data/data/\nCoinDesk / Decrypt RSS  news\nFear+Greed / blockchain.info  onchain\nReddit / SEC  best-effort\n離線降級: demo/sample_data"]
            S1["Step 1  Claim 抽取\nBedrock #1  /  regex fallback"]
            S2["Step 2  信任評分聚合\n純演算法  ·  無 Bedrock 呼叫"]
            S3["Step 3  帶 claim_id 溯源行文\nBedrock #2"]
            S4["Step 4  限制複審  選用\nBedrock #3  預算 >60s 才執行"]
        end

        Bedrock[("Amazon Bedrock\nau.anthropic.claude-sonnet-4-6")]
        S3Dep[("S3  trustforge-deploy-acct\n部署 zip")]
        SSM["SSM Session Manager\n無金鑰運維"]
        CWL["CloudWatch Logs"]
        Lambda["Lambda  trustforge-demo\n已部署\nFunction URL 403 gated\n免費方案限制  非公開入口"]
    end

    IAM(["IAM Instance Role  trustforge-ec2\nbedrock:InvokeModel  anthropic.*\ns3:GetObject  deploy bucket\nAmazonSSMManagedInstanceCore"])

    User -->|"HTTP :80  公開"| Web
    Web --> Ingest
    Ingest --> S1 --> S2 --> S3 --> S4 -->|"report + evidence"| Web
    S1 -->|"InvokeModel"| Bedrock
    S3 -->|"InvokeModel"| Bedrock
    S4 -.->|"InvokeModel  選用"| Bedrock
    S3Dep -->|"aws s3 cp\nuser-data / SSM Run"| EC2Block
    SSM -->|"Session Manager"| EC2Block
    EC2Block -.->|"stdout / systemd"| CWL
    IAM -.->|"attached"| EC2Block
```

---

## AWS 服務對應表

| 服務 | 用途 | 為何選它 | 真實狀態 |
|------|------|---------|---------|
| **EC2 t3.micro** | 公開 HTTP 伺服器，跑 `trustforge.web`（port 80） | 最直接的公開 IP + systemd 常駐，無冷啟動延遲 | **已部署**，公開 IP，ap-southeast-2 |
| **Amazon Bedrock** | 唯一 LLM 入口（`bedrock.py`），Step1/3/4 呼叫 `au.anthropic.claude-sonnet-4-6` | 競賽規則：僅限 AWS 基礎模型；集中於 `bedrock.py` 方便合規審查 | **已部署**，instance role 直接呼叫 |
| **S3** | 部署 zip 暫存（`trustforge-deploy-{acct}`）；EC2 user-data / SSM 從這裡拉取更新 | 比傳送大檔到 EC2 更可靠，冪等 | **已部署** |
| **IAM Instance Role** | `trustforge-ec2`：最小權限（bedrock:InvokeModel + s3:GetObject + SSMCore），無 access key | 符合最小權限原則，不需在 EC2 儲存長效 credential | **已部署** |
| **SSM Session Manager** | 無金鑰遠端運維（含 Session、Run Command） | 不需開 SSH port，符合 zero-trust；EC2 無 key pair | **已部署**（AmazonSSMManagedInstanceCore） |
| **CloudWatch Logs** | EC2 systemd/stdout 日誌落地 | 預設收集，可觀測 | **已部署**（預設） |
| **Lambda** | `trustforge-demo` 函數已部署，可執行 | 部署流程涵蓋（`deploy/deploy_lambda.sh`） | **已部署但 Function URL 403 gated**（免費方案限制）；**不作公開入口，公開入口為 EC2** |

---

## 前後端分離對外拓樸（Issue #81 定案，2026-07-06）

方案 B 定案（詳見 `docs/architecture/PLAN-frontend-backend-split.md` §8）：EC2 上
nginx 取代 python 成為公開入口，python (`trustforge.web`) 收斂只聽
`127.0.0.1:8080`，不對外。**照 `deploy/nginx.conf` 逐字核對，公開監聽的
port 實際有兩個**（不是只有 :443）：

```
:80（IPv4+IPv6，明碼）                          :443（TLS，主要入口，HSTS/CSP）
 ├─ /healthz  → proxy_pass 127.0.0.1:8080/healthz     ├─ /assets/*、/、/analyze 等靜態路徑
 │   （明碼直通，供 LB/健康檢查探測，故意不強制走          →  React 靜態 build（Vite dist，SPA fallback）
 │    301，避免探測器不支援 redirect 誤判服務掛掉）      └─ /api/*、/healthz
 ├─ /.well-known/acme-challenge/ → 本機檔案                →  proxy_pass 127.0.0.1:8080（python，僅內部監聽）
 │   （certbot HTTP-01 challenge，含續簽用）
 └─ 其餘所有路徑 → 301 redirect 到
     https://trustforge.hurricanesoft.com.tw$request_uri
```

即 :80 這個 listener 本身仍是公開的（cutover 沒有把它拆掉），只是流量語意
從「直接把應用流量餵給 python」收斂成「health probe 明碼直通 + ACME
challenge + 其餘一律 301 轉 443」。Security Group 需同時開放
**tcp/80、tcp/443**（`deploy/deploy_ec2.sh` 建 SG 時先開 80，
`deploy/deploy_frontend_nginx.sh` cutover 到 react 拓樸時再加開 443；兩條
規則都要在，不是把 80 換成 443）。

nginx conf：`deploy/nginx.conf`；cutover 開關：`deploy/cutover_switch.sh`
（`react`/`react-http`/`legacy` 三態，回滾秒切）。EC2/Bedrock/S3/IAM/SSM
本身拓樸未變，只是公開入口從 python 直聽單一 port 80，換成 nginx 的
80+443 雙 listener 分工。

2026-07-06 CTO 對生產實測確認此拓樸已生效（curl 原始輸出見 PR #94／
`docs/architecture/PLAN-frontend-backend-split.md` §8）。

### 現有 API 端點（`/api/*`，web.py `_handle_api_*` 盤點）

| 端點 | 方法 | 對應函式 | 說明 |
|------|------|---------|------|
| `/api/health` | GET | `_handle_api_health` | 健康檢查，`{ok,data:{status,version,uptime_seconds}}` |
| `/api/status` | GET | `_handle_api_status` | 版本/uptime/`bedrock_capable`/`live_token_set` 能力旗標/cache backend 連線健康（primary/fallback、是否 degraded）/資料鮮度矩陣（fresh/stale/missing）；**刻意不含**成本帳本明細／連接器用量／最近排程執行，成本請走 `/api/costs`；依賴（cache backend 建構或鮮度讀取）失敗回 502 |
| `/api/costs` | GET | `_handle_api_costs` | cost ledger 表 |
| `/api/overview` | GET | `_handle_api_overview` | 首頁多幣總覽卡；**逐幣**對 cache backend 呼叫 `cache_get(strict=True)`（可能實際打 DynamoDB，讀不到才 fallback 到本地 JSON），非零 I/O；單幣純缺快照時該幣正常跳過（仍 200），backend 建構失敗或 primary+fallback 皆讀取失敗時整個請求回 502 |
| `/api/history` | GET | `_handle_api_history` | `?coin=BTC` 讀 `get_trust_history()`，PIT 歷史 |
| `/api/analyze` | GET | `_handle_api_analyze` | 信任分析報告；`?type=comparison` 為雙幣比較分支；與 SSR `/analyze`、`/analyze.json` 共用同一把 dedup key space（#51+#87） |
| `/healthz` | GET | （非 `/api/*`，nginx 部署腳本健康檢查用） | 純文字 `ok` |

SSR 舊路由（`/`、`/costs`、`/status`、`/analyze`、`/analyze.json`）仍在，
僅供 `cutover_switch.sh legacy` 緊急回滾，凍結新功能。

### 計劃新增（下一步，#85 多維度信任雷達）

- 後端新增 `agent/orchestrator.py::build_trust_radar()`（尚未存在，待實作）：
  按 kind（news/onchain/social/regulatory 等）聚合 `TrustScore`，回傳
  `{avg_trust, claim_count, single_source}`；`single_source: true`
  （目前 `regulatory`/`social` 各僅 1 個實體來源）需誠實標注。
- Issue #85 原文寫的落點是 `/analyze.json`（SSR JSON），但依本次 #81 定案
  「新功能一律走 `/api/*` + React、SSR 凍結」，實際應落在 `/api/analyze`
  （= `/analyze.json` 的 API 對等版，見上表），回應加 `trust_radar` 欄位，
  供 React `TrustRadarChart.tsx`（骨架已存在）接線；不新增路由、不回頭
  加功能到 SSR `/analyze.json`。

### 未採用服務（舊願景規劃，實際未部署）

| 服務 | 說明 |
|------|------|
| AWS App Runner | 未採用。改用 EC2 直接部署，避免容器化額外複雜度 |
| AWS Step Functions | 未採用。Pipeline 4 步驟在 EC2 單一 Python 行程內直接順序執行，不需跨服務編排 |
| Amazon DynamoDB | 未採用。快取需求不存在（每次 per-request 即時抓取，逾時直接降級） |
| API Gateway | 未採用。EC2 Security Group 直接開 TCP 80，不需額外閘道層 |
| AWS Secrets Manager | 未採用。外部 API key 目前不持久化（連接器以離線模式或公開端點為主） |
| CloudFront / Amplify | 未採用。無靜態前端資產 CDN 需求 |

---

## 15 分鐘執行預算對策

競賽規定每次分析限 15 分鐘。TrustForge 的實際表現遠優於此限制：

| 指標 | 數值 |
|------|------|
| 實測 per-run 耗時 | ~25–68 秒 |
| 競賽上限 | 900 秒（15 分鐘） |
| 餘裕倍數 | ~13–36 倍 |

**預算控制機制**：

- `ExecutionLog.remaining()` 全程追蹤剩餘秒數；Step 4（限制複審）設 `>60s` 門檻，接近預算時自動跳過，不阻塞報告輸出。
- 各 Ingestion 來源失敗（逾時/連線錯誤）**個別降級跳過**，不卡住整條管線；失敗來源名稱自動填入 `report.limits`，供評審可見。
- 公開面預設**離線示範模式**（`demo/sample_data`），無需等待外部 API；真實 Bedrock 分析需 `TRUSTFORGE_LIVE_TOKEN` token-gate 保護。

---

## 安全設計

| 面向 | 實作 |
|------|------|
| **最小 IAM** | Instance Role 僅 `bedrock:InvokeModel`（限 `anthropic.*` / `inference-profile/*anthropic*`）+ `s3:GetObject`（限 deploy bucket）+ SSMCore；無其他 AWS 權限 |
| **無金鑰運維** | EC2 無 SSH key pair，維運走 SSM Session Manager，不開 port 22 |
| **離線預設** | 公開 EC2 預設離線示範，真實 Bedrock 呼叫需 `TRUSTFORGE_LIVE_TOKEN` HTTP header |
| **token gate** | Live 模式 token-gated（環境變數 `TRUSTFORGE_LIVE_TOKEN`），防止 Bedrock 被濫用 |
| **反作弊（模型層）** | `bedrock.py` 強制：`fact` 型 claim 只能來自客觀來源（price/onchain/regulatory），社群/新聞主張一律降為 `inference`，不得宣稱是事實 |
| **版控安全** | pre-push hook：pytest 全綠才放行；GitHub + Gitea 雙遠端；不入版控任何 AWS credential |

---

## 與 HOYA BIT 理念對齊

HOYA BIT「AI Native Exchange OS」的核心定位是：**不代替投資決策，提供明確確認機制**。

TrustForge 與此理念同向：

- **不代客決策**：報告輸出「可查證分析 + 信心分數 + 反方證據 + 已知限制」，明確標示不確定性，而非給出買賣建議。
- **溯源透明**：每條主張（Claim）帶 `claim_id` 溯源到原始文件，評審可一路追回原始資料來源。
- **客觀 / 主觀分層**：`fact` 型主張只能來自客觀數據源（OHLCV / onchain / regulatory），社群輿論嚴格標記為 `inference` 或 `opinion`，防止主觀資訊被誤當事實。
- **市場資訊信任層**：TrustForge 可作為 HOYA BIT AI 入口的「市場資訊信任層」延伸——在資訊進入決策前，先做可查證的信任評分與溯源。

---

## 部署流程（EC2 更新）

```
本機開發 → pytest 通過 → git push
            ↓
    pre-push hook（可選 AWS CLI CD）
            ↓
    zip 打包 → s3 cp s3://trustforge-deploy-{acct}/trustforge_app.zip
            ↓
    SSM Run Command: aws s3 cp + unzip + systemctl restart trustforge
```

腳本：`deploy/deploy_ec2.sh`（冪等，IAM role / SG / user-data 全部自動建立）
