# Changelog

## v0.8.0 — 2026-07-07

管理控制台（運行時設定）+ SecureString token 傳遞。開真 Bedrock 前的線上調控與部署安全前置。

### 管理控制台（新）
- **設定儲存層**（#111）：`admin_config` — DynamoDB 保留字 item 存 config，CAS 樂觀鎖、審計軌跡（token 遮罩）、15s TTL 快取 write-through。
- **admin API + 認證**（#112）：獨立 `TRUSTFORGE_ADMIN_TOKEN`（header-only、compare_digest、未設→404 fail-closed、admin≠live token 拒啟）、失敗 lockout + 全域 backstop、GET/PUT/audit 三端點。
- **三層 cap + live 閘動態化**（#114）：每日 cap `config → env → DEFAULT($3)` 三層；`HAS_BEDROCK`/`LIVE_TOKEN` 從模組級常數改每請求動態；kill-switch 完整化（env cap≤0 凌駕 config、`bedrock_enabled=false` 統一擋 online-stance、budget 負快取）。
- **React /admin 頁**（#116）：token 閘（記憶體儲存禁 localStorage）、cap/Bedrock 開關（二次確認）、live token 一次性顯示、來源徽章（config/env/default）、審計表格。
- **部署銜接**（#117）：三 env 進 systemd（`${VAR-}` fail-closed）、nginx `/api/admin/` 硬化（X-Real-IP 覆寫、no-store）、react-http 明碼模式技術封鎖 admin。

### 安全
- **SecureString token 傳遞**（#119）：admin/live token 改經 SSM Parameter Store SecureString——值不進 command history/user-data/argv，遠端 `get-parameter --with-decryption` + trap 清理（失敗可見）。IAM 窄範圍（前綴 ARN + `kms:ViaService`）。

_全程副手雙審（CISO + QA/VP-Eng）+ CEO 親測（含 Chrome Playwright）。_

## v0.7.0 — 2026-07-07

開真 Bedrock 前的護欄工程、前後端架構定案、W3 資料前置與 AI 友善介面。

### 核心 / 信任引擎
- **SSR `/analyze` 防重複計費**（#51 #87）：`/analyze`、`/analyze.json`、`/api/analyze` 三路由共用同一把 in-flight dedup key space，同參數跨路由並發只執行一次；fail-open 頻率告警（incident 週期追蹤 + monotonic clock + 冷卻防洗版，#93）。
- **stance 快取預建**（#84）：冪等去重（version+label 雙判準）、逐呼即時入帳的原子預留 budget guard。
- **跨幣信任排行 + 操縱風險徽章**（#86）：操縱風險採 worst-case 主訊號（不被平均稀釋），資料不足顯式「未評分」。
- **獨立來源去重**：跨源分歧來源按 source 去重（信任誠實性）。

### 前端 / UX
- **架構定案方案 B**（#81）：web.py 降純 `/api/*` API + React SPA 獨立部署（nginx serve 靜態 + `/api/*` 反代 127.0.0.1:8080），SSR 凍結僅供緊急回滾。
- **資訊完整度重定位**（#90 #101 #12）：全站「信心」措辭改「資訊完整度」（衡量資訊充足度非預測準確率）、首頁/內頁主角數字統一口徑、比較頁去重複巢狀。
- **信任趨勢嵌入**（#89）：分析頁嵌入信任趨勢，今日對比誠實標注日期（缺資料標「較前次快照」不假裝較昨日）。

### 資料 / 平台
- **W3 前置**（#107）：連接器擷取公開 author 累積帳號維度資料（供未來協同操縱偵測），公開端點過濾不外洩、90 天 TTL、隱私聲明。
- **AI 友善介面**（#108）：OpenAPI 3.1 spec（`GET /api/openapi.yaml`）、agent 指南（`GET /llms.txt`）、`/api/status.docs`——AI agent 可直接消費信任情報，缺鍵語意「未評估≠零」為正式契約。

### 安全
- **CISO hardening**（#2 #11）：live token 改走 `X-Live-Token` header（query 保留 deprecation 相容）、Bedrock IAM ARN region 白名單且每次部署 reconcile、`_safe_href` 控制字元防護、token 零回吐自我連結。

### 基建
- **文件重整**（#97）：docs/ 五分區 + archive 歸檔制。
- **測試加速**（#109）：conftest 全域鎖 `CACHE_BACKEND=json`，全量 pytest 15:50 → 27s。

_全程 PR gate：eye 影響面掃描 + 對抗式窮舉審查 +（安全項）CISO 雙審 + CEO 親測。_
