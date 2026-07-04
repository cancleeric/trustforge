# TLS 設定（Let's Encrypt / certbot）— task #28 Phase 3（react-TLS domain cutover）

domain：`trustforge.hurricanesoft.com.tw` → DNS A record 已指到 EC2
`13.211.110.218`（✓ 已完成）。

> ⛔ 本文件只寫**設定與步驟**；實際簽發憑證這個任務**不真跑**
> （config-only，禁真跑 AWS/certbot——CEO 主線親自跑真部署）。CTO 這邊只
> 確保 `deploy/nginx.conf`（React + TLS）讀取的憑證路徑跟這裡的簽發步驟
> 一致，以及 `deploy/setup_tls.sh` 的呼叫方式跟本文件描述的順序相符。

## ⛔ 順序鐵則：certbot 前 nginx 必須先在 80 可服務 HTTP-01 challenge

Let's Encrypt 的 HTTP-01 challenge 是 certbot 對外開一個臨時檔案在
`http://<domain>/.well-known/acme-challenge/...`，ACME 伺服器會實際打這個
URL 驗證你真的控制這個 domain——**這代表 nginx 必須先在 80 port 上跑起來、
真的能對外服務**，certbot 才簽得出憑證。順序反了（例如先切上
`deploy/nginx.conf` 這種假設憑證已存在的 TLS conf）nginx 自己都起不來
（`nginx -t` 找不到憑證檔案而失敗），更別談通過 challenge。

完整 cutover runbook（見 `deploy/README.md` 同名章節有更完整版本）：

1. **DNS**：`trustforge.hurricanesoft.com.tw → 13.211.110.218`（✓ 已完成）。
2. **deploy legacy（nginx 在 80 上先服務）**：`bash deploy/deploy_frontend_nginx.sh`
   ——預設啟用 `deploy/nginx-legacy.conf`（SSR 全轉發，HTTP-only）；bare-IP
   現況若已切過 `deploy/cutover_switch.sh react-http` 也可以，重點是 nginx
   此刻確實在 80 上可服務。兩份 conf 都已內建
   `location ^~ /.well-known/acme-challenge/ { root /var/www/certbot; }`，
   HTTP-01 challenge 檔案能直接從檔案系統回應，不需要（也不依賴）
   `server_name` 是不是真實 domain。
3. **certbot 簽發**（本文件 + `deploy/setup_tls.sh`，見下——`certonly
   --webroot`，不用 `--nginx` plugin，見下方說明）。
4. **cutover 到 react（TLS 版）**：憑證就位後才執行
   `TRUSTFORGE_CUTOVER_CONFIRMED=yes deploy/cutover_switch.sh react`。
5. **驗證 https**：`curl -I https://trustforge.hurricanesoft.com.tw/`。

## 前置

1. DNS A/AAAA record 已指到 EC2 public IP（✓ 已完成，見上）。
2. Security Group 需開放 80（ACME challenge 用）與 443（見
   `deploy/deploy_frontend_nginx.sh` 的 SG 規則，該腳本已處理）。
3. 上方 Step 2（deploy legacy）已跑過，nginx 確實在 80 上可服務。

## 簽發（Amazon Linux 2023，nginx 已由 `deploy/deploy_frontend_nginx.sh` 裝好）

用 `deploy/setup_tls.sh`（**可選 step，預設不跑**，需
`TRUSTFORGE_RUN_CERTBOT=yes` + 真實 `ADMIN_EMAIL` 才會真的透過 SSM 對 EC2
執行 certbot——CEO 真跑時決定，不寫死自動執行）：

```bash
ADMIN_EMAIL=<真實可收信 email，CEO 填> TRUSTFORGE_RUN_CERTBOT=yes \
  bash deploy/setup_tls.sh
```

腳本內部等效於：

```bash
sudo dnf install -y certbot
sudo mkdir -p /var/www/certbot/.well-known/acme-challenge
sudo certbot certonly --webroot -w /var/www/certbot \
  -d trustforge.hurricanesoft.com.tw \
  --non-interactive --agree-tos -m <admin email> \
  --deploy-hook "nginx -t && systemctl reload nginx"
sudo systemctl enable --now certbot-renew.timer
sudo certbot renew --dry-run
```

⛔ **改用 `certonly --webroot`，不用 `--nginx` plugin**（codex 複審 HIGH：
`--nginx` plugin non-interactive 模式需要精準比對到 `server_name
trustforge.hurricanesoft.com.tw` 的 server block 才簽得出來，但
`nginx-legacy.conf`/`nginx-react-http.conf` 目前的 `server_name` 寫死是
`_`——這兩份 conf 從未被任何部署腳本自動改寫成真實 domain（先前文件誤以為
會、實際上是遺留的手動步驟，從沒真的自動化），`--nginx` non-interactive
在這種情況下配對不到，會直接簽發失敗或留下半殘狀態）：

- `certonly --webroot` **只取憑證，完全不碰 nginx config**：HTTP-01
  challenge 檔案寫進 `/var/www/certbot/.well-known/acme-challenge/`，
  ACME 伺服器打 `http://trustforge.hurricanesoft.com.tw/.well-known/
  acme-challenge/<token>` 時，是由 `nginx-legacy.conf`／
  `nginx-react-http.conf`／`nginx.conf`（cutover 後）裡新增的
  `location ^~ /.well-known/acme-challenge/ { root /var/www/certbot; }`
  這個 location 直接從檔案系統回應，**跟 `server_name` 是不是 `_` 完全
  無關**（同一個 port 上只有一個 server block 時，nginx 一律用它服務，
  不管 Host header 是什麼）——完全繞開 `--nginx` plugin 的 server_name
  配對問題。
- `--nginx` plugin 原本還會有的 `--redirect`（自動幫目前 live 的 conf 加
  80→443 redirect）效果，這裡不需要：真正的 TLS 拓樸/redirect 是
  `deploy/nginx.conf`（`cutover_switch.sh react` 才會切上去），`certonly`
  不改 nginx，兩者職責更乾淨地分開。
- 真正的 TLS 拓樸 `deploy/nginx.conf` 已經寫死讀
  `/etc/letsencrypt/live/trustforge.hurricanesoft.com.tw/{fullchain,privkey}.pem`
  ——只要憑證檔案簽出來就位，之後 `cutover_switch.sh react` 切上去時
  `nginx.conf` 就讀得到。
- **續簽也走同一條路**：`nginx.conf`（cutover 後的 TLS 拓樸）port 80
  server block 同樣加了 `/.well-known/acme-challenge/` 這個 location（在
  `return 301 ...` 的 catch-all之前），所以 `certbot-renew.timer` 之後
  自動續簽時，即使此刻 live 的是 TLS 版 nginx，HTTP-01 challenge 一樣服務
  得到，不會被 301 redirect 擋掉。

## 續簽

certbot 裝好後會自帶 systemd timer（`certbot-renew.timer`），預設每天檢查
兩次、剩 30 天內才真的續簽。**不需要**額外自建 cron，但 `deploy/setup_tls.sh`
會主動 `systemctl enable --now certbot-renew.timer` 確保它真的有啟用（不能
只裝好 certbot 卻沒開 timer）。驗證：

```bash
sudo systemctl list-timers | grep certbot
sudo certbot renew --dry-run   # 模擬續簽，不真的簽發
```

⛔ **`--deploy-hook` 是續簽真的生效的必要條件**（codex 複審 HIGH：90 天憑證
定時炸彈）：`--nginx` plugin 續簽時會自動 reload nginx，但這裡用的
`certonly --webroot` **不會**——續簽只更新磁碟上的憑證檔
（`/etc/letsencrypt/live/<domain>/`），nginx worker 仍抱著啟動時載入的舊
憑證不放，直到有人手動 reload。續簽本身「成功」（timer/certbot 都報
OK），但客戶端最終會收到過期憑證，而且極難察覺（沒有任何一步會報錯）。

修法：`certbot certonly` 加 `--deploy-hook "nginx -t && systemctl reload
nginx"`——這個 hook 會被 certbot 寫進
`/etc/letsencrypt/renewal/<domain>.conf` 的 `renew_hook`，之後每次
`certbot-renew.timer` 觸發的 `certbot renew` 續簽成功後都會自動重跑
（`nginx -t` 先擋語法錯誤，通過才 `systemctl reload nginx`，避免 reload 到
一個壞掉的 config）。`certbot renew --dry-run` 也會列出/觸發這個 hook，是
驗證整條續簽鏈路（timer → `certonly --webroot` → HTTP-01 challenge 走
`location ^~ /.well-known/acme-challenge/` → deploy-hook reload nginx）的
最後一步。

**續簽完整流程**：`certbot-renew.timer`（定期觸發）→ `certbot renew`
（webroot 模式重新走 HTTP-01 challenge）→ 續簽成功 → `renew_hook`
（`nginx -t && systemctl reload nginx`）自動執行 → nginx 載入新憑證。

## HSTS

`deploy/nginx.conf` 已內建
`Strict-Transport-Security: max-age=31536000; includeSubDomains`（只在
443 server block，簽發完成、cutover 到 react 之後即生效，不需額外設定）。
**刻意不加 `preload`**——一旦提交到瀏覽器 preload list 很難撤銷，等 443
穩定運行一段時間（建議 P3 一週觀察期之後）再由 CEO+CISO 決定是否申請
preload。

## 回滾

TLS 憑證與 nginx 站台設定（legacy/react/react-http/legacy-tls）互相獨立，
`deploy/cutover_switch.sh` 只切換站台 conf、不動憑證；憑證出問題（過期/
吊銷）不影響 legacy⇄react 的切換邏輯，兩者可獨立除錯。緊急回滾：
`deploy/cutover_switch.sh legacy`（秒切回 SSR 全轉發，不動憑證）。

⛔ **HSTS-safe rollback**（codex 複審 HIGH）：`react`（TLS）cutover 之後
瀏覽器已經記住一年 HSTS，若這裡切上純 HTTP 版 `nginx-legacy.conf`，回訪過
的使用者的瀏覽器會直接強制升級成 https、連不到只聽 80 的 legacy——回滾本
身反而讓事故惡化。`cutover_switch.sh` 因此在 `legacy` 模式下會先偵測
`/etc/letsencrypt/live/trustforge.hurricanesoft.com.tw/fullchain.pem` 是否
存在：
- **存在**（本文件 Step 3 已跑過、憑證已簽發）→ 自動改用
  `deploy/nginx-legacy-tls.conf`（443 服務同一張憑證、保留 HSTS、80→443
  canonical redirect + ACME challenge location 續簽用），SSR/API 拓樸跟
  `nginx-legacy.conf` 完全一樣，只是多包一層 TLS。
- **不存在**（Step 3 還沒跑，pre-cert ACME bootstrap 現況）→ 維持原本的
  HTTP-only `nginx-legacy.conf`，行為不變（這是唯一有 HTTP-01 webroot
  可服務、certbot 才簽得出憑證的階段，本來就不該是 443）。

真的起本機 nginx + python 驗證過「切上 `nginx-legacy-tls.conf` 後 443 仍
正常 serve SSR、且回應帶 `Strict-Transport-Security` header」，見
`deploy/test_nginx_legacy_tls_conf.sh`；ACME challenge 在這份 TLS 版底下
續簽時一樣不受 301 影響，見 `deploy/test_acme_challenge.sh` 的
`legacy-tls-renewal` 場景。
