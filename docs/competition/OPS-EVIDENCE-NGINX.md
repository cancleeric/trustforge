# Ops Evidence — nginx X-Real-IP (#113)

> 決賽提交用：nginx 四份設定檔的 X-Real-IP / X-Forwarded-For / X-Forwarded-Proto 設定證據。

---

## 1. nginx 設定檔一覽

| 設定檔 | 用途 | Mode | TLS | HSTS |
|---|---|---|---|---|
| `deploy/nginx.conf` | **React SPA + TLS**（cutover 後主要設定） | 前後端分離 | ✅ 443 | ✅ |
| `deploy/nginx-react-http.conf` | React SPA + HTTP（bare-IP / pre-cert） | 前後端分離 | ❌ 80 only | ❌ |
| `deploy/nginx-legacy.conf` | SSR 全轉發 HTTP（cutover 前 / 回滾） | Python SSR | ❌ 80 only | ❌ |
| `deploy/nginx-legacy-tls.conf` | SSR 全轉發 TLS（HSTS-safe 回滾） | Python SSR | ✅ 443 | ✅ |

## 2. IP 可信度設計

### 2.1 `$remote_addr` → `X-Real-IP`（TCP 層，不可偽造）

```
nginx 使用 $remote_addr（TCP 連線對端 IP，非 HTTP header，不可由客戶端偽造）
  → proxy_set_header X-Real-IP $remote_addr
    → python _resolve_client_ip 讀取 X-Real-IP
```

**所有四份設定檔**皆使用 `proxy_set_header X-Real-IP $remote_addr`，確保 python 端能拿到真實來源 IP。

### 2.2 Python 端 `_resolve_client_ip`（`src/trustforge/web.py` L517-L540）

```
解析優先序：
1. TRUST_PROXY=1 且 X-Real-IP 存在 → 取 X-Real-IP（nginx 寫死的 $remote_addr）
2. TRUST_PROXY=1 且 X-Real-IP 缺席 → 退而求其次取 X-Forwarded-For 最左段
3. TRUST_PROXY=0（預設）或兩者皆無 → 退回直連 IP（client_address[0]）
```

`TRUST_PROXY` 只有 python 監聽 `127.0.0.1` 時才允許啟用，確保繞過 nginx 的請求不會被偽造的 header 騙走。

## 3. 各設定檔 IP 設定細節

### 3.1 `deploy/nginx.conf`（React TLS）

| Location | X-Real-IP | X-Forwarded-For | 說明 |
|---|---|---|---|
| `/api/` | `$remote_addr` | — | API 泛用反代 |
| `/api/admin/` | `$remote_addr` | `$remote_addr`（強制覆寫） | Admin 雙重硬化 |
| `/healthz` | `$remote_addr` | — | 健康檢查反代 |
| 80→443 redirect | `$remote_addr` | — | `/healthz` 明碼例外 |

**Admin 硬化（`location /api/admin/`）**：
- `X-Real-IP` 用 `$remote_addr` 覆寫（非透傳，防偽造）
- `X-Forwarded-For` 同樣用 `$remote_addr` 覆寫（`_resolve_client_ip` fallback 路徑也不能留偽造空間）
- `proxy_no_cache 1` + `proxy_cache_bypass 1`（防快取汙染）
- `add_header Cache-Control "no-store"`（防下游快取）

### 3.2 `deploy/nginx-react-http.conf`（React HTTP）

| Location | X-Real-IP | 說明 |
|---|---|---|
| `/api/` | `$remote_addr` | API 泛用反代 |
| `/healthz` | `$remote_addr` | 健康檢查 |
| `/api/admin/` | — | ⛔ 技術封鎖：`return 404`（明碼 HTTP 不允許 admin token 明文過線） |

### 3.3 `deploy/nginx-legacy.conf`（SSR HTTP）

| Location | X-Real-IP | 說明 |
|---|---|---|
| `/` (SSR 全轉發) | `$remote_addr` | 所有請求 proxy 給 python |

### 3.4 `deploy/nginx-legacy-tls.conf`（SSR TLS）

| Location | X-Real-IP | 說明 |
|---|---|---|
| `/` (SSR 全轉發) | `$remote_addr` | 所有請求 proxy 給 python（包一層 TLS） |
| `/healthz` | `$remote_addr` | 健康檢查明碼例外 |

## 4. 安全防線（縱深）

```
Layer 1: nginx TCP 層 $remote_addr → X-Real-IP（不可偽造）
Layer 2: python TRUST_PROXY gate（只有 127.0.0.1 bind 才開）
Layer 3: python main() 強制 bind=127.0.0.1（TRUST_PROXY=1 時，不允許對外直連）
Layer 4: live token TRUST_PROXY 連動（TRUST_PROXY 未開但 LIVE_TOKEN 已設 → 拒絕啟動）
Layer 5: admin per-IP lockout 仰賴正確的 X-Real-IP（nginx 覆寫而非透傳）
Layer 6: HTTP-only conf 的 `/api/admin/` 技術封鎖（return 404，完全不轉發）
```

## 5. Admin 管理面特別硬化

根據 harper CISO 條件 A + M1：

- **nginx 層**：`X-Real-IP` 和 `X-Forwarded-For` 皆用 `$remote_addr` 強制覆寫（非透傳），防止攻擊者偽造來源 IP 分散 admin per-IP lockout 計數
- **python 層**：`_resolve_client_ip()` 優先讀 `X-Real-IP`（nginx 寫死的值），X-Forwarded-For 只當 fallback
- **HTTP-only 模式**：`/api/admin/` 被 nginx `return 404` 技術封鎖，不轉發給 python（防 token 明文過線）
- **快取防護**：`proxy_no_cache` + `proxy_cache_bypass` + `Cache-Control: no-store`

## 6. 關鍵原始碼位置

| 檔案 | 內容 |
|---|---|
| `deploy/nginx.conf` | React TLS 反代設定（含 X-Real-IP、admin 硬化） |
| `deploy/nginx-react-http.conf` | React HTTP 反代設定（pre-cert 用） |
| `deploy/nginx-legacy.conf` | SSR HTTP 全轉發設定 |
| `deploy/nginx-legacy-tls.conf` | SSR TLS 全轉發設定（HSTS-safe 回滾） |
| `src/trustforge/web.py::_resolve_client_ip` | IP 解析邏輯（TRUST_PROXY 控制） |
| `src/trustforge/web.py::main()` L8422-L8450 | TRUST_PROXY + bind host 連動防護 |
| `src/trustforge/web.py` L3640-L3659 | X-Forwarded-Proto TLS 檢測 |
| `tests/test_security.py` L442-L529 | X-Real-IP / X-Forwarded-For 的 TRUST_PROXY 行為測試 |

## 7. Upstream 備援

所有四份設定檔皆包含雙 backend upstream：

```
upstream trustforge_backend {
    server 127.0.0.1:8080 max_fails=1 fail_timeout=1s;
    server 127.0.0.1:8081 backup;
}
```

- `:8080` — 主要 backend（python 主 process）
- `:8081` — backup（用於 zero-downtime restart，或新版先起在 8081 驗證再切）
- `max_fails=1 fail_timeout=1s` — 快速 failover，不讓單一請求卡住
