# TLS 設定（Let's Encrypt / certbot）— task #28 Phase 3

> ⛔ 本文件只寫**設定與步驟**；實際簽發憑證留給 netops 執行（需要真實 domain
> 指到 EC2 public IP，且開發環境無法驗證 ACME HTTP-01 challenge，此任務
> 不真跑）。CTO 這邊只確保 `deploy/nginx.conf`／`deploy/nginx-legacy.conf`
> 讀取的憑證路徑跟這裡的簽發步驟一致。

## 前置

1. 一個指到 EC2 public IP（或未來 ALB/CloudFront）的 DNS A/AAAA record，
   例如 `trustforge.example.com`。**沒有 domain 前無法用 HTTP-01 challenge
   簽發**——這是 netops 的前置工作，不在本次 Phase 3 範圍內完成。
2. Security Group 需開放 80（ACME challenge 用）與 443（見
   `deploy/deploy_frontend_nginx.sh` 的 SG 規則）。

## 簽發（Amazon Linux 2023，nginx 已由 `deploy/deploy_frontend_nginx.sh` 裝好）

```bash
sudo dnf install -y python3-certbot-nginx
sudo certbot --nginx -d trustforge.example.com \
  --non-interactive --agree-tos -m ops@hurricanesoft.example \
  --redirect
```

- `--nginx` plugin 會自動找到 `deploy/nginx.conf`／`deploy/nginx-legacy.conf`
  裡 `server_name trustforge.example.com` 的 server block，簽發後改寫
  `ssl_certificate`/`ssl_certificate_key` 指向
  `/etc/letsencrypt/live/trustforge.example.com/`（跟本專案兩份 conf 檔預留
  的路徑一致，不用改路徑）。
- `--redirect`：certbot 自動加 80→443 redirect（本專案兩份 conf 已經手動寫好
  對應的 redirect server block，`--nginx` plugin 偵測到已有 redirect 時
  不會重複加）。

## 續簽

certbot 裝好後會自帶 systemd timer（`certbot-renew.timer`），預設每天檢查
兩次、剩 30 天內才真的續簽。**不需要**額外自建 cron。驗證：

```bash
sudo systemctl list-timers | grep certbot
sudo certbot renew --dry-run   # 模擬續簽，不真的簽發
```

## HSTS

`deploy/nginx.conf`／`deploy/nginx-legacy.conf` 已內建
`Strict-Transport-Security: max-age=31536000; includeSubDomains`。**刻意
不加 `preload`**——一旦提交到瀏覽器 preload list 很難撤銷，等 443 穩定
運行一段時間（建議 P3 一週觀察期之後）再由 CEO+CISO 決定是否申請 preload。

## 回滾

TLS 憑證與 nginx 站台設定（legacy/react）互相獨立，`deploy/cutover_switch.sh`
只切換站台 conf、不動憑證；憑證出問題（過期/吊銷）不影響 legacy⇄react 的
切換邏輯，兩者可獨立除錯。
