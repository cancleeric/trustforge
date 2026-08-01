# TrustForge 資安設計說明（Security Posture）

> 本文件說明 TrustForge 在基礎設施、應用層、資料處理各面向的安全設計。
> 目的：向主辦方/評審說明我們如何保護系統與使用者。

---

## 1. 安全設計總覽

TrustForge 採用**縱深防禦（Defense in Depth）**策略，從基礎設施到應用層逐層設防：

```
┌─────────────────────────────────────────────────┐
│  Layer 1：AWS 基礎設施安全                        │
│  ├─ IAM 最小權限                                 │
│  ├─ SSM 無金鑰運維（零 SSH）                      │
│  └─ Security Group 最小開放                       │
├─────────────────────────────────────────────────┤
│  Layer 2：網路與傳輸安全                          │
│  ├─ nginx TLS (HTTPS :443) + HSTS               │
│  ├─ HTTP :80 僅做 301 redirect + health probe    │
│  └─ 後端僅聽 127.0.0.1:8080（不對外）             │
├─────────────────────────────────────────────────┤
│  Layer 3：應用層安全                              │
│  ├─ Token Gate（防 Bedrock 濫用）                 │
│  ├─ 反作弊架構（LLM 無法注入外部結論）             │
│  ├─ Dedup 防重複提交                              │
│  └─ 輸入驗證 + 錯誤處理                          │
├─────────────────────────────────────────────────┤
│  Layer 4：資料安全                                │
│  ├─ 無持久化使用者個資                            │
│  ├─ API key 不入版控                              │
│  └─ 分析結果不含交易建議                          │
└─────────────────────────────────────────────────┘
```

---

## 2. AWS 基礎設施安全

### IAM 最小權限原則

| 權限 | 範圍 | 理由 |
|------|------|------|
| `bedrock:InvokeModel` | 限 `anthropic.*` + `inference-profile/*anthropic*` | 只允許呼叫 Bedrock Claude，不能存取其他 AWS 服務 |
| `s3:GetObject` | 限 `trustforge-deploy-*` bucket | 只能讀取部署用 zip，不能寫入或讀取其他 bucket |
| `AmazonSSMManagedInstanceCore` | EC2 自身 | 允許 SSM Session Manager 連線 |

**不授予的權限：**
- ❌ 無 `s3:PutObject`（不能寫 S3）
- ❌ 無 `dynamodb:*`（不使用 DynamoDB）
- ❌ 無 `lambda:InvokeFunction`（Lambda 僅部署用，非公開入口）
- ❌ 無任何 VPC/網路管理權限

### 無金鑰運維

- EC2 **無 SSH key pair** — 不可能被 brute force 或 key 洩漏攻擊
- Security Group **不開 port 22**
- 所有遠端維運透過 **AWS SSM Session Manager**（IAM 認證 + 加密通道）
- 維運操作有 CloudTrail 稽核記錄

### Security Group 規則

| 方向 | Port | 來源 | 用途 |
|------|------|------|------|
| Inbound | 80 | 0.0.0.0/0 | HTTP→HTTPS redirect + health check |
| Inbound | 443 | 0.0.0.0/0 | HTTPS 主要入口 |
| Outbound | 443 | 0.0.0.0/0 | Bedrock API + 外部資料來源 |

**不開放：** SSH (22)、資料庫 (3306/5432)、任何其他 port。

---

## 3. 網路與傳輸安全

### TLS 配置

- **協議**：TLS 1.2+（禁用 TLS 1.0/1.1）
- **憑證**：Let's Encrypt 自動續簽（certbot + ACME HTTP-01）
- **HSTS**：啟用（`Strict-Transport-Security: max-age=31536000`）
- **CSP**：Content Security Policy 限制資源載入來源

### 前後端分離拓樸

```
外部流量 → nginx (:443 TLS)
              ├─ /assets/* → React 靜態檔案（本機 dist/）
              ├─ /api/*    → proxy_pass 127.0.0.1:8080
              └─ /healthz  → proxy_pass 127.0.0.1:8080

Python 後端只聽 127.0.0.1:8080（外部無法直接存取）
```

---

## 4. 應用層安全

### Token Gate（防止 Bedrock 被濫用）

- 公開 Demo 預設**離線模式**（使用 `demo/sample_data`）
- 真實 Bedrock 分析需要 `TRUSTFORGE_LIVE_TOKEN` HTTP header
- Token 儲存在環境變數，不硬編碼

→ **即使 EC2 公開 URL 被掃到，無 token 無法觸發 Bedrock 呼叫，不會產生 AWS 費用**

### 反作弊架構（資料完整性）

| 威脅 | 防禦機制 |
|------|---------|
| LLM 幻覺注入假資料 | LLM 只能引用 TrustedBrief 的 claim_id，無法自創結論 |
| 外部 API 回傳惡意內容 | 所有外部資料經 claim extraction 後才進入 pipeline，原始 HTML/JSON 不直接呈現 |
| 使用者輸入注入 | 使用者問題僅作為 Bedrock prompt 的一部分，不直接拼接到系統指令 |
| 重複提交攻擊 | Dedup 機制（滑動視窗 + coin_key 去重），防止重複消耗 Bedrock quota |

### 事實/推論分層（防止誤導）

```python
# bedrock.py 強制規則：
# fact 型 claim 只能來自客觀來源（price/onchain/regulatory）
# 社群/新聞主張一律降為 inference，不得宣稱是事實
```

---

## 5. 資料安全與隱私

### 不蒐集、不儲存的資料

| 資料類型 | 狀態 |
|---------|------|
| 使用者帳號/密碼 | ❌ 不存在（無登入系統） |
| 使用者 IP/裝置指紋 | ❌ 不記錄（nginx access log 僅做除錯用，不長期保留） |
| 交易資料/持倉資訊 | ❌ 不接觸（TrustForge 是分析工具，不連接交易所帳戶） |
| 個人投資偏好 | ❌ 不蒐集 |

### 資料流向（無個資涉入）

```
外部公開 API → TrustForge Pipeline → 分析報告（公開資訊摘要）
                                     ↓
                               使用者瀏覽器（不回傳任何使用者資料）
```

### API Key 管理

- 外部 API key 以**環境變數**存取
- `.gitignore` 排除所有 `.env`、credential 檔案
- pre-push hook 會掃描是否有 secret 意外入版控
- 連接器以**離線模式或公開端點為主**，減少 key 依賴

---

## 6. 版控與部署安全

### Git 安全

| 措施 | 說明 |
|------|------|
| pre-push hook | pytest 全綠 + secret 掃描才允許推送 |
| 雙遠端 | GitHub + Gitea 備援 |
| 無 credential 入版控 | `.gitignore` + pre-push 雙重防護 |

### 部署流程

```
開發機 → pytest 通過 → git push → zip 打包 → S3 → SSM Run Command → EC2 重啟
```

- 部署透過 SSM（非 SSH）
- 部署包從 S3 拉取（IAM 認證，非公開下載）
- systemd 管理服務生命週期（自動重啟、日誌收集）

---

## 7. 可觀測性與事件回應

### 監控

| 工具 | 用途 |
|------|------|
| CloudWatch Logs | 系統日誌、錯誤追蹤 |
| `/api/status` | 服務健康狀態自檢（cache 狀態、dedup 健康） |
| `/api/health` | 版本、uptime、Bedrock 可用性 |
| Execution Log | 每次分析的完整時間軸記錄 |

### 異常處置

- 外部 API 失敗 → 個別降級，標記在報告 `limits` 欄位
- Bedrock 呼叫逾時 → 自動跳過選用步驟（Step 4），保證報告產出
- Dedup 異常 → fail-open（允許通過但記錄告警）
- Token 驗證失敗 → 403 拒絕，不觸發任何後端邏輯

---

## 8. 威脅模型與已知風險

| 威脅 | 風險等級 | 緩解措施 | 殘餘風險 |
|------|---------|---------|---------|
| Bedrock 被濫用（費用暴增） | 中 | Token Gate + 離線預設 | 低（需取得 token） |
| 外部 API 回傳惡意內容 | 低 | Claim extraction 過濾 | 極低 |
| DDoS 對 EC2 | 中 | SG 只開 80/443，無狀態服務 | 中（未加 WAF/CloudFront） |
| 供應鏈攻擊（pip 套件） | 低 | requirements 鎖版 + pre-push 測試 | 低 |
| Bedrock 模型回傳不當內容 | 低 | Pipeline 結構限制 LLM 輸出範圍 | 極低 |

### 未實作但已規劃（誠實揭露）

- ❌ WAF / CloudFront（成本考量，MVP 階段未加）
- ❌ 自動化弱點掃描（手動 + pre-push hook 替代）
- ❌ SOC 2 / ISO 27001（MVP 階段不適用）

---

## 9. 合規聲明

| 要求 | 狀態 |
|------|------|
| 僅使用 AWS Bedrock 基礎模型 | ✅ 合規（Claude Sonnet，無第三方 LLM） |
| 反作弊（判斷由自有 pipeline 產生） | ✅ 合規（Trust Layer 純演算法） |
| 不提供投資建議 | ✅ 合規（報告明確標示「非投資建議」） |
| 來源揭露 | ✅ 合規（所有資料來源列於 Evidence List） |

---

*文件路徑：`docs/competition/SECURITY-POSTURE.md`*
*關聯文件：`docs/architecture/AWS-ARCHITECTURE.md`、`TRUST-EXPLAINABILITY.md`*
