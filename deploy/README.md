# 部署 — AWS CLI + pre-push CD（Lambda + Function URL）

> 不走 App Runner 自動化。流程：`git push` → pre-push hook 跑測試 → 綠 → AWS CLI 部署到 Lambda。
> Lambda 在免費方案內可用、每月 100 萬請求免費；App Runner 不在免費內故不採用。

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
2. 若暫不上 DynamoDB，設定 `CACHE_BACKEND=json` 直接用本地 JSON 檔即可（單機
   demo/小流量夠用；多台排程機/多台產品機不共用磁碟時不適用，資料不會同步）。
   這種情況下 JSON 本身就是 primary backend，不是「fallback」，寫入失敗一樣
   會誠實回報失敗。

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

dev/CI 沒有真 AWS、想要「即使 DynamoDB 打不到，也有一個真正能用的本地快取」
時，才明確開啟這個 opt-in：

```bash
# 方式一：env（不用改 code）
TRUSTFORGE_CACHE_JSON_FALLBACK=1 python3 scripts/fetch_scheduler.py

# 方式二：直接用 JSON 當 primary backend（本來就不是 fallback，不受這個 opt-in 限制）
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
