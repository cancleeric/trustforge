# Memory: TrustForge v0.16.18 生產佈署

- **日期**：2026-07-20
- **主題**：TrustForge v0.16.18 生產佈署
- **操作者**：HurricaneSoft 團隊

---

## Repo 結構

- 主目錄 `/Users/apple/HurricaneSoft/trustforge` 是 **bare repo**
- 使用 **git worktrees**，各功能分支在 `/private/tmp/` 的 worktree 裡
- 主工作目錄 `/Users/apple/HurricaneSoft/trustforge-main` 為 main 分支 worktree

## 佈署方式

- 腳本：`deploy/deploy_ec2.sh`
- 流程：S3 上傳 → SSM update-in-place
- 目標：EC2 實例 `<EC2_INSTANCE_ID>`（ap-southeast-2）

## 遇到的問題

### 1. SSM agent ConnectionLost

- **現象**：SSM 無法連線到 EC2 實例
- **原因**：SSM agent 可能因為長時間 idle 斷線
- **解法**：reboot EC2 實例

### 2. trustforge-canary.service transient unit 殘留

- **現象**：systemd 報告 canary service failed
- **解法**：`systemctl reset-failed trustforge-canary.service`

### 3. nginx conf 301 空轉（重要）

- **現象**：佈署後管理面板 parse_error，API 回傳 301
- **根因**：之前有人跑過 `cutover_switch.sh react`，把 nginx 切到 TLS 版 `react.conf`，port 80 全部 301 到 `https://trustforge.hurricanesoft.com.tw`。使用者用 bare IP 訪問 → 被 301 → 管理面板 API 拿不到 JSON → parse_error
- **解法**：切回 `react-http.conf`（純 HTTP，port 80 直接 serve）

## 省錢設定

| 設定 | 值 | 效果 |
|------|----|------|
| `BEDROCK_MODEL_ID` | 不設 | 離線模式，不呼叫 Bedrock |
| analysis-flow | 不啟動 | 不跑自動分析 |
| `RUNTIME_SWITCH` | 不設 | 不跑持續循環 |

## 手動即時分析

需要時才設定：

```bash
BEDROCK_MODEL_ID=ap-southeast-2.anthropic.claude-sonnet-4-20250514-v1:0
```

只有手動觸發才花錢，平時不設定 = 零 Bedrock 費用。

## 修正措施

在 `deploy_ec2.sh` 加了 **nginx port 80 smoke test**：

- 佈署後自動 `curl -s -o /dev/null -w "%{http_code}" http://localhost/healthz`
- 若偵測到 301 → 自動降級到 `react-http.conf` 並 reload nginx

## 教訓

> 多人開發時，deploy 腳本不碰 nginx conf，但**必須驗證最終使用者可達性**。
> nginx conf 切換是獨立於 code deploy 的操作，deploy 腳本不知道 nginx 被動過。
> 因此 deploy 後的 smoke test 是必要的安全網。
