# 技能：EC2 佈署後 nginx 健全性檢查

## 技能名稱

`trustforge-deploy-nginx-smoke-check`

## 觸發時機

每次 `deploy_ec2.sh update-in-place` 完成後。

## 目的

確保佈署後使用者透過 public IP:80 可以正常存取服務。

## 步驟

1. **健康檢查** — 透過 SSM 對實例打：
   ```bash
   curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/healthz
   ```

2. **回傳 200** → 通過，不動。

3. **回傳 301 或其他** → nginx conf 可能是 TLS 版在做 redirect，自動降級：
   - 確認 `/etc/nginx/trustforge-sites/react-http.conf` 存在
   - 切換 symlink：
     ```bash
     ln -sfn /etc/nginx/trustforge-sites/react-http.conf /etc/nginx/conf.d/trustforge.conf
     ```
   - 驗證並重載：
     ```bash
     nginx -t && systemctl reload nginx
     ```

4. **印 WARNING** 提醒後續人工切回 TLS 版。

## 實作位置

`deploy/deploy_ec2.sh` 第 593–620 行（`update-in-place` 路徑結尾）。

## 相關知識

- **nginx conf symlink 架構**：
  ```
  /etc/nginx/conf.d/trustforge.conf
    → /etc/nginx/trustforge-sites/{react,react-http,legacy,legacy-tls}.conf
  ```

- **cutover_switch.sh** 是唯一正式切 nginx conf 的腳本（有 flock 交易鎖、rollback 機制）。

- `deploy_ec2.sh` 的 smoke test 是**防禦性補強**，不是替代 `cutover_switch.sh`。

## 適用場景

- 多人開發時 A 切了 nginx、B 佈署新版
- TLS cert 過期 / domain DNS 未指向
- 意外的 nginx conf 殘留

## 不適用

- TLS 已正確設定且 domain DNS 正確 → nginx port 80 的 `/healthz` 本身有例外不走 301（見 `react.conf`）
- 首次建置（user-data 自帶 nginx 設定）
