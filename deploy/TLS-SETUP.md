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
   此刻確實在 80 上可服務。
3. **certbot 簽發**（本文件 + `deploy/setup_tls.sh`，見下）。
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
sudo dnf install -y python3-certbot-nginx
sudo certbot --nginx -d trustforge.hurricanesoft.com.tw \
  --non-interactive --agree-tos -m <admin email> \
  --redirect
```

- `--nginx` plugin 會自動找到**目前 live** 的 nginx conf（此刻是
  `nginx-legacy.conf` 或 `nginx-react-http.conf`）裡
  `server_name trustforge.hurricanesoft.com.tw` 的 server block 簽發憑證。
  真正的 TLS 拓樸 `deploy/nginx.conf` 已經寫死讀
  `/etc/letsencrypt/live/trustforge.hurricanesoft.com.tw/{fullchain,privkey}.pem`
  ——不依賴 certbot 幫 legacy/react-http conf 加的東西，只要憑證檔案簽出來
  就位，之後 `cutover_switch.sh react` 切上去時 `nginx.conf` 就讀得到。
- `--redirect`：certbot 順便幫「目前 live 的那份 conf」加 80→443
  redirect；這個副作用不影響後續 cutover（`cutover_switch.sh react` 會
  整份換成 `deploy/nginx.conf`，覆蓋掉 certbot 這裡順手加的東西）。

## 續簽

certbot 裝好後會自帶 systemd timer（`certbot-renew.timer`），預設每天檢查
兩次、剩 30 天內才真的續簽。**不需要**額外自建 cron。驗證：

```bash
sudo systemctl list-timers | grep certbot
sudo certbot renew --dry-run   # 模擬續簽，不真的簽發
```

## HSTS

`deploy/nginx.conf` 已內建
`Strict-Transport-Security: max-age=31536000; includeSubDomains`（只在
443 server block，簽發完成、cutover 到 react 之後即生效，不需額外設定）。
**刻意不加 `preload`**——一旦提交到瀏覽器 preload list 很難撤銷，等 443
穩定運行一段時間（建議 P3 一週觀察期之後）再由 CEO+CISO 決定是否申請
preload。

## 回滾

TLS 憑證與 nginx 站台設定（legacy/react/react-http）互相獨立，
`deploy/cutover_switch.sh` 只切換站台 conf、不動憑證；憑證出問題（過期/
吊銷）不影響 legacy⇄react 的切換邏輯，兩者可獨立除錯。緊急回滾：
`deploy/cutover_switch.sh legacy`（秒切回 SSR 全轉發，不動憑證）。
