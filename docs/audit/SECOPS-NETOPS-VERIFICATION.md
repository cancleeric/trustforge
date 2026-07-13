# SecOps / NetOps 硬化稽核驗證（#113，純讀取，未切換 conf）

> 聲明：本報告為純讀取稽核，未執行 deploy/cutover_switch.sh、未切換 nginx conf、未啟用 443 block。實際 cutover 放行需 CEO+CISO+CPO+老闆四簽核。

## 驗證點 A — netops：nginx.conf /api/admin/ 用 TCP 層 $remote_addr 無條件覆寫來源 IP
- 證據：`deploy/nginx.conf`
  - `location /api/admin/` 區塊（起始於 L158）內：
    - L162 `proxy_set_header X-Real-IP $remote_addr;`
    - L163 `proxy_set_header X-Forwarded-For $remote_addr;`
  - 兩條皆使用 TCP 層 `$remote_addr`，非透傳客戶端自帶 header。
- 設計意圖：攻擊者偽造 header 會分散 per-IP lockout 計數，故兩條必須不可由客戶端塞值。
- 結果：**PASS**

## 驗證點 B — secops：web.py TRUST_PROXY 拓樸綁定 + fail-closed
- 證據（`src/trustforge/web.py`，行號以當下讀取為準）：
  - `TRUST_PROXY` 預設關：L387–389 定義，`os.getenv("TRUSTFORGE_TRUST_PROXY", ...)` 預設為空、不屬 truthy 集合 → 關。
  - `_resolve_client_ip()`（L420）：L433–434 `if not TRUST_PROXY: return direct_ip` —— 僅在 `TRUST_PROXY` 開時（L435 起）才讀 `X-Real-IP`，否則回傳 TCP 對端 `direct_ip`。
  - `main()` 拓樸綁定：L6522–6529，當 `TRUST_PROXY` 開且 `TRUSTFORGE_BIND_HOST` ≠ `127.0.0.1` 時強制改綁 `127.0.0.1`。
  - live token 已設但 `TRUST_PROXY` 未開 → fail-closed 拒絕啟動：L6537–6539 `raise SystemExit(...)`，防明文 HTTP token 外洩（issue #1 CISO High）。
- 設計意圖：信任反代 header 只有在 python 不對外（綁 127.0.0.1）時才安全；無 TLS 反代保護時直接拒絕啟動而非放行。
- 結果：**PASS**

## 驗證點 C（補強確認）— 泛用 /api/ 區塊同款 X-Real-IP 覆寫
- 證據：`deploy/nginx.conf`
  - 泛用 `location /api/`（起始於 L136）內 L140 `proxy_set_header X-Real-IP $remote_addr;`
  - 最長前綴優先（`/api/admin/` 長於 `/api/`）確保 admin 請求落 admin block（見 L145–147 註解說明）。
- 設計意圖：即便請求誤落泛用區塊，來源 IP 仍被 TCP 層 `$remote_addr` 無條件覆寫，不留可被客戶端塞值的路。
- 結果：**PASS**

## 結論
三項全 PASS → #113 稽核通過（純讀，未切換）。cutover 放行與 443 block 啟用仍待四簽核，不在本輪範圍。
