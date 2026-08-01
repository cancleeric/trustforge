# 14 — Troubleshooting FAQ 與術語表

[← 13 Hands-on Labs 實作手冊 ](13-hands-on-labs.md)[文件首頁 ](README.md)

## 14 — Troubleshooting FAQ 與術語表

Troubleshooting FAQ · AWS、部署、API、UI、成本、rate limit、rollback

**目錄 **

- [排錯決策樹 ](#decision-tree)

- [AWS / Bedrock ](#aws)

- [部署 / nginx / TLS ](#deploy)

- [API / UI ](#api)

- [成本 / rate limit ](#cost)

- [術語表 ](#glossary)

### 1. 排錯決策樹

頁面打不開？ ├─ DNS/TLS 錯 → 查 domain、certbot、nginx 443 ├─ 502/504 → 查 systemctl status trustforge + nginx proxy ├─ /healthz OK 但 /api 失敗 → 查 token、config、backend logs └─ analyze 429 → 查 Budget Guard / rate limit / daily cap

### 2. AWS / Bedrock

| 症狀 | 可能原因 | 處理 |
| --- | --- | --- |
| Bedrock AccessDenied | model access 未開、IAM policy 不含 InvokeModel、region 錯 | 確認 ap-southeast-2 model access 與 inference profile ARN |
| SSM token 讀不到 | Parameter 名稱或 KMS 權限錯 | 查 `TRUSTFORGE_TOKEN_SSM_PREFIX `與 instance role |
| DynamoDB fallback local | 表不存在或 IAM 不足 | 建立表並補 policy；確認 CloudWatch 降級指標 |

### 3. 部署 / nginx / TLS

- **HTTP health 被 301： **`/healthz `在 port 80 必須直通，不能被全部轉 HTTPS。

- **502 Bad Gateway： **backend 沒跑或 nginx proxy 指到錯 port。

- **TLS 失敗： **先確認 DNS 指向、ACME challenge 路徑、certbot renewal timer。

sudo nginx -t sudo systemctl status nginx sudo systemctl status trustforge.service curl -v http://DOMAIN/healthz curl -v https://DOMAIN/api/health

### 4. API / UI

- **401： **缺 live token 或 token rotation 後前端未更新。

- **429： **Budget Guard 或 rate limit 正常保護，不應直接關掉；先看成本。

- **報告沒有來源： **檢查 claim_id / provenance pipeline，不要接受無來源生成。

- **UI build 成功但頁面空白： **查 CSP、Vite base path、API base URL。

### 5. 成本 / rate limit

**成本異常優先順序： **先調低 daily cap 或清空 Bedrock model id → 查 cost ledger → 查 traffic/rate limit → 再恢復 live analyze。

curl -fsS https://trustforge.hurricanesoft.com.tw/api/costs curl -fsS https://trustforge.hurricanesoft.com.tw/api/status journalctl -u trustforge.service -n 200 --no-pager | grep -i budget

### 6. 術語表

| 術語 | 意思 |
| --- | --- |
| TrustScore | 來源聲譽、交叉佐證、時效、操縱懲罰的加權分數。 |
| Provenance | 每個結論能追溯到 claim/source 的證據鏈。 |
| Budget Guard | Bedrock 呼叫前先預留成本，避免超支。 |
| Fail-closed | 未設定時預設關閉高風險功能，而不是誤開。 |
| Stale / Partial | 資料過舊或不完整時誠實標記，不假裝完整。 |

[Hands-on Labs ](13-hands-on-labs.md)[運維手冊 ](07-operations.md)[安全交接 ](10-security-handover.md)

TrustForge by HurricaneSoft（颶風軟體）· AWS Workshop-grade 技術文件
文件版本：v0.18.5 · 最後更新：2026-07-26
