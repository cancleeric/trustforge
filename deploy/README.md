# 部署 — AWS CLI + pre-push CD（Lambda + Function URL）

## 每小時 release train

`scripts/hourly_release_train.py` 不掃描 feature 分支、不建立或合併 PR，只從已由
開發流程整理完成的遠端 `develop` 開始。每輪使用獨立 worktree 與 lease，先驗
`develop` 完整 pre-push gate，再合併到 `main`、重跑完整 gate、建立不可混淆的
`release/auto-<UTC>-<SHA>` 分支，最後才執行備份回復驗證與 production deploy。
衝突、測試、備份或部署任一失敗都會停止並在 `out/release-train/` 留下 JSON
receipt。

安裝預設為 dry-run，每小時只產盤點 receipt：

```bash
./scripts/install_hourly_release_train.sh
```

啟用 production 必須同時提供兩個命令。備份命令需把 JSON 寫到
`$TRUSTFORGE_BACKUP_RECEIPT`，內容至少為
`{"archive":"/absolute/backup.tar.gz","restore_verified":true}`；部署命令應呼叫
既有 immutable A/B deployment workflow。缺任一設定會 fail-closed：

```bash
TRUSTFORGE_RELEASE_BACKUP_CMD='<approved backup and restore-drill command>' \
TRUSTFORGE_RELEASE_DEPLOY_CMD='bash deploy/deploy_ec2.sh' \
./scripts/install_hourly_release_train.sh --execute
```

目前核定的唯讀 production 備份命令是
`bash deploy/backup_production_release.sh`：它封存 active/previous pointer、
active immutable artifact 與 manifest 成 `tar.gz`，重新解壓核對 SHA-256，並確認
cost ledger DynamoDB PITR 已啟用；不寫 AWS、不碰 schema。

此排程不會啟動或變更 Hermes、資料收集、web 或 frontend daemon。

> 不走 App Runner 自動化。流程：`git push` → pre-push hook 跑測試 → 綠 → AWS CLI 部署到 Lambda。
> Lambda 在免費方案內可用、每月 100 萬請求免費；App Runner 不在免費內故不採用。

## Immutable A/B Artifact Identity（#728）

`deploy_ec2.sh` 現在使用 content-addressed 部署流程（SHA-256 digest），而非固定 key 覆寫 S3。

### Artifact layout（S3 `trustforge-deploy-<ACCT>`）

```
artifacts/
  <sha256_digest>/         # 每個 build 有獨立前綴
    artifact.zip           # content-addressed ZIP
    manifest.json          # ReleaseManifest v1
  index.jsonl              # append-only 部署紀錄
pointers/
  active.json              # 目前 production 指到的 digest
  candidate.json           # 本次部署的 digest
  previous.json            # 前一個 production digest（rollback 目標）
retention/
  index.jsonl              # 歷史 index（供 retention 政策引用）
  policy.json              # retention 政策設定
```

### Deploy flow

1. **Build**：打包 zip → 計算 SHA-256 digest → 生成 `ReleaseManifest` → 上傳到 `artifacts/<digest>/`
2. **Candidate**：寫 `pointers/candidate.json`
3. **Deploy**：下載 candidate → 解壓 → `verify_deployed_manifest` fail-closed 驗證 → restart → `web healthz` gate
4. **Promote**：`candidate.json` → `active.json`，舊 `active.json` → `previous.json`

### ReleaseManifest（`trustforge.release-manifest/v1`）

| Field | Description |
|-------|-------------|
| `artifact_digest` | `sha256:<hex>` of the zip file |
| `git_sha` | Commit hash at build time |
| `app_version` | TrustForge version from `__version__` |
| `kernel_contract_version` | Kernel contract schema version |
| `kernel_resolution_version` | Direction resolution policy version |
| `core_content_hash` | SHA-256 over `src/trustforge_core/*.py` |
| `config_snapshot_identity` | `sha256:<digest>` of canonical config JSON |
| `build_timestamp` | ISO 8601 UTC |
| `build_host` | Hostname where build ran |

### A/B app endpoint identity（#733）

真正的 A/B app 仍由 production entrypoint `python -m trustforge.web` 提供服務；
每個 immutable release instance 必須設定
`TRUSTFORGE_RELEASE_IDENTITY_REQUIRED=1`，並提供以下五個值：

| env | runtime read-only input |
|-----|-------------------------|
| `TRUSTFORGE_ENDPOINT_MANIFEST_PATH` | build/release plane 簽好的 endpoint manifest |
| `TRUSTFORGE_RELEASE_MANIFEST_PATH` | 同一份 artifact 的 `ReleaseManifest` |
| `TRUSTFORGE_RELEASE_ARTIFACT_PATH` | app 實際啟動來源的 immutable artifact ZIP |
| `TRUSTFORGE_ENDPOINT_MANIFEST_KEYRING_PATH` | 只含 Ed25519 public keys 的 keyring |
| `TRUSTFORGE_RELEASE_ORIGIN` | router 使用的固定 loopback origin（含 port） |

啟動時會確認簽章、key role、loopback origin 及 artifact digest 綁定；缺檔、
符號連結、非 regular file、不安全 owner/permissions、錯誤簽章或 digest
不一致都會在 bind socket 前中止。runtime **不得**持有 endpoint manifest
private key。驗證通過後，app 於
`GET /.well-known/trustforge-release-manifest` 回傳凍結的 canonical JSON，
供 release router 在每次轉送前驗證。

簽署是 build/release-plane 步驟（不是 production installer）：

```bash
.venv/bin/python scripts/build_endpoint_manifest.py \
  --release-manifest /absolute/build/manifest.json \
  --origin http://127.0.0.1:18081 \
  --key-id endpoint-2026-07 \
  --private-key /absolute/offline/endpoint-ed25519.key \
  --output /absolute/build/endpoint-manifest.json
```

private key 必須是 owner-only 的 32-byte raw Ed25519 seed；output 必須是尚未
存在的絕對路徑，以 `O_EXCL` 建立為 read-only。A/B systemd unit 與 transactional
installer 的實際接線屬下一層工作，本層不會自行安裝、enable 或部署服務。

### Verification gates（fail-closed）

| Gate | Location | Checks |
|------|----------|--------|
| Pre-deploy | `deploy/verify_release.py` | Manifest完整性、artifact_digest matching、git_sha matching、core_content_hash matching、dirty build reject |
| Deployed (EC2) | `src/trustforge/verify_deployed_manifest.py` | 遠端 artifact 重新比對 digest/core_hash/config snapshot、zip entry 完整性、config drift detection |

### Config Snapshot（`src/trustforge/config_snapshot.py`）

快照 non-secret environment config（systemd env: BEDROCK_MODEL_ID, CSP_MODE, cache backends, etc.）為 canonical JSON。機敏 token 值（ADMIN_TOKEN, LIVE_TOKEN, SSM_PREFIX）只記錄 boolean presence，不記錄值。

### Retention 政策

- **Observation window**：24h（build 時間在 24h 內的 artifact 受保護）
- **Canary window**：10min（上傳後 10min 內的 artifact 受保護）
- **Pointer protection**：active/candidate/previous.json 指到的 artifact 永遠受保護
- **Dry-run**：`python scripts/apply_retention_policy.py --dry-run` 只產報告
- **Execute**：`python scripts/apply_retention_policy.py --execute --force` 實際刪除

### Rollback

`deploy_ec2.sh` 在 healthz gate 失敗時自動讀取 `pointers/previous.json`，下載/驗證舊 artifact 並重啟。

## 本機排程（macOS launchd / Linux systemd --user）

本機排程不使用含特定使用者絕對路徑的靜態 plist。安裝器從自身位置找出 repo；
如需覆寫可設 canonical、已存在的 `TRUSTFORGE_HOME`，symlink 或非 canonical
路徑會 fail-closed。這套本機安裝器不修改 production installer。

```bash
# 只產生檔案供檢查，不呼叫 launchctl/systemctl
./deploy/install_local_scheduler.sh --render-only --output-dir /tmp/trustforge-scheduler

# 寫入使用者層排程並啟用；UI 預設不裝，需明確 opt-in
./deploy/install_local_scheduler.sh
./deploy/install_local_scheduler.sh --with-ui

# 寫入但不啟用
./deploy/install_local_scheduler.sh --no-enable
```

macOS plist 一律由 `scripts/install_launch_agent.py` 透過 `plistlib` 產生，不用
`sed` 改 XML。Linux 僅建立 user units。測試可用
`TRUSTFORGE_SCHEDULER_OS`、`TRUSTFORGE_LAUNCHCTL`、
`TRUSTFORGE_SYSTEMCTL` 注入假平台／命令。

`./deploy/uninstall_local_scheduler.sh` 只移除固定 TrustForge labels/units，
保留 `out/` logs 與 SQLite 資料。

## 一次性前置（🧑 你做，AI 不碰憑證/IAM）

1. **安裝 AWS CLI**
   ```bash
   brew install awscli   # 或：python3 -m pip install --user awscli
   aws --version
   ```
2. **設定憑證**（你在 IAM 建一個有部署權限的使用者，拿 access key 後）：
   ```bash
   aws configure   # 輸入 Access Key / Secret / region=ap-southeast-2
   ```
   建議該 IAM 使用者政策：`lambda:*`、`iam:PassRole`（傳執行角色）、`logs:*`。
3. **建 Lambda 執行角色**（一次）：
   ```bash
   aws iam create-role --role-name trustforge-lambda-exec \
     --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
   aws iam attach-role-policy --role-name trustforge-lambda-exec \
     --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
   # 之後要真實 Bedrock，再加 bedrock:InvokeModel 的 inline policy
   export ROLE_ARN=$(aws iam get-role --role-name trustforge-lambda-exec --query Role.Arn --output text)
   ```
4. **啟用 pre-push CD hook**：
   ```bash
   git config core.hooksPath .githooks
   ```

## 平時：push 即 CD

```bash
git push            # → pre-push 跑 pytest；綠 → deploy_lambda.sh 部署 → 印出 Live Demo URL
TRUSTFORGE_NO_CD=1 git push   # 只跑測試、不部署
```

## 手動部署 / 調參

```bash
ROLE_ARN=<arn> FUNCTION_NAME=trustforge-demo REGION=ap-southeast-2 \
  bash deploy/deploy_lambda.sh
```

## 點亮真實 Bedrock（離線示範→真模型）

1. 給執行角色加 `bedrock:InvokeModel`（+`InvokeModelWithResponseStream`）於選定模型。
2. 提交 Anthropic use case（Bedrock console 橫幅，一次性）。
3. 在 `deploy_lambda.sh` 的 `ENVVARS` 加 `BEDROCK_MODEL_ID=<apac.claude profile>` 與 `AWS_REGION`。
4. 之後請求帶 `?live=1` 走真實 Bedrock；預設仍離線示範。

## 注意
- 互動 demo 單次請求秒級，遠低於 Lambda 15 分鐘上限（上限只在「單請求跑滿全程」才咬）。
- 部署失敗不擋 push（pre-push 設計：測試硬閘、CD 盡力）。
- 成本：Lambda 免費額度通常 cover demo；Bedrock 按 token，用 credits。建議設 AWS Budgets 告警。

## 排程 fetcher（階段2：連接器快取，`scripts/fetch_scheduler.py`）

背景見 `src/trustforge/ingestion/cache.py` 模組 docstring：產品線上路徑一律
讀快取（`CachedSource`），**不**每個 request 直接打真連接器 API（news/social/
onchain/regulatory 各有 rate limit，reddit 尤其容易 429/封鎖）。真正打真 API
只發生在本腳本的排程執行，寫入快取供產品讀取。

### 一次性前置（🧑 你做，AI 不碰 AWS 憑證/建表）

1. **建 DynamoDB 表**（`CACHE_BACKEND` 預設 `dynamodb`）：
   - 表名：`trustforge-connector-cache`（可用 `TRUSTFORGE_CACHE_TABLE` env 覆寫）
   - PK：`source_id`（String）　SK：`coin`（String）
   - 啟用原生 TTL，屬性名 `ttl`（Number，epoch 秒；寫入時用「硬過期時限」
     換算，見下方 refresh 間隔 vs 硬過期說明，**不是**排程 refresh 間隔本身，
     避免 item 在還「夠新鮮」時就被背景清掉）
   - IAM：執行排程的機器/角色需要 `dynamodb:GetItem`、`dynamodb:PutItem`
   - ⚠️ 沒建表/沒憑證時：**讀**（`cache_get`）會自動 fallback 讀本地 JSON；
     **寫**（`cache_set`）預設**不會**自動 fallback（見下方「cache 寫入失敗
     不能被靜默吞掉」），排程會回報明確失敗、exit 非零。
2. 本機開發設定 `CACHE_BACKEND=sqlite`，Web 與排程共用
   `out/trustforge.sqlite3`（可用 `TRUSTFORGE_SQLITE_PATH` 覆寫）。舊版 JSON
   快取可用 `python scripts/migrate_json_cache_to_sqlite.py` 一次搬入；SQLite
   使用 WAL，適合本機 Web 與排程並行讀寫。多台產品機仍使用 DynamoDB，不能
   共享單機 SQLite 檔案。

### refresh 間隔 vs 硬過期時限——刻意分開，別設成一樣

**⚠️ 重要**：排程「多久打一次真 API」（refresh 間隔）跟 `CachedSource`「多久
沒更新就判定 cache 不可用」（硬過期時限）是**兩個不同的數字**，硬過期時限
= `STALE_AFTER_MULTIPLIER`（3）× refresh 間隔（見 `cache.py::stale_after_for()`）。
若兩者設成一樣（如 10min cron + 10min TTL），cron 稍微 jitter 或單次真呼叫
失敗，就會出現「排程還沒跑到、但 cache 已經『剛好』過期」的例行空窗，讓產品
在每輪排程之間必然有一段時間讀不到資料——這不是罕見的邊界情況，是**每一輪
都會發生**的結構性問題。3 倍 margin 代表允許連續 2 次 refresh 失敗，cache
仍撐得住，直到第 3 次才真的觸底降級。

| 來源類別 | refresh 間隔（排程多久打一次） | 硬過期時限（3倍 margin，CachedSource 用） |
|---|---|---|
| coindesk / decrypt / cryptopanic / blockchain-info | 15 min | 45 min |
| reddit-cryptocurrency / reddit-bitcoin | 30 min | 90 min |
| alternative-me-fng / sec-gov | 60 min | 180 min |

改任何來源的 refresh 間隔（`cache.py::DEFAULT_REFRESH_INTERVAL_SECONDS`），
硬過期時限會自動跟著等比例調整（`DEFAULT_STALE_AFTER_SECONDS` 是衍生值，
不是獨立手填的第二份數字），不會忘記同步改到走回這個坑。

### cache 寫入失敗不能被靜默吞掉

`scripts/fetch_scheduler.py` 真的呼叫到真 API、但寫入 cache backend（如
DynamoDB）失敗時（憑證過期/IAM 少權限/網路問題），**視為這次排程真失敗**：
`main()` 回傳非零 exit code，cron/systemd 應該對非零 exit 告警（不要只看
程式有沒有當掉）。預設也**不會**悄悄 fallback 寫本地 JSON 卻回報成功——那樣
只有寫入的那台排程機看得到資料，產品讀取路徑（走 DynamoDB）完全看不到，會
變成「DynamoDB 早就掛了但監控說一切正常」的假象。

dev/CI 沒有真 AWS、想要本地持久快取時，直接用 SQLite；只有相容舊環境時才
使用 JSON fallback：

```bash
# 本機 primary backend（推薦）
CACHE_BACKEND=sqlite python3 scripts/fetch_scheduler.py

# 相容舊環境：env（不用改 code）
TRUSTFORGE_CACHE_JSON_FALLBACK=1 python3 scripts/fetch_scheduler.py

# 相容舊環境：直接用 JSON 當 primary backend
CACHE_BACKEND=json python3 scripts/fetch_scheduler.py
```

### 平時：cron 或 systemd timer 排程執行

各來源 rate limit 不同（reddit ~10/min 需 ≥15-30min/feed；FNG/SEC 30-60min；
news/blockchain 10-15min，見上方 refresh 間隔對照表 /
`cache.py::DEFAULT_REFRESH_INTERVAL_SECONDS`）。兩種排法皆可：

**方式 A（推薦，省心）**：每 5-15 分鐘跑一次「全部來源」，讓腳本內建的新鮮度
守門（未達各自 refresh 間隔自動跳過，不重打）自然分散頻率：

```cron
*/10 * * * * cd /path/to/trustforge && AWS_REGION=ap-southeast-2 python3 scripts/fetch_scheduler.py >> out/fetch_scheduler.log 2>&1
```

**方式 B**：每個來源各自一條 cron line，間隔對齊其 TTL（想精準控頻率、或想
把某來源獨立出來單獨重試/告警時用）：

```cron
*/15 * * * *  cd /path/to/trustforge && python3 scripts/fetch_scheduler.py --source coindesk --source decrypt --source cryptopanic --source blockchain-info
*/30 * * * *  cd /path/to/trustforge && python3 scripts/fetch_scheduler.py --source reddit-cryptocurrency --source reddit-bitcoin
0    * * * *  cd /path/to/trustforge && python3 scripts/fetch_scheduler.py --source alternative-me-fng --source sec-gov
```

**Axis C #1（task #23）：`--snapshot` 快照寫入者**——獨立 cron line，**不**
跟上面「打真連接器 API」的方式 A/B 共用同一條，cadence 也刻意分開（見
`scripts/fetch_scheduler.py::SNAPSHOT_REFRESH_INTERVAL_SECONDS`）：每 15
分鐘對 `COIN_POOL` 5 幣各跑一次 real-off `pipeline.run(data_mode="live",
llm_mode="off")`（純讀既有 cache 運算，$0，不打真連接器、不打 Bedrock），
把精華信任快照寫入 `__trust_snapshot__:{coin}`，並把首頁「多幣總覽」HTML
blob 寫入 `__trust_overview_html__`——供 `web.py::_render_home_page()` 走
單次短 timeout 讀路徑顯示，不在首頁 request 當下逐幣讀 DynamoDB：

```cron
*/15 * * * * cd /path/to/trustforge && AWS_REGION=ap-southeast-2 python3 scripts/fetch_scheduler.py --snapshot >> out/fetch_scheduler_snapshot.log 2>&1
```

**systemd timer 等效寫法**（`fetch-scheduler.service` + `fetch-scheduler.timer`，
`OnUnitActiveSec=10min` 對應方式 A 的 `*/10 * * * *`）：

```ini
# /etc/systemd/system/fetch-scheduler.service
[Unit]
Description=TrustForge connector cache fetch scheduler

[Service]
Type=oneshot
WorkingDirectory=/path/to/trustforge
Environment=AWS_REGION=ap-southeast-2
ExecStart=/usr/bin/python3 scripts/fetch_scheduler.py
```

```ini
# /etc/systemd/system/fetch-scheduler.timer
[Unit]
Description=Run TrustForge fetch scheduler periodically

[Timer]
OnBootSec=1min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now fetch-scheduler.timer
```

### 其他常用旗標

```bash
python3 scripts/fetch_scheduler.py --list-sources        # 列出所有已知來源，不打 API
python3 scripts/fetch_scheduler.py --dry-run              # 只印會呼叫哪些 (來源,幣別)，不真打
python3 scripts/fetch_scheduler.py --source reddit-bitcoin --force   # 強制略過新鮮度守門（節制用，避免 429）
```

⚠️ 手動驗證/除錯時務必節制：本腳本是唯一打真連接器 API 的地方，reddit 尤其
別狂打（雲端 IP 本來就容易被判 403/429）。

### Exit code / 監控

- `0`：全部目標這次都真的刷新成功（真呼叫 + cache 寫入都成功；或本來就沒有
  目標要跑）。
- `1`：至少一個目標這次沒有真的刷新成功，原因可能是下列任一種（`stderr`
  訊息會標明是哪一種）：
  - 「真呼叫本身失敗」（逾時/429/憑證錯/上游故障）——**即使全部來源都這樣
    失敗**也一樣回非零，不會因為「至少嘗試過」就誤報成功；連續多輪都這樣
    會撞上 cache 硬過期、產品端開始真的斷資料，所以這個訊號不能被忽略；
  - 「真呼叫成功，但 cache 寫入失敗」（沒有真的持久化）——多半是 DynamoDB
    憑證/IAM/網路故障的直接訊號，同樣不該被忽略或被本地 JSON fallback
    悄悄蓋過去（見上方「cache 寫入失敗不能被靜默吞掉」）。
  - cron/systemd 應該對任何非零 exit 告警，不用區分是上述哪一種——兩種都
    代表「這次排程沒有把資料刷新進 cache」。

## 前後端分離 Phase 3：cutover 拓樸（task #28）

> ⛔ 以下腳本只負責「把拓樸架好、隨時可切、但預設不切」。真正把使用者流量
> 切到 React 前端（cutover）需要 **CEO+CISO+CPO 三審 + 老闆簽核**，見
> `docs/architecture/PLAN-frontend-backend-split.md` P3。本節不涉及任何真實 AWS/生產操作。

分三個階段，各自獨立、可回滾：

| 階段 | 腳本 | 做什麼 | 預設行為 |
|------|------|--------|----------|
| 0（既有） | `deploy_ec2.sh` | 建 EC2、裝 python，port 80 直接對外 | 不變 |
| 1 | `deploy_frontend_nginx.sh` | 疊加 nginx 層 + 上傳 React dist + **四份** nginx conf 候選（legacy/react/react-http/legacy-tls）；python 收斂只聽 `127.0.0.1:8080` | 預設啟用 `nginx-legacy.conf`（全部原樣轉發給 python，功能與階段 0 逐字等價） |

React dist 佈署本身也是 versioned release + atomic symlink（codex 複審
HIGH）：每次下載/解壓到全新的 `frontend/releases/<ts>/`，完全不動現在活著
的 `frontend/current`（nginx `root` 指的就是這個 symlink），驗證通過後才在
ERR trap 保護下把 `current` atomic 切過去；前一版 release 目錄刻意保留，
失敗時 rollback 能把 `current` 切回去、內容原封不動、立刻可服務——不再是
舊版「先 `rm -rf` 現正 serving 的目錄再解壓」那種下載中/解壓失敗就直接讓
active 站壞掉、且沒有任何 rollback 機制救得回來的做法。
| 2 | `cutover_switch.sh react\|react-http\|legacy` | 秒切 nginx conf（symlink）+ 同步 python 的 `TRUSTFORGE_CSP_MODE` | `react`/`react-http` 模式**強制**要求 `TRUSTFORGE_CUTOVER_CONFIRMED=yes`（視為三審+簽核完成的憑證），否則直接中止；`react-http` **另外**預設禁止（見下方），需明確 `TF_ALLOW_INSECURE_HTTP_CUTOVER=yes` 才放行 |

**`react` vs `react-http` 怎麼選**：兩者是同一套 React 前端拓樸，差別只在
nginx 層有沒有 TLS。**DNS 已就緒**（`trustforge.hurricanesoft.com.tw →
13.211.110.218`，見 `deploy/nginx.conf`／`deploy/TLS-SETUP.md`）——**production
唯一路徑是 `react`**（TLS 版，需先跑 `deploy/setup_tls.sh` 簽出憑證，見下方
「完整 cutover runbook」）。

⛔ **`react-http` 預設禁止用於 production**（codex 複審 HIGH）：既然
production 已經有 domain + TLS，`react-http`（React 前端跑在純 HTTP、無
TLS）就不該再是 production cutover 選項——中間人可以竄改沒有 TLS 保護的
JS/API 回應內容本身，CSP header 只能限制「已收到」的內容能做什麼，擋不住
封包被竄改這件事。`cutover_switch.sh react-http` 現在預設會直接拒絕、非零
結束，需要明確設定 `TF_ALLOW_INSECURE_HTTP_CUTOVER=yes` 才會放行——這是
刻意設計的例外路徑，僅供**極早期 bare-IP、還沒有 domain 時**的暫時
fallback（或憑證還沒就緒前想先驗證 React 拓樸本身），不是常態 production
用法。`react`（TLS）與 `legacy` 完全不受這道關卡影響。

production React 的唯一路徑是：`legacy`（ACME challenge 用）→
`deploy/setup_tls.sh` 簽發憑證 → `react`（TLS）cutover。

python 端 `TRUSTFORGE_CSP_MODE` `react`/`react-http` 兩者都設成 `react`
（CSP 指令集本身一致，`web.py` 不需要為 `react-http` 多開一個分支）。

### 涉及的 config-gated 環境變數（`src/trustforge/web.py`）

- `TRUSTFORGE_TRUST_PROXY`（預設關）：關閉時 rate-limit 用 `client_address[0]`
  （現況不變）；開啟時改讀 `X-Real-IP` / `X-Forwarded-For`（nginx 已設定
  `proxy_set_header X-Real-IP $remote_addr`）。**只在 python 只對內監聽
  （`TRUSTFORGE_BIND_HOST=127.0.0.1`）時才安全開啟**——`main()` 會在偵測到
  `TRUST_PROXY=1` 但綁定非 127.0.0.1 時強制改綁並記警告，避免被繞過 nginx
  直接偽造 header 打。
- `TRUSTFORGE_CSP_MODE`（預設 `legacy`）：`legacy` = 目前 SSR 用的舊 CSP
  （逐字不變）；`react` = 給 React 前端用的放寬版 CSP（`script-src 'self'`
  等，見 `web.py` 內 `_CSP_REACT`）+ `X-Frame-Options: DENY` +
  `Referrer-Policy: strict-origin-when-cross-origin`。cutover 前後兩者不
  混用（nginx 端也對應切換）。

### nginx conf 四個變體

- `deploy/nginx-legacy.conf`：cutover 前的預設/回滾安全值。**刻意只寫
  HTTP（80）**，不預先手刻 443/TLS——避免部署當下引用尚不存在的憑證檔案
  導致 `nginx -t`/reload 失敗。TLS 憑證由 `certbot certonly --webroot`
  簽發（見 `deploy/TLS-SETUP.md`——**不用** `--nginx` plugin，本檔
  `server_name _` 從未被自動改寫成真實 domain，`--nginx` non-interactive
  配對不到會失敗；本檔已內建
  `location ^~ /.well-known/acme-challenge/` 直接從檔案系統服務 HTTP-01
  challenge，跟 `server_name` 無關）。全部（`/`、`/api/*`、`/healthz` 等）
  原樣轉發給 `127.0.0.1:8080`。
- `deploy/nginx.conf`：cutover 後的目標拓樸，**主線（domain 已就緒）**。
  `server_name trustforge.hurricanesoft.com.tw`；`/` serve React 靜態檔
  （`frontend/current` symlink，見上方 versioned release 說明）、`/api/`
  轉發給 python，80→443 redirect + HSTS，
  React 用 CSP 只加在 `location /`（不外溢到 `/api/` 的 JSON 回應）；
  `ssl_certificate`/`ssl_certificate_key` 讀
  `/etc/letsencrypt/live/trustforge.hurricanesoft.com.tw/`（見
  `deploy/TLS-SETUP.md`、`deploy/setup_tls.sh`）。
- `deploy/nginx-react-http.conf`：同一套 React 拓樸的 **HTTP-only fallback
  版**（bare IP、無 domain，或憑證還沒簽出來前想先驗證拓樸本身時用）。跟
  `nginx.conf` 差別只在**只有 port 80、無 TLS、無 301 redirect、無 HSTS**
  （HSTS 只在 HTTPS 下有意義，HTTP-only 站台加了不會生效，故不加）。
- `deploy/nginx-legacy-tls.conf`：`nginx-legacy.conf` 的 **443/TLS 版**，
  react→legacy 緊急回滾**專用**（codex 複審 HIGH：HSTS-safe rollback）。
  ⛔ 為什麼需要它：`nginx.conf` 送一年 HSTS，瀏覽器記住後該 domain 之後
  一年內一律強制走 https，`nginx-legacy.conf` 只監聽 80——若回滾切上純
  HTTP 版，回訪過（吃過 HSTS）的使用者連不上任何東西，回滾本身失去意義、
  甚至讓事故惡化。這份 conf 拓樸跟 `nginx-legacy.conf` 完全一樣（SSR/API
  全部原樣轉發給 python，CSP 由 python 端 `CSP_MODE=legacy` 自己下），差別
  只在多了 443/TLS + HSTS + 80→443 canonical redirect（跟 `nginx.conf` 的
  80 server block 設計一致），憑證路徑跟 `nginx.conf` 共用同一張（同一個
  domain，回滾不需要另外簽）。`cutover_switch.sh` 偵測到
  `/etc/letsencrypt/live/<domain>/fullchain.pem` 已存在時，legacy 回滾會
  自動選用這份而不是 HTTP-only 版；還沒簽過憑證的 pre-cert 現況（ACME
  bootstrap）則維持用 HTTP-only 版，行為不變——偵測邏輯見下方
  `cutover_switch.sh` 段落。真的起本機 nginx + python 驗證過 443 serve SSR
  + HSTS header 存在，見 `deploy/test_nginx_legacy_tls_conf.sh`。

  ⛔ **憑證偵測不是只有 explicit `legacy` mode 才做**（codex 複審 HIGH：
  自動 trap rollback 繞過 legacy-tls 保護）：`react`/`react-http` cutover
  在 nginx 已 reload、public smoke check 卻失敗時，觸發的是 `cutover_switch.sh`
  內建的自動 ERR-trap rollback（不是使用者手動打 `legacy` mode），這條路徑
  一樣會偵測憑證是否存在——若切換前是 `nginx-legacy.conf`（HTTP-only）且
  憑證已存在，rollback 還原目標改成 `nginx-legacy-tls.conf`，並且**還原後
  真的用 `curl --resolve <domain>:443:127.0.0.1 https://<domain>/healthz`
  驗證 443 SSR 可達**才回報 rollback 成功，驗不過直接判 ROLLBACK-FAILED
  （exit 97），不謊報半殘的「symlink 指對但打不通」為成功。測試見
  `deploy/test_cutover_switch.sh` 場景 32-34。

  **什麼時候用哪份**（cutover 決策，供 CEO/CISO/CPO 三審參考）：
  - domain + certbot 簽發憑證已就緒（主線現況）→ `deploy/cutover_switch.sh react`
  - bare IP、無 domain，或憑證尚未簽出 → `deploy/cutover_switch.sh react-http`（fallback）
  - 緊急回滾／SSR 一週觀察期 → `deploy/cutover_switch.sh legacy`（憑證已存在
    時自動改用 `nginx-legacy-tls.conf`，443 服務、保留 HSTS；pre-cert 現況
    仍是 `nginx-legacy.conf`，HTTP-only）

  跟 `nginx.conf` 一樣，`/` 下 CSP/X-Frame-Options 等安全 header 的實際
  生效點是 `location = /index.html`（精確比對），不是 `location /`——
  `try_files ... /index.html` fallback 是內部重導向，會拿 `/index.html`
  重新跑一次 location 比對，兩邊 add_header 務必同步，見
  `deploy/nginx-react-http.conf` 內註解與 `deploy/test_nginx_react_http_conf.sh`。

  **cutover 完成後不是只驗語法**（codex 五次複審，HIGH）：`cutover_switch.sh`
  Step 4 完成後驗證，除了 active symlink/CSP_MODE/python 直連 `healthz`，
  在清除 rollback trap **之前**還加了 Step 4b public nginx（port 80）smoke
  check——`nginx -t` 只驗語法，不證 React dist 目錄真的存在可服務、SPA
  路由/try_files 沒壞；只驗 python 直連的話，dist 缺失/nginx 層本身有問題
  時 python 還是健康，腳本會謊報成功。Step 4b 實際打
  `http://127.0.0.1/`＋`/analyze`（斷言 200＋CSP header＋含
  `<div id="root">` React dist 特徵，順便驗證 try_files SPA fallback）、
  `http://127.0.0.1/api/health`（斷言 200＋`{"ok": true, ...}` JSON，驗
  nginx→python 的 `/api/` proxy 是通的）；legacy 模式則打
  `http://127.0.0.1/healthz`（驗 nginx 對 SSR 的全轉發鏈路）。任一失敗都
  沿用同一顆 ERR trap 觸發既有 rollback（不是新的失敗路徑），失敗注入測試
  見 `deploy/test_cutover_switch.sh` 場景 17-25。

### TLS

`deploy/TLS-SETUP.md` + `deploy/setup_tls.sh`：domain 已就緒
（`trustforge.hurricanesoft.com.tw → 13.211.110.218`），但**這個任務仍是
config-only，沒有實際簽發憑證**——`setup_tls.sh` 預設只印出會執行的內容、
不真的呼叫 `aws ssm`/`certbot`，需同時設
`TRUSTFORGE_RUN_CERTBOT=yes` + 真實 `ADMIN_EMAIL` 才會真跑（CEO 真部署時
決定）。**順序鐵則**：certbot 走 HTTP-01 challenge，必須先跑
`deploy_frontend_nginx.sh`（或已切 `react-http`）讓 nginx 在 80 port 上
可服務，才能跑 `setup_tls.sh`；反過來（先切 TLS 版 `nginx.conf`）會讓
nginx 因為憑證檔案不存在而 `nginx -t` 失敗。完整順序見下方「完整 cutover
runbook」。

### 完整 cutover runbook（react-TLS domain cutover）

1. **DNS**：`trustforge.hurricanesoft.com.tw → 13.211.110.218`（✓ 已完成）。
2. **deploy legacy**（nginx 在 80 上先服務）：`bash deploy/deploy_frontend_nginx.sh`
   ——預設啟用 `deploy/nginx-legacy.conf`。
3. **certbot 簽發**：
   `ADMIN_EMAIL=<真實 email> TRUSTFORGE_RUN_CERTBOT=yes bash deploy/setup_tls.sh`
   ——簽出 `/etc/letsencrypt/live/trustforge.hurricanesoft.com.tw/{fullchain,privkey}.pem`。
4. **cutover 到 react（TLS 版）**：
   `TRUSTFORGE_CUTOVER_CONFIRMED=yes deploy/cutover_switch.sh react`
   （需 CEO+CISO+CPO 三審 + 老闆簽核）。
5. **驗證 https**：`curl -I https://trustforge.hurricanesoft.com.tw/`，
   確認 200 + HSTS/CSP header 皆存在；`cutover_switch.sh` Step 4b 的
   public smoke check 也會在 cutover 當下自動驗證這件事（見上方「nginx
   conf 三個變體」段落說明）。

回滾：`deploy/cutover_switch.sh legacy`（秒切回 SSR 全轉發，不動憑證，見
`deploy/TLS-SETUP.md`「回滾」段落）。**cutover 4 步之後**（憑證已存在），
這裡的 legacy 回滾會自動偵測到 `fullchain.pem` 存在，改用
`nginx-legacy-tls.conf`（443 服務、保留 HSTS）而不是 HTTP-only 版，避免
HSTS 讓回訪過的使用者連不上（codex 複審 HIGH，見上方「nginx conf 四個
變體」段落）；步驟 2（cutover 4 之前、憑證還沒簽發時）的 legacy 仍是
HTTP-only 版，行為不變。

### `cutover_switch.sh` exit code 慣例（供維運/監控）

`cutover_switch.sh`（不管是遠端腳本本身，還是本機呼叫它的 production SSM
wrapper）用以下 distinct exit code，讓自動化/監控能分清楚失敗種類，不要
一律當成「隨便一種失敗」處理（`react-http` mode 沿用同一套 guarded
transaction/flock/rollback 控制流程與 exit code 慣例，只是 candidate conf
換成 `react-http.conf`、python 端 `TRUSTFORGE_CSP_MODE` 沿用 `react`，見
`deploy/test_cutover_switch.sh` 場景 14-16）：

| exit code | 含義 | 是否已動過 mutation | 建議動作 |
|-----------|------|----------------------|----------|
| `0` | 成功切換（或成功回滾但流程本身正常結束的分支不會走到這裡） | 是，已切到目標狀態 | 無 |
| `1` | 一般失敗（候選設定驗證失敗等）；也是任何未知/未定義 ResponseCode 的保守 fallback | 視情況——候選驗證失敗一律**沒有**任何 mutation；其餘一般失敗請查 log 判斷 | 查 log，通常可直接重試 |
| `97` | `ROLLBACK-FAILED`：自動回滾**沒有完全成功**（見腳本內詳細訊息與手動復原指令） | **可能處於半殘狀態**，不要假設已還原 | 立即人工介入，照腳本印出的手動復原指令逐項核對 nginx symlink／CSP_MODE／healthz |
| `98` | lock contention：另一個 cutover 呼叫正在進行中，本次直接中止 | **完全沒有** mutation | 等目前那個呼叫跑完再重試，不需要人工介入 |
| `99` | 相符實例數不是剛好 1 台（tag `Name=trustforge-demo`、`running`）——0 台或多台一律 fail-closed 中止，不會靜默選第一台（codex 複審 HIGH：以前 `awk '{print $1}'` 會默默選到 stale/非 prod 實例，正牌 prod 沒切卻回報成功）；跟 `deploy/setup_tls.sh` 已有的同名判斷一致 | **完全沒有** mutation（連 SSM 都還沒發） | 0 台：先確認 EC2 是否真的在跑；多台：先手動確認/收斂到剛好一台 running 的 trustforge-demo 實例，再重試 |

production SSM wrapper（`cutover_switch.sh` 尾段呼叫 `aws ssm
send-command`/`get-command-invocation` 那段）會讀 `get-command-invocation`
的 `ResponseCode`（遠端指令實際的 exit code），把 97/98 原樣傳遞成 wrapper
自己的 top-level exit code，不會全部塌成 1（codex 四次複審修正項）。

### 本機驗證方式（禁真 AWS/生產）

```bash
# 語法檢查
bash -n deploy/deploy_frontend_nginx.sh deploy/cutover_switch.sh deploy/setup_tls.sh
shellcheck deploy/deploy_frontend_nginx.sh deploy/cutover_switch.sh deploy/setup_tls.sh

# setup_tls.sh dry-run（不呼叫真 aws ssm/certbot，只印出會執行的遠端指令）
TF_SETUP_TLS_DRY_RUN=1 ADMIN_EMAIL=test@example.com TRUSTFORGE_RUN_CERTBOT=yes \
  bash deploy/setup_tls.sh

# nginx conf 語法（本機 brew nginx，legacy/react-http 不需憑證；react 需
# 暫時自簽憑證才能測 443 block，見 commit message／PR 描述的驗證步驟）
nginx -t -c <harness 檔案 include 對應 conf>

# rate-limit-trust + CSP 邏輯的單元測試（mock，不連真 AWS）
python3 -m pytest tests/test_security.py tests/test_lambda_handler.py -q

# deploy_frontend_nginx.sh 的邏輯測試（完全 mock aws/npm，不連真 AWS/不真
# npm install）
bash deploy/test_deploy_frontend_nginx.sh

# cutover_switch.sh 的 guarded-transaction 控制流程測試（TF_CUTOVER_DRY_RUN
# 擷取遠端指令內容、本機沙箱 + mock nginx/systemctl/curl/flock 實際執行；
# 涵蓋 react 與 react-http 兩種 mode）
bash deploy/test_cutover_switch.sh

# cutover_switch.sh production SSM wrapper 的整合測試（mock aws CLI 本身，
# 不用 TF_CUTOVER_DRY_RUN，驗證 ResponseCode 97/98/1/0 正確傳遞成 wrapper
# 自己的 top-level exit code）
bash deploy/test_cutover_ssm_wrapper.sh

# deploy/nginx.conf（React + TLS）路由/安全 header 整合測試（真起本機
# nginx + stub dist + 本機 python，自簽憑證測 443）
bash deploy/test_nginx_react_conf.sh

# deploy/nginx-react-http.conf（React + HTTP-only，bare-IP 現況）路由/
# 安全 header 整合測試（真起本機 nginx + stub dist + 本機 python，純
# HTTP，額外斷言不帶 HSTS）
bash deploy/test_nginx_react_http_conf.sh

# deploy/nginx-legacy-tls.conf（legacy 回滾 + TLS，codex 複審 HIGH：
# HSTS-safe rollback）路由測試（真起本機 nginx + 本機 python SSR，自簽
# 憑證測 443，斷言 443 真的 proxy SSR + 帶 Strict-Transport-Security
# header，且 80→443 redirect 用 canonical domain）
bash deploy/test_nginx_legacy_tls_conf.sh
```

### 回滾

`deploy/cutover_switch.sh legacy`——秒切回 SSR 全轉發，不動 AWS 資源、不
重建實例。憑證已存在時自動改用 `nginx-legacy-tls.conf`（443 服務、保留
HSTS，codex 複審 HIGH：HSTS-safe rollback，見上方「nginx conf 四個變體」）；
pre-cert 現況仍是 HTTP-only 版，行為不變。

## 前端 CD（`.github/workflows/deploy-frontend.yml`，手動觸發）

> 消掉「前端改動要在本機手動跑 `deploy_frontend_nginx.sh`、還會撞本機 AWS
> session 過期」的痛點：改成 GitHub Actions 手動按鈕觸發部署，複用同一支
> `deploy/deploy_frontend_nginx.sh`（不重寫部署邏輯），AWS 認證走 OIDC（不放
> 長期 AWS access key 進 GitHub secret）。**觸發方式刻意是
> `workflow_dispatch`（手動），不是 push develop 自動部署**——一個壞 merge
> 就直接上生產風險太高，人為 gate 保留，只是把「按鈕」搬到 GitHub 上。

### 一次性前置（🧑 老闆做，AI 不碰 IAM/OIDC）

這是 IAM trust boundary，AI 只寫文件、不執行。老闆需要在 GitHub repo
設定＋AWS Console／CLI 完成以下設定，完成後把 role ARN 存進 GitHub repo
secret，workflow 才跑得動：

0. **建 GitHub environment `production` + 設 required reviewers**（harper
   複審 MEDIUM：把「觸發」跟「放行」分成兩個人——任何人按了
   `Run workflow` 只是送出請求，job 實際執行前還要卡在 environment
   protection rule，需要指定 reviewer 手動核准才會真的跑）：
   1. GitHub repo → Settings → Environments → **New environment**，名稱
      填 `production`（要跟 `deploy-frontend.yml` 裡 `environment:
      production` 完全一致，大小寫敏感）。
   2. 勾選 **Required reviewers**，加入至少 1 位審核人（建議跟觸發者不同
      人；老闆自己審或指定信任的人選）。
   3.（可選但建議）**Deployment branches**：限制只有 `main`／`develop`
      能用這個 environment，避免任意分支也能跑到生產部署。
   - 設定完成後，`workflow_dispatch` 觸發只是「排隊等待核准」，job 真正
     assume AWS role 之前會先卡在這一關，即使觸發者的 GitHub 帳號被盜、
     或誤點按鈕，沒有 reviewer 核准也不會動到生產。
1. **建 GitHub OIDC identity provider**（若帳號內尚未建過，一次性）：
   ```bash
   aws iam create-open-id-connect-provider \
     --url https://token.actions.githubusercontent.com \
     --client-id-list sts.amazonaws.com \
     --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
   ```
2. **建部署專用 IAM role**，trust policy **鎖到 GitHub environment
   `production`**（`sub` condition，**不是**鎖整個 repo 的任何分支/
   workflow）：
   ```bash
   cat > trust-policy.json <<'JSON'
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": { "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com" },
       "Action": "sts:AssumeRoleWithWebIdentity",
       "Condition": {
         "StringEquals": {
           "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
           "token.actions.githubusercontent.com:sub": "repo:cancleeric/trustforge:environment:production"
         }
       }
     }]
   }
   JSON
   aws iam create-role --role-name trustforge-frontend-deploy \
     --assume-role-policy-document file://trust-policy.json
   ```
   > ⚠️ **收斂說明（harper 複審 MEDIUM，這是本文件最重要的一條）**：
   > 最初草稿的 `sub` 用 `StringLike: "repo:cancleeric/trustforge:*"`——這
   > 個萬用字元會讓 repo 內**任何分支、任何 workflow、任何 PR**都能換到
   > 這個能碰生產的 role，跟這個 workflow 有沒有走 `environment:
   > production`、有沒有 required reviewers 核准完全無關（OIDC token 的
   > `sub` 只跟觸發來源有關，不會自動繼承 environment gate）。改成
   > `StringEquals` 精確鎖
   > `repo:cancleeric/trustforge:environment:production`（注意這裡改用
   > `StringEquals` 不是 `StringLike`，因為不再需要萬用字元）之後，AWS
   > 端會強制要求這次 assume-role 的 GitHub token **一定來自已經通過
   > environment `production` 核准關卡**的 job——就算有人在其他 workflow
   > 或分支拿到 `id-token: write` 權限，`sub` 對不上也換不到這個 role。
   > 這條 trust policy 收斂跟上面的「GitHub environment + required
   > reviewers」設定是**一體兩面**：environment 設定沒做，`sub` 這個值
   > 根本不會出現在 OIDC token 裡，trust policy 永遠比對失敗；`sub` 沒鎖
   > 到 environment，environment 的核准關卡再怎麼設也擋不住其他分支/
   > workflow 直接換到 role。兩者缺一不可。
3. **permission policy 限定 `deploy_frontend_nginx.sh` 實際會用到的最小
   集合**（別給 `*:*`）——腳本實際呼叫的 AWS API：`sts:GetCallerIdentity`、
   `ec2:DescribeInstances`／`StartInstances`／`DescribeVpcs`／
   `DescribeSecurityGroups`／`AuthorizeSecurityGroupIngress`（⚠️ harper
   複審 LOW：`AuthorizeSecurityGroupIngress` 這個 API 沒有 resource-level
   permission 可以限制「哪個 security group」，只能靠 IAM condition
   `ec2:ResourceTag` 或直接把 GroupId 寫進 `Resource` ARN——實際套用時
   務必用 `Condition` 或 `Resource` 鎖定 `deploy_ec2.sh` 建立的那個
   `trustforge-ec2-sg`（腳本用 `Name=trustforge-ec2-sg` tag 查出來的那
   個 SG），不要給這個 action 全帳號範圍的萬用 resource，否則等於能對
   帳號內任何 security group 開洞）、
   `s3:HeadBucket`／`CreateBucket`／`PutObject`（限
   `trustforge-deploy-<ACCOUNT_ID>` bucket）、`ssm:SendCommand`／
   `GetCommandInvocation`（限 `AWS-RunShellScript` document + tag
   `Name=trustforge-demo` 的實例）：
   ```bash
   # <policy.json> 是老闆依上述 API 清單自行撰寫/核定的最小權限 policy 檔
   # （repo 內刻意不放現成的 policy JSON，避免文件與實際套用內容 drift；
   # 需要草稿可請 CTO 依上述 API 清單產出草稿供老闆審，但正式套用是老闆的
   # gate，不是 AI 執行）
   aws iam put-role-policy --role-name trustforge-frontend-deploy \
     --policy-name trustforge-frontend-deploy-inline \
     --policy-document file://<policy.json>
   ```
4. **把 role ARN 存進 GitHub repo secret**（Settings → Secrets and
   variables → Actions → New repository secret）：
   - Name：`AWS_DEPLOY_ROLE_ARN`
   - Value：`arn:aws:iam::<ACCOUNT_ID>:role/trustforge-frontend-deploy`

以上全部完成前，`deploy-frontend.yml` 的 `Configure AWS credentials (OIDC)`
step 會因為讀不到有效的 `secrets.AWS_DEPLOY_ROLE_ARN`、或 environment 核准
沒過、或 assume-role 被 trust policy 的 `sub` 條件拒絕而直接失敗——這是
刻意的 fail-closed，不是 bug。

### 平時：Actions 頁按 Run workflow 即部署

1. GitHub repo → Actions 分頁 → 選 **Deploy Frontend** workflow。
2. 右上 **Run workflow**，`confirm` 選 `yes`（選 `no` 或不選會被
   workflow 內建的確認檢查擋下、直接 `exit 1` 中止，不會誤觸發部署）。
3. 因為 `deploy` job 掛了 `environment: production`，觸發後 job 會停在
   「Waiting for review」，需要前置設定裡指定的 reviewer 到該次 run 的頁面
   按 **Approve and deploy** 才會真的往下跑（觸發者跟核准者建議不同人）。
4. 同一時間只會有一個部署在跑（`concurrency: deploy-frontend`，新觸發的
   run 會排隊、不會取消正在跑的），跑完後 log 最後一行會印出這次部署的
   git short sha，供比對。

### 部署版本標記（curl 驗證線上 bundle 對應哪個 commit）

build 時 workflow 會把當次 checkout 的 git short sha 透過 `VITE_GIT_SHA`
環境變數注入 Vite build（`import.meta.env.VITE_GIT_SHA`），前端
`Header.tsx` 右上角版本徽章（`v0.6.5 · <sha>`，hover 有 tooltip）會顯示；
bundle 本身也會逐字含有這個 sha 字串，不用起瀏覽器也能直接驗證：

```bash
# 方式一：肉眼看網站右上角版本徽章
# 方式二：curl 拉下 bundle，grep 是否含有預期的 commit sha
curl -s https://trustforge.hurricanesoft.com.tw/ | grep -o 'assets/index-[^"]*\.js'
curl -s https://trustforge.hurricanesoft.com.tw/assets/index-<hash>.js | grep -o '<期望的 7 碼 sha>'
```

## 管理控制台部署銜接（admin/live token + cap env，管理控制台 PR-5）

### PR-B：runtime token 改由 app 自己在啟動期從 SSM 讀（#119 完全退場）

`deploy_ec2.sh` **不再接受、也不再傳遞任何 token 實際值**。token 值改成
**一次性**用 `deploy/put_runtime_tokens.sh` 寫入常駐 SSM SecureString 參數
（`/trustforge/runtime/{admin,live}-token`，預設前綴可用
`TRUSTFORGE_TOKEN_SSM_PREFIX` 覆寫），之後 app 行程啟動時自己去讀（見
`src/trustforge/ssm_params.py::get_runtime_token()`）。部署（`deploy_ec2.sh`）
只需要告訴 app「去哪個前綴讀」，而不是「讀什麼值」：

```bash
# 一次性（或輪替時）：老闆/CEO 手動執行，把 token 值寫進常駐 SSM 參數
# （本機 shell env 傳值，值永遠不進 repo/腳本本體，也不進 process list）
TRUSTFORGE_ADMIN_TOKEN=<老闆提供> TRUSTFORGE_LIVE_TOKEN=<老闆提供> \
  bash deploy/put_runtime_tokens.sh

# 平時部署：deploy_ec2.sh 只帶一個非機敏的 opt-in 旗標（純路徑字串，值本身
# 不是機密——真正機敏的 token 值已經在上一步進了 SSM，不再經過這支腳本）
TRUSTFORGE_TOKEN_SSM_PREFIX=/trustforge/runtime TRUSTFORGE_BEDROCK_DAILY_USD_CAP=1 \
  bash deploy/deploy_ec2.sh
```

`deploy_ec2.sh` 支援兩個**由部署當下 shell env 傳入**的變數（⛔ 憑證邊界
鐵則：腳本與 repo **不含任何 token 實際值**，`TRUSTFORGE_TOKEN_SSM_PREFIX`
本身只是路徑字串、不是機敏值）：

| env | 用途 | 未設時 |
|-----|------|--------|
| `TRUSTFORGE_TOKEN_SSM_PREFIX` | 告訴 app 去哪個 SSM 前綴讀 admin/live token（非機敏、opt-in） | 不寫該 Environment 行 → app 端 `get_runtime_token()` 直接回傳 `None`（零 boto3 匯入/零 AWS 呼叫），fallback 走既有的 env-based token（零設定不變式，向下相容） |
| `TRUSTFORGE_BEDROCK_DAILY_USD_CAP` | config store 未設 cap 時的 env fallback 層（`budget_guard.py` 三層順序：config → env → DEFAULT） | 不寫該行 → 吃 DEFAULT |

語意跟 `BEDROCK_MODEL_ID` 的 `${VAR-}` 慣例一致：「未設＝該行完全不寫進
`trustforge.service`」，**不是寫空值**。update-in-place（既有實例）路徑會
每次部署 reconcile 這兩行——有值＝取代/插入、未設＝**整行刪除**。

### 遷移：#119 時代殘留的 token 環境變數行會被自動清掉

舊版（#119 機制）曾經把 `TRUSTFORGE_ADMIN_TOKEN`/`TRUSTFORGE_LIVE_TOKEN`
**token 值本身**寫進 `trustforge.service` 的 `Environment=` 行。這是本 PR
的核心安全價值：**token 從此離開 unit 檔落點**。update-in-place 每次部署
都會無條件對這兩個 key 執行整行刪除（不管這次部署有沒有帶
`TRUSTFORGE_TOKEN_SSM_PREFIX`），把舊機制留下的殘留行清乾淨，不需要人工
介入或額外的一次性遷移腳本。

IAM：本 PR **刻意不動** IAM（風險收斂，留給 PR-C）。`trustforge-inline`
既有的 `ssm:GetParameter`（鎖 `parameter/trustforge/deploy/*`，#119 部署期
臨時參數用）語句維持不變，即使部署邏輯已經不再使用它；app 啟動期讀
`parameter/trustforge/runtime/*` 常駐參數所需的權限已在 PR-A 隨
`ssm_params.py` 一併補上（見 `deploy_ec2.sh` 內 `trustforge-inline` policy
的 PR-A 註解），跟這裡的部署期 IAM 各自獨立。

注意事項：

- `TRUSTFORGE_TOKEN_SSM_PREFIX` 僅接受 `[A-Za-z0-9._/~-]`（cap 僅接受十進
  位數字）——值最終仍會進遠端 sed 取代式與 systemd unit，含引號/分號等
  字元一律在本機 fail-fast 中止（注入防護）。
- token 值本身**不再經過 `deploy_ec2.sh`**：換發/撤銷 token 一律重跑
  `deploy/put_runtime_tokens.sh`（`Overwrite: true`，可重複執行），app 需要
  重啟才會讀到新值（`systemctl restart trustforge`；`put_runtime_tokens.sh`
  本身不負責重啟，見該腳本內「啟動期凍結提醒」）。
- **勿設**（do-not-set）`TRUSTFORGE_ADMIN_TOKEN`/`TRUSTFORGE_LIVE_TOKEN`
  作為 `deploy_ec2.sh` 的呼叫環境——這兩個變數在 PR-B 起對 `deploy_ec2.sh`
  完全無效（腳本已不讀取這兩個名字），值只應該出現在呼叫
  `put_runtime_tokens.sh` 的當下 shell env。

### ⛔ /admin 只准在 TLS 模式使用（harper CISO 條件 A）

admin token 走 `X-Admin-Token` header——**明碼 HTTP 下 token 會明文過線**。
規則：

- 管理面只准在 **TLS 模式**（`nginx.conf` react 版，或 SSR 回滾時的
  `nginx-legacy-tls.conf`）下使用。
- **`nginx-react-http.conf`（明碼）模式下管理面技術封鎖**（harper CISO
  M-3 = vp-eng 複審 M-1 修正）：該 conf 內含
  `location ^~ /api/admin/ { return 404; }`，`/api/admin/*` 一律在 nginx
  層直接 404、不會轉發給 python——⚠️ 早期版本誤以為「不寫該 location」
  就等於禁用，但省略 location 只會讓請求落入下面泛用的 `location /api/`
  照樣 proxy 給 python，若誤設了 token，管理面會在明碼 HTTP 上全開；現在
  是用 nginx 主動 404 技術性封死，不只是「刻意不寫」。該模式仍**勿設
  `TRUSTFORGE_ADMIN_TOKEN`**（web.py 未設 token = app 層也 fail-closed 全
  關，雙重防護，非單一防線）。

### nginx `/api/admin/` 硬化（`deploy/nginx.conf`，harper 條件 A + M1）

react TLS 版 conf 有專屬 `location /api/admin/`（最長前綴優先於 `/api/`）：

- `X-Real-IP`／`X-Forwarded-For` **無條件用 TCP 層 `$remote_addr` 覆寫**
  （非透傳）——admin per-IP lockout 完整性依賴此（harper M1；本項同時落實
  task #113 的 netops 項，#113 的 secops 告警項仍開放）。
- `proxy_no_cache 1; proxy_cache_bypass 1;` + `Cache-Control: no-store`：
  設定快照不被任何中介/瀏覽器快取。
- （選配）來源 IP allowlist 範本預設註解在該 location 內：取消
  `# allow <ADMIN_SOURCE_IP>;` / `# deny all;` 兩行註解、填入老闆固定出口
  IP、`nginx -t && systemctl reload nginx` 即生效——非名單來源直接 403，
  token 驗證都碰不到（縱深防禦，IP 浮動時再註解回來即可）。

`Cache-Control: no-store` 另在 `src/trustforge/web.py::_send()` 集中補一層
（harper L-2）：只要路徑落在 `/api/admin/` 下，這個唯一的回應出口一律加此
header，不管跑的是哪份 nginx conf（react TLS／react-http／legacy-tls）—— nginx
層的 `proxy_no_cache`/`no-store` 若有哪份 conf 漏配，app 層仍是最後一道
防線。

## #75 多實例 budget 預留（DynamoDB 表 + 最小權限 IAM + 可見降級）

`budget_guard` 在 `TRUSTFORGE_BUDGET_GUARD_BACKEND=dynamodb` 時，對所有實例共用
一張 `trustforge-budget-guard` 表做原子 conditional 預留（多實例部署才安全的
每日 `$N` 上限）。部署腳本 `deploy_ec2.sh` 現在會在 IAM/表 reconcile 階段自動：

1. 呼叫 `deploy/setup_budget_guard_dynamodb.sh` 建表（PK `source_id` String /
   SK `coin` String，PAY_PER_REQUEST + TTL on `ttl`）。
2. 掛**最小權限** IAM policy `trustforge-budget-guard` 到 `trustforge-ec2` 角色，
   只含對該表 ARN 的 `dynamodb:UpdateItem` / `dynamodb:GetItem`（**絕不**
   `dynamodb:*`）。可用 `TRUSTFORGE_BUDGET_COUNTER_TABLE` 覆寫表名。
3. 確認表存在，不存在就 fail-closed 中止部署（不讓「多實例保護靜默失效」被當
   成成功）。

### 殘餘風險（必讀）

若表不存在 / 實例角色未掛 `trustforge-budget-guard` IAM policy / DynamoDB 不可用，
`try_reserve` 會拋 `BudgetBackendError`，app **fallback 回 process-local 預留**
（單 process 內仍安全），但**多實例保護在這段期間暫時失效**——多 process/多機
部署時各 process 的 reserved 互不可見，每日 `$N` 硬上限可能被並行撐爆成 N 倍。

**這不是靜默降級**：app 每次偵測到後端失效，都會送一條 CloudWatch 指標
`BudgetGuardMultiInstanceProtectionDisabled`（不受 `TRUSTFORGE_CW_METRICS` opt-in
限制）並記 warning log，證明多實例保護已失效。維運應對該指標建告警、定期查
`/api/status`。後端恢復後，下一次 `try_reserve` 走 DynamoDB 路徑，原子收斂自動
恢復。

## #104 dedup fail-open 告警（部署清單強制項）

`deploy/put_dedup_alarm.sh` 會建立 #104 要求的兩層 CloudWatch 告警：

1. `DedupFailOpenRecentFailures`：監控 app 端送出的滑動視窗數值（見
`src/trustforge/cloudwatch_metrics.py`、`web.py::_record_dedup_prep_failure`），指標
超過門檻（預設 5）即觸發。
2. `DedupFailOpenAlertLogCount`：先在 CloudWatch Logs 建 metric filter，匹配固定前綴
`"ALERT: TrustForge dedup"`，再對該 log metric 建 alarm。這條作首次通知，
auto-resolve 仍以 `/api/status.dedup.degraded` 的即時狀態為準。

**demo 部署清單強制三件事，否則告警形同虛設：**

1. **`TRUSTFORGE_CW_METRICS=1` 必須開啟**（app 端 opt-in，否則不送指標、Alarm
   永遠收不到數據）。`deploy_ec2.sh` 對公開 demo 預設已開（=1），實例角色也補了
   `cloudwatch:PutMetricData`。
2. **確認 `TRUSTFORGE_DEDUP_LOG_GROUP`**。預設為
   `/aws/apprunner/trustforge/application`；若 demo 環境的 web log group 不同，部署前
   必須覆寫，否則 log-based alarm 收不到 ALERT 前綴。
3. **必設 `TRUSTFORGE_DEDUP_ALARM_SNS=<arn:aws:sns:...>`**。有 SNS 時 Alarm 觸發才
   真的發通知；未設則 Alarm 仍會建立（純狀態可視、可在 CloudWatch 控制台看到），
   但**不發任何通知**——腳本絕不會再把非法的 Logs ARN 塞進 `--alarm-actions`
   （那會讓 `set -e` 下的建表失敗、Alarm 整個建不出來，舊版 codex 打回的主因）。
   傳入非 `arn:aws:sns:*` 的值會直接 `exit 1` 要求修正。

```bash
# 建 Alarm（先設 SNS topic）
REGION=ap-southeast-2 TRUSTFORGE_CW_NAMESPACE=TrustForge \
  TRUSTFORGE_DEDUP_LOG_GROUP=/aws/apprunner/trustforge/application \
  TRUSTFORGE_DEDUP_ALARM_SNS=arn:aws:sns:ap-southeast-2:<ACCT>:trustforge-alerts \
  ./deploy/put_dedup_alarm.sh
```

## #121 runtime token SSM/KMS（sweep + KMS 收斂）

### runtime token 機制 = SSM Parameter Store 讀取（不含 systemd tmpfs 憑證層）

app 端 `src/trustforge/ssm_params.py::get_runtime_token` 於啟動期直接從 SSM
Parameter Store 讀取 SecureString 型態的 runtime token（admin-token /
live-token），啟動時經 SSM `get_parameter --with-decryption` 讀取，全程**不落
argv / env / 持久碟 / 日誌**，fail-closed（讀不到就回 None，由呼叫端 fallback）。

> ⚠️ 歷史：曾有 #121.7 的 systemd `LoadCredential` + tmpfs 憑證層（獨立 oneshot
> unit 在啟動前把 SSM token 寫進 tmpfs、app 經 `$CREDENTIALS_DIRECTORY` 讀取）。
> 經 codex-review 第三輪實測確認，該層在真實部署路徑（全新 EC2 user-data、
> update-in-place reconcile）完全失效，且有「假安全感 + 服務起不來」風險。**已
> 移除並回退到上述 SSM 路徑**（兩位審查員均認證 SSM 路徑安全）。sweep 與 KMS
> 收斂不受影響。

### 部署期參數 sweep（#121.6，現已接線）

`sweep_deploy_parameters`（清理 `/trustforge/deploy/*` 超時殘留參數）原本從未被
呼叫。現經 `scripts/sweep_deploy_parameters.sh`（unit 的 `ExecStartPre`，非致命）
接線執行；`describe_parameters` 已加 `NextToken` 分頁迴圈，多頁參數不會漏清。

### KMS EncryptionContext 收斂（#121.9）

`put_runtime_tokens.sh` 設定 `TRUSTFORGE_TOKEN_KMS_KEY_ID` 時，SSM 以該 CMK 加密
SecureString 並自動帶入 EncryptionContext `aws:ssm:parameter-arn`。CMK 的 key
policy 應收斂解密權限（見 `put_runtime_tokens.sh` 輸出的片段）：

```json
"Condition": { "StringEquals": {
  "kms:EncryptionContext:aws:ssm:parameter-arn":
  "arn:aws:ssm:<region>:<acct>:parameter/trustforge/runtime/*" } }
```

收斂後只有「經 SSM 且目標為該前綴參數」的解密才被放行，直接用 KMS API 解任意
東西都不符此 condition 而被拒。`put_runtime_tokens.sh` 與 `ssm_params.py` 的
region 預設一致（均 `ap-southeast-2`）。
