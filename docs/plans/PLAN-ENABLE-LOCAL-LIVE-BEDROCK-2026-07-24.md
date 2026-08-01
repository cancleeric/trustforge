# 執行計劃：本機開啟 Live Bedrock 真分析（daily cap $10，僅 localhost）

- 狀態：**待 CEO 審查，尚未執行**（本文件不含任何 code 變更，未重啟任何 process）
- 撰寫：CPO（gray）
- Repo：`/Users/yinghaowang/HurricaneSoft/trustforge`，branch `develop`
- 老闆裁示：只在本機開 live Bedrock 真分析、**日上限 $10**、生產/線上一律不動

---

## ⚠️ 重大發現（審查前必看，直接影響驗收範圍）

在讀 code 過程中發現兩個**架構層級**的限制，會直接影響第 4 節「驗收條件」能否照原始描述達成，先摘要在最前面，供 CEO 決定範圍：

### 發現一：Hermes 排程引擎（daemon + 前端「送分析」）目前**硬編碼離線**，補 env 也不會變 live

`src/trustforge/analysis_flow.py` 的 5 階段管線（`STAGES`，見 analysis_flow.py:38：`source_ingestion / claim_extraction / trust_reasoning / evidence_assembly / report_delivery` —— 正好就是老闆講的「5 階段」）：

- `analysis_flow.py:745` `_stage_claim_extraction`：`client = BedrockClient(offline=True)` —— **寫死 `offline=True`**，不讀任何環境變數。
- `analysis_flow.py:748`：log 明寫 `"llm_active": False`。
- `analysis_flow.py:754-755` `_stage_trust_reasoning`：`build_stance_fn(stance_client=None, ...)` + `score(..., offline=True, ...)` —— stance 判斷同樣寫死離線。
- `analysis_flow.py:470` `create_snapshot()` 的 `collect(query, coin=coin, offline=False)` 只有**市場資料蒐集**是真連接器，LLM 部分（claim 抽取／trust reasoning／敘事）全部離線，無論 `BEDROCK_MODEL_ID`/`AWS_REGION`/live token 怎麼設都一樣。

這條管線同時被兩種呼叫者共用：
1. daemon 排程（`scripts/run_analysis_flow.py --daemon` → `flow.refresh_once()`）
2. 前端「送 BTC 分析」的 `registerAnalysisQuestion()`（見下一點）

**結論**：無論這次計劃怎麼補 env，daemon 排程分析與「已發佈的 Hermes snapshot」都不會變成真 Bedrock —— 這是 code 寫死的行為，不是設定問題，需要 CTO 另立工單修改 `analysis_flow.py`（超出本次 CPO 計劃範圍、也超出老闆這次「只補 env」的裁示）。

### 發現二：前端 HERMES 工作區（4174）目前**沒有任何管道**可以送出 live=1 請求

- `frontend/src/lib/endpoints.ts:49-56` `AnalyzeParams` 介面只有 `coin / type / q / coin2 / sample`，**沒有 `live`/`token` 欄位**。
- `frontend/src/pages/AnalyzePage.tsx:18-24` `paramsFromSearch()` 從網址列只解析 `coin/type/q/sample` 四個 query 參數，就算手動在瀏覽器網址列加 `&live=1&token=...` 也會被前端**直接忽略**、不會轉送給後端。
- `frontend/src/pages/AnalyzePage.tsx:134` 呼叫 `getAnalyze({coin,type,q,sample})` → 命中的是「真資料·$0」real-off 檔位（`data_mode=live, llm_mode=off`），不是真 Bedrock。
- `frontend/src/pages/AnalyzePage.tsx:179` 呼叫 `registerAnalysisQuestion()` → 進 `AnalysisFlow` 佇列 → 落到發現一的硬編碼離線路徑。

**結論**：老闆原始驗收條件「前端 4174 HERMES 工作區送 BTC 分析應出現真信任分數與 5 階段跑完」**目前程式碼做不到**——不是本次計劃的 env 設定能解決的。5 階段本身可以跑完沒錯，但那 5 階段從頭到尾都是離線 $0，不會有真信任分數（真 Bedrock 產出）。

### 建議（請 CEO 選一項）

- **選項 A（本次計劃採用，範圍不變）**：只開通 `/api/analyze?...&live=1&token=<TOKEN>` 這條**既有、本來就支援 live 的 on-demand 路徑**（`pipeline.run()`，見 `src/trustforge/pipeline.py`）。CEO 驗收改用 **curl 直打後端**確認真 Bedrock、真花費、真 cap 生效；HERMES 工作區前端維持現況（real-off $0，不受影響、不會誤花錢）。daemon 維持現況跑（本來就全離線，不受影響）。
- **選項 B（需另立工單，本次不做）**：若堅持要「前端點一下 HERMES 工作區就出現真 Bedrock 結果」，需要 CTO 改 `analysis_flow.py`（拔掉硬編碼 offline、改走 env 判斷 + `budget_guard` 護欄）+ 改前端（`AnalyzeParams`/`endpoints.ts`/`AnalyzePage.tsx` 加 live/token 欄位 + UI 開關）。這牽涉到把 $10/日 cap 保護機制擴展到 daemon 排程管線（目前 cap 保護只包在 `pipeline.run()` 裡，`analysis_flow.py` 完全不吃 `budget_guard`），改動面較大，建議另外派工。

以下第 1-5 節按老闆原始要求撰寫，**採選項 A 的範圍**（web.py on-demand live 路徑），第 3 節會另外標明 daemon 重啟後「不會」得到 live 分析結果的事實。

---

## 1. 完整 env 清單（web.py + daemon 共用）

### 1.1 live 閘怎麼被打開（file:line 證據）

| 判斷點 | 位置 | 邏輯 |
|---|---|---|
| Bedrock 是否可呼叫（總閘） | `src/trustforge/web.py:376-385` `_bedrock_allowed_resolved()` | `env BEDROCK_MODEL_ID` 未設 → 直接 `(False,"env")` 短路；設了才讀 `cfg.bedrock_enabled`。在 `TRUSTFORGE_DISABLE_ADMIN_CONFIG=1` 下 `cfg` 是空的 `AdminConfig()`（見下），`bedrock_enabled is None` → 回 `(True,"env")`。**即：本機只要設 `BEDROCK_MODEL_ID` 這個總閘就開了，不需要 DynamoDB。** |
| admin config 讀取短路 | `src/trustforge/web.py:216-221` `_admin_runtime_config()` | `TRUSTFORGE_DISABLE_ADMIN_CONFIG` 為 `1/true/yes` → 直接回 `admin_config.AdminConfig()`（全欄位 `None`），**零 DynamoDB 呼叫**。 |
| live token 三層優先序 | `src/trustforge/web.py:393-425` `_live_token_effective_layer()` | 1) config store `live_token_hash`（我們的空 cfg 下是 `None`）→ 2) SSM bootstrap `_LIVE_TOKEN_SSM_BOOTSTRAP` → 3) env `TRUSTFORGE_LIVE_TOKEN`。 |
| SSM 層確認不生效 | `src/trustforge/ssm_params.py:99` `get_runtime_token()` | 未設 `TRUSTFORGE_TOKEN_SSM_PREFIX`（本機沒設）→ 回 `None`，SSM 層不介入。 |
| → 結論 | | **`TRUSTFORGE_DISABLE_ADMIN_CONFIG=1` + 不設 `TRUSTFORGE_TOKEN_SSM_PREFIX` 時，live token 100% 由 env `TRUSTFORGE_LIVE_TOKEN` 供給，完全不需要 DynamoDB admin 表**——回答老闆問題 1 的第二部分：**是，可以純 env 供給**。 |
| 啟動期額外閘門（容易漏） | `src/trustforge/web.py:8443-8454` | 若偵測到 live token 已設定（`_LIVE_TOKEN_BOOTSTRAP_RESOLVED` 非空）且 `TRUSTFORGE_TRUST_PROXY` 未開（本機沒有 nginx TLS 反代，一定沒開）→ **`raise SystemExit`，process 直接拒絕啟動**，除非明確設 `TRUSTFORGE_ALLOW_INSECURE_LIVE_TOKEN=1`。**這個 env 是本機測試 live 模式的硬性必要條件，不設會直接開不起來。**（現有 daemon PID 37438 已經有設這個，web PID 30602 目前沒設 live token 所以沒觸發。） |

### 1.2 region / model 不相容問題（老闆已提示，此處補證據）

| 項目 | 位置 | 說明 |
|---|---|---|
| `AWS_REGION` 預設值 | `src/trustforge/bedrock.py:112` | `region: str = os.getenv("AWS_REGION", "ap-southeast-2")` —— 若不顯式設 `AWS_REGION=us-east-1`，會用舊預設 `ap-southeast-2`，打新帳號 `<ACCOUNT_ID>`（us-east-1）會失敗。 |
| narrative 主模型 | `src/trustforge/bedrock.py:115` | `model_id: str = os.getenv("BEDROCK_MODEL_ID", "")` —— 必須顯式設。 |
| stance 子模型預設值（地雷） | `src/trustforge/bedrock.py:120-122`、`src/trustforge/budget_guard.py:45` | `stance_model_id` 預設是 `au.anthropic.claude-haiku-4-5-20251001-v1:0`（`au.` 是 region-prefix，只能在 `ap-southeast-2/4/6` 呼叫）。**只設 `AWS_REGION=us-east-1` 而不覆寫這個預設，stance 分類會每次真呼叫失敗**（`classify_stance` 失敗會靜默 fallback `neutral`，見 bedrock.py:258-266 docstring，不會報錯但會悄悄降級品質）。**必須額外設 `BEDROCK_HAIKU_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0`** 覆寫成跟 narrative 同一個、已實測可用的 `us.` prefix 模型。 |
| 計價表確認 | `src/trustforge/ledger.py:51` | `"us.anthropic.claude-haiku-4-5-20251001-v1:0": (1.0, 5.0)` —— 已在 `PRICING` 登記（$1/$5 每百萬 token），narrative 與 stance 都用同一顆已計價模型，不會被 `budget_guard.narrative_model_priced()`/`stance_model_priced()`（`budget_guard.py:701-713`）fail-closed 擋掉。 |

### 1.3 $10 cap 怎麼設、怎麼被本機吃到

| 位置 | 說明 |
|---|---|
| `src/trustforge/budget_guard.py:52` | `DEFAULT_BEDROCK_DAILY_USD_CAP = 3.0`（預設 $3，需覆寫） |
| `src/trustforge/budget_guard.py:66-75, 119-131` | `daily_cap_usd_resolved()`：`TRUSTFORGE_DISABLE_ADMIN_CONFIG=1` 時**直接**用 `_env_cap()`（env `TRUSTFORGE_BEDROCK_DAILY_USD_CAP`）或 DEFAULT，**完全不讀 config store**，零 DynamoDB 探測。→ 設 `TRUSTFORGE_BEDROCK_DAILY_USD_CAP=10` 即生效，不需要動任何管理面設定。 |

### 1.4 完整 env 清單（web.py + daemon 一致採用）

```bash
# --- Bedrock 連線 ---
AWS_REGION=us-east-1                                        # 覆寫 bedrock.py:112 的 ap-southeast-2 預設
AWS_DEFAULT_REGION=us-east-1                                # boto3 部分路徑吃這個而非 AWS_REGION，保險一起設
BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0        # narrative 主模型，CTO 已實測 converse 可用
BEDROCK_HAIKU_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0  # 覆寫 stance 預設 au. prefix（bedrock.py:120, budget_guard.py:45）

# --- live 閘 + 成本護欄 ---
TRUSTFORGE_LIVE_TOKEN=<執行時用 `openssl rand -hex 24` 現場產生，記在安全處，不寫入本文件/git>
TRUSTFORGE_BEDROCK_DAILY_USD_CAP=10
TRUSTFORGE_DISABLE_ADMIN_CONFIG=1                            # trustforge_control.sh 預設已帶，顯式寫出避免漏
TRUSTFORGE_ALLOW_INSECURE_LIVE_TOKEN=1                       # 本機無 nginx TLS 反代，web.py:8443 啟動閘的必要 opt-out

# --- 安全收斂（本次新增建議，降低本機暴露面）---
TRUSTFORGE_BIND_HOST=127.0.0.1                                # 預設 0.0.0.0（web.py:8428）會聽所有網卡；本次是會真花錢的功能，收斂成只聽 loopback

# --- 沿用既有（勿變動）---
TRUSTFORGE_ENV=local
PORT=8799
MODELHUB_API_KEY=<沿用 PID 30602 現有值，不在本文件明寫，執行時從既有 process/密鑰管理處取得>
MODELHUB_BASE_URL=http://localhost:8950
```

**⛔ 執行前必做（憑證衛生）**：目前 daemon（PID 37438）process 環境裡已經帶有一組 `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN`（STS 臨時憑證），從我診斷時解出的帳號片段判斷，**疑似是舊/其他帳號的殘留憑證，不是新的 `<ACCOUNT_ID>`**，且 STS session token 通常數小時到最長 36 小時就過期（daemon 已跑 3 天，這組憑證幾乎必然已失效）。boto3 的憑證優先序是「顯式 env 變數 > `~/.aws/config`」，**若重啟時繼承了同一個 shell 殘留的這三個 env 變數，会蓋掉 `~/.aws/config` 的新 default profile**，導致連線失敗或（更糟）誤打舊帳號。

→ 重啟前務必先：
```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
```
讓 boto3 走 `~/.aws/config`（已確認：`[default]` → `arn:aws:iam::<ACCOUNT_ID>:root`、`region=us-east-1`，CTO 已用這個 profile 實測 `us.anthropic.claude-haiku-4-5-20251001-v1:0` converse 成功）。

（附註：這組憑證因為 `analysis_flow.py:745/755` 硬編碼離線，daemon 實際上從未真的用它打過 Bedrock——不構成本輪資安風險，只是顯示先前那次嘗試的 env 設定本身就是矛盾/不完整的，值得記錄。）

---

## 2. web.py 重啟步驟

沿用 `scripts/trustforge_control.sh`，`PORT=8799`：

```bash
cd /Users/yinghaowang/HurricaneSoft/trustforge

# 1) 停舊 process（PID 30602，離線狀態）
scripts/trustforge_control.sh stop

# 2) 憑證衛生（見第 1.4 節說明）
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN

# 3) 設定完整 env（見第 1.4 節清單；TRUSTFORGE_LIVE_TOKEN 現場產生並記下）
export PORT=8799
export TRUSTFORGE_ENV=local
export TRUSTFORGE_DISABLE_ADMIN_CONFIG=1
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
export BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
export BEDROCK_HAIKU_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
export TRUSTFORGE_LIVE_TOKEN="$(openssl rand -hex 24)"
echo "LIVE TOKEN（記下，不要外流）：$TRUSTFORGE_LIVE_TOKEN"
export TRUSTFORGE_BEDROCK_DAILY_USD_CAP=10
export TRUSTFORGE_ALLOW_INSECURE_LIVE_TOKEN=1
export TRUSTFORGE_BIND_HOST=127.0.0.1
export MODELHUB_API_KEY=<沿用既有值>
export MODELHUB_BASE_URL=http://localhost:8950

# 4) 啟動
scripts/trustforge_control.sh start

# 5) 立即確認
scripts/trustforge_control.sh status
curl -s http://localhost:8799/api/status | jq '.data.bedrock_capable, .data.live_token_set'
```

log 位置：`out/trustforge-web.log`（沿用現有，`scripts/trustforge_control.sh` 用 `>>` 附加，不會截斷舊 log）。

---

## 3. daemon 的安全重啟步驟

**先講結論**：依第 0 節「重大發現一」，**重啟 daemon 不會讓它產生真 Bedrock 結果**——`analysis_flow.py:745/755` 的硬編碼 `offline=True` 跟環境變數無關。以下步驟純粹是「讓 daemon 的 env 跟 web.py 一致、清掉疑似舊帳號的殘留憑證」的環境衛生動作，**功能上排程分析輸出不會改變**（依然是離線 $0）。是否值得為此重啟，請 CEO 一併決定；若決定「這輪先不重啟 daemon」也完全合理（反正它不會變 live）。

若仍要重啟（環境衛生 / 為將來選項 B 預作準備）：

```bash
# 1) 狀態確認（不動）
ps -p 37438
lsof -p 37438 | grep LISTEN   # 預期無 —— daemon 不開 port

# 2) 優雅停止（SIGTERM，讓 in-flight job 走完 graceful shutdown；
#    見 scripts/run_analysis_flow.py:50-52 有註冊 SIGTERM handler，
#    daemon loop 收到後 `flow.join(); flow.stop()` 才真的結束，
#    不是硬殺，會等佇列 worker 收尾）
kill -TERM 37438
# 等待 process 真的結束再繼續（避免同 SQLite 檔案雙開）
while kill -0 37438 2>/dev/null; do sleep 1; done

# 3) 狀態是否保留：AnalysisFlow 用 SQLite 持久化在
#    out/trustforge.sqlite3（src/trustforge/analysis_flow.py:107-108
#    _db_path() 預設值），啟動時 start() 會呼叫 recover()（analysis_flow.py:525-530）
#    重新 adopt 佇列裡 queued/running/failed 的 job，**不會遺失**。
#    schedule 節奏（next_scheduled_refresh）是 in-memory 變數，重啟後
#    重新從 0 開始計，等同「立刻可以再排一輪」，不是問題。

# 4) 用同組 env 重啟（同第 2 節，daemon 不需要 TRUSTFORGE_BIND_HOST/live token，
#    但為了跟 web.py 一致環境仍建議一起帶）
cd /Users/yinghaowang/HurricaneSoft/trustforge
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
export TRUSTFORGE_ENV=local
export TRUSTFORGE_DISABLE_ADMIN_CONFIG=1
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
export BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
export BEDROCK_HAIKU_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
export PYTHONPATH=/Users/yinghaowang/HurricaneSoft/trustforge/src
nohup .venv/bin/python scripts/run_analysis_flow.py --daemon --poll-seconds 15 --schedule-seconds 300 \
  >> out/hermes-daemon.log 2>&1 &
echo "daemon pid: $!"

# 5) 確認方式
tail -f out/hermes-daemon.log
# 但如上所述，即使跑起來，log 裡 bedrock.complete 事件永遠是 "llm_active": false
# （analysis_flow.py:748），不會有真結果——這是預期行為，不是 bug。
```

log 位置：`out/hermes-daemon.log`（沿用現有）。

---

## 4. 驗收條件（CEO 親測）

### 4.1 web.py on-demand live 路徑（本計劃唯一能保證的 live 驗證管道）

```bash
# coin 白名單見 src/trustforge/schema.py:15 COIN_POOL = (BTC,ETH,SOL,BNB,XRP)
curl -s "http://localhost:8799/api/analyze?type=multi_source&coin=BTC&q=分析BTC近期市場狀況&live=1&token=$TRUSTFORGE_LIVE_TOKEN" | jq '.'
```
- 預期：`ok:true`，`data` 內含真敘事文字（非離線罐頭句），花費非 $0。
- query 參數依據：`src/trustforge/web.py:312-315` `_compute_live_from_cfg()` —— `live=1` + `token=<TRUSTFORGE_LIVE_TOKEN 的值>`。

```bash
curl -s http://localhost:8799/api/status | jq '{bedrock_capable: .data.bedrock_capable, live_token_set: .data.live_token_set}'
```
- 預期：兩者皆 `true`。依據：`src/trustforge/web.py:6874-6875`。

```bash
curl -s http://localhost:8799/api/budget-governance | jq '.'
```
- 預期：`daily_cap_usd:10`、`daily_cap_source:"env"`、`spent_today_usd` 在跑過 4.1 的 `/api/analyze` 之後 > 0 且遞增、`kill_switch_active:false`。依據：`src/trustforge/web.py:5648-5687`；花費寫入見 `src/trustforge/agent/orchestrator.py:1633` `append_run()`（`pipeline.run()` 內部呼叫）。

### 4.2 前端 4174 HERMES 工作區（範圍調整，見開頭「重大發現」）

- **不會**出現真信任分數／真 Bedrock 產出的 5 階段結果——目前程式碼架構下做不到（見發現一、二）。
- 可驗證的是：既有「真資料·$0」流程正常（`sample` 未帶時走 real-off，`data_mode=live,llm_mode=off`），不受本次改動影響、不會意外燒錢。
- 若 CEO 堅持要前端驗收，需先核准第 0 節「選項 B」另立工單，不在本計劃範圍。

---

## 5. 成本 / 安全護欄

### 5.1 $10 cap 如何強制

| 機制 | 位置 | 說明 |
|---|---|---|
| 每日全域上限判定 | `src/trustforge/budget_guard.py:263-290` `daily_cap_exceeded()` | `cap<=0` 視為全關；讀 ledger 累計今日花費 `>= cap` 即觸發；**讀取失敗也 fail-closed**（保守視為已達上限，強制離線，見 279-289） |
| 並行 race 防護（TOCTOU） | `src/trustforge/budget_guard.py:293-341`（`BudgetReservation`/`try_reserve_request_budget`） | 每個 `/api/analyze` 呼叫前先做 process-local 原子預留（估計上界 ≥ `DEFAULT_REQUEST_MAX_USD=$0.05`，實際用 `estimate_request_max_cost_usd()` 精算），避免多個並行請求同時看到「今日還沒花」而一起衝過 cap |
| unpriced model 保護 | `src/trustforge/budget_guard.py:701-713` | narrative/stance 各自的 model 若不在 `ledger.PRICING` 計價表，一律 fail-closed 降離線（我們選的 `us.anthropic.claude-haiku-4-5-20251001-v1:0` **已登記**，`ledger.py:51`，不會被擋） |
| 多實例保護 | `src/trustforge/budget_guard.py:521-535` | `TRUSTFORGE_BUDGET_GUARD_BACKEND` 預設 `"local"`（未設）→ **process-local**，零 DynamoDB 呼叫；本計劃刻意不設這個 env，維持純本機、零額外 AWS 依賴 |
| per-IP 限流（live 專用） | `src/trustforge/web.py:546-552` | 每 IP 每 60 秒最多 5 次 live 請求，額外防洪水誤觸 |

### 5.2 確認不會誤觸生產

- 本計劃**只涉及 localhost:8799**（`TRUSTFORGE_BIND_HOST=127.0.0.1` 進一步收斂只聽 loopback，不對外/不對 LAN）。
- 未動任何 Cloud Run 服務、未動任何 GCP/AWS 生產 env、未動任何 GCP Secret Manager / AWS Secrets Manager 內容。
- 未寫入任何 config store（`TRUSTFORGE_DISABLE_ADMIN_CONFIG=1` 全程零 DynamoDB 讀寫，見 1.1/1.3）。
- 未改動除本計劃檔以外的任何檔案；未重啟任何 process（本文件僅為計劃，待 CEO 核准後才執行）。
- daemon 重啟（若執行）僅影響本機 SQLite 佇列狀態，不觸碰任何雲端資源。

---

## 待 CEO 決定事項

1. **是否接受第 0 節的範圍調整**（選項 A：只驗收 curl live 路徑；HERMES 前端工作區與 daemon 排程本輪維持離線不變）？
2. 是否要為「選項 B」（讓前端/daemon 也能真 Bedrock）另立 CTO 工單？
3. daemon 是否值得為環境衛生重啟（功能上無影響，純粹清掉疑似舊帳號殘留憑證）？
