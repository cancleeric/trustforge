# 佈署注意事項：nginx conf 301 空轉問題

## 問題描述

update-in-place 佈署後管理面板顯示 `parse_error`，API 回傳 HTTP 404/301。
使用者無法正常使用管理面板與 API。

## 根因

nginx conf 是 `react.conf`（TLS 版），port 80 全部 301 到 HTTPS domain（`https://trustforge.hurricanesoft.com.tw`），但使用者用 bare IP 訪問。

流程：

```
使用者 → http://<bare-IP>/api/... 
      → nginx 301 → https://trustforge.hurricanesoft.com.tw/api/...
      → 瀏覽器跟隨 301 → DNS 解析失敗或 cert 不匹配
      → 管理面板 fetch 拿到 HTML/error 而非 JSON
      → parse_error
```

## 影響範圍

- 所有透過 IP 訪問的使用者
- 管理面板（前端）
- API（後端 JSON endpoints）

## 觸發條件

有人跑過 `cutover_switch.sh react` 把 nginx 切到 TLS 版，但後續佈署腳本不碰 nginx conf，不會自動切回。

## 修復步驟

### 方法 1：透過 SSM 直接修復

```bash
ln -sfn /etc/nginx/trustforge-sites/react-http.conf /etc/nginx/conf.d/trustforge.conf \
  && nginx -t \
  && systemctl reload nginx
```

### 方法 2：使用 cutover 腳本

```bash
TF_ALLOW_INSECURE_HTTP_CUTOVER=yes \
TRUSTFORGE_CUTOVER_CONFIRMED=yes \
bash deploy/cutover_switch.sh react-http
```

## 預防措施

`deploy_ec2.sh` 已加入 **nginx port 80 smoke test**：

- 佈署完成後自動對 `http://localhost/healthz` 發 HTTP 請求
- 若回應為 301（而非 200），自動降級到 `react-http.conf` 並 reload nginx
- 降級後再次驗證，確保 port 80 可正常 serve

## 相關檔案

| 檔案 | 用途 |
|------|------|
| `deploy/deploy_ec2.sh` | 主佈署腳本（含 smoke test） |
| `deploy/cutover_switch.sh` | nginx conf 切換腳本 |
| `deploy/nginx-react-http.conf` | 純 HTTP 版 nginx 設定 |

## 多人協作注意

> ⚠️ 切 nginx conf 是**獨立於 code deploy** 的操作。
> deploy 腳本不知道 nginx 被動過。
> 
> 若需要切換到 TLS 版（`react.conf`），必須確保：
> 1. DNS 已指向該 IP
> 2. TLS 憑證已正確安裝
> 3. 所有使用者都透過 domain 而非 bare IP 訪問
> 
> 否則應維持 `react-http.conf`。
