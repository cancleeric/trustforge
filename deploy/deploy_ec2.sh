#!/usr/bin/env bash
# TrustForge → EC2 公開部署（純 AWS CLI，冪等）。
# 給「真正跑在 AWS、有公開網址、不靠筆電」的 Live Demo，並完成 EC2 領 $20 credit。
# 最小權限：instance role 有 bedrock:InvokeModel + S3 讀(自家 bucket) + SSM +
# DynamoDB（鎖 trustforge-connector-cache / trustforge-cost-ledger 兩表 ARN）。
# 無 SSH key pair（走 SSM Session Manager）。
# ⚠️ 假設單人循序部署；不支援並行部署（TOCTOU：兩個行程同時偵測到「無既有
# 實例」再各自 run-instances 的競態，本腳本未加跨程序鎖）。緩解措施：
# run-instances 帶 --client-token 防同一次呼叫的 SDK 重試建重複，並在建立
# 後複查一次數量，多筆只印警告、交由人工判斷清理，不做自動刪除。
set -euo pipefail
cd "$(dirname "$0")/.."

REGION="${REGION:-ap-southeast-2}"
NAME=trustforge
# ⚠️ credit-safety fail-safe：公開 EC2 預設「離線」(空 model id → 不呼叫 Bedrock、不燒 credit)。
# 用 `${VAR-}`（非 `:-`）：只有「未設」才空。此腳本目前僅供**離線公開部署**。
# 註：真正開受控 live 需同時設 BEDROCK_MODEL_ID + 讓 app 拿得到 TRUSTFORGE_LIVE_TOKEN
# （PR-B 起本腳本不再傳遞 token 值，見下方 TRUSTFORGE_TOKEN_SSM_PREFIX 說明）。
MODEL="${BEDROCK_MODEL_ID-}"
# 管理控制台 PR-5（部署銜接）：日花費 cap + token SSM 前綴旗標的 env 傳遞層。
# ⛔ 憑證邊界鐵則：本腳本**不含任何 token 實際值**——PR-B 起 admin/live
# token 值不再由 deploy 搬運，改由 app（web.py）啟動期自行從 SSM Parameter
# Store 讀取。deploy 只傳一個非機敏的 opt-in 旗標 TRUSTFORGE_TOKEN_SSM_PREFIX
# （SSM 參數名前綴，純路徑字串、非機敏），告訴 app 去 prefix 底下取
# admin-token / live-token。未設（`${VAR-}` 空）＝該 Environment 行完全
# 不寫進 systemd unit（不是寫空值）→ app 端看不到前綴 → 落回 env fallback
# （TRUSTFORGE_ADMIN_TOKEN/TRUSTFORGE_LIVE_TOKEN，向後相容舊部署）。
#   - TRUSTFORGE_TOKEN_SSM_PREFIX：token SSM 路徑前綴 opt-in（未設＝app 走
#     env fallback；app 啟動期以此前綴 get-parameter 取 token 值，見
#     src/trustforge/ssm_params.py）
#   - TRUSTFORGE_BEDROCK_DAILY_USD_CAP：config store 未設時的 env fallback
#     層（見 src/trustforge/budget_guard.py 三層 cap 順序），部署可帶如 1
TOKEN_SSM_PREFIX="${TRUSTFORGE_TOKEN_SSM_PREFIX-}"
DAILY_CAP="${TRUSTFORGE_BEDROCK_DAILY_USD_CAP-}"
# #75：多實例 budget 預留後端。demo（公開 EC2）預設開啟 dynamodb（多實例部署
# 才安全的原子計數）；若表/IAM/DynamoDB 不可用，app 會 fallback 回 process-local
# 並送 CloudWatch 指標 BudgetGuardMultiInstanceProtectionDisabled 標記降級（不
# 靜默）。純本地離線開發可設 TRUSTFORGE_BUDGET_GUARD_BACKEND=local 關掉。
BUDGET_BACKEND="${TRUSTFORGE_BUDGET_GUARD_BACKEND:-dynamodb}"
# #104.5：demo 強制開啟 CloudWatch 指標上報（dedup fail-open 告警 + #75 後端
# 失效指標都靠它）。未設視同 1（開）。若不想送指標設 TRUSTFORGE_CW_METRICS=0。
CW_METRICS="${TRUSTFORGE_CW_METRICS:-1}"
COUNTER_TABLE="${TRUSTFORGE_BUDGET_COUNTER_TABLE:-trustforge-budget-guard}"
# H-19: web instances must share this lease table.  It prevents duplicate
# analysis/LLM work when the load balancer sends matching requests to different
# workers.  Provisioning remains in the privileged bootstrap path below.
LEASE_BACKEND="${TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND:-dynamodb}"
LEASE_TABLE="${TRUSTFORGE_LEASE_TABLE:-trustforge-analyze-leases}"
# 注入防護（值會被嵌進 SSM commands JSON 與遠端 root shell 的 sed 取代式）：
# prefix 限 SSM 參數名字元集（含 `/` 路徑分隔），cap 限十進位數字——含引號/
# 反斜線/管線/空白等一律在本機就 fail-fast 中止，杜絕「值本身」對遠端腳本
# 做 shell/JSON 注入。
# harper CISO L-1：原本用 `printf '%s' "$_val" | grep -Eq '^...$'` 有換行
# 繞過——grep 逐行比對，`-q` 只要「任一行」整行符合字元集就回傳成功，值若
# 含換行（例如 `good\n;rm -rf /`），第一行 `good` 單獨看合法，grep -q 就會
# 誤判整個值合法，換行後夾帶的惡意內容完全沒被擋到。改用 bash `[[ =~ ]]`：
# 這裡的 `^`/`$` 錨定的是整個字串（沒有 grep 那種逐行模式），字元集本身又
# 不含換行字元，只要值裡有任何一個換行，`+` 就無法從頭到尾連續匹配，整個
# 比對必然失敗——沒有「切成多行、挑一行過關」這條路。
if [ -n "$TOKEN_SSM_PREFIX" ] && ! [[ "$TOKEN_SSM_PREFIX" =~ ^[A-Za-z0-9._/~-]+$ ]]; then
  echo "[ec2] ❌ TRUSTFORGE_TOKEN_SSM_PREFIX 含不允許字元（僅接受 [A-Za-z0-9._/~-]，避免注入遠端 systemd/sed/JSON），中止" >&2
  exit 1
fi
if [ -n "$DAILY_CAP" ] && ! [[ "$DAILY_CAP" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
  echo "[ec2] ❌ TRUSTFORGE_BEDROCK_DAILY_USD_CAP 必須是十進位數字（如 1 或 0.5），中止" >&2
  exit 1
fi

# 首次建置 user-data 的 systemd Environment 追加行：有值才產生該行（行尾含
# 換行，heredoc 內用 `${EXTRA_UNIT_ENV}ExecStart=...` 緊貼嵌入、未設不留
# 空行也不留空值行——fail-closed 的「不寫該行」語意）。cap 與 token SSM 前綴
# 均為非機敏值，可直接寫入 user-data；admin/live token 值不再經 deploy 傳遞
# （PR-B 起改由 app 啟動期自行從 SSM 讀取，見上）。
EXTRA_UNIT_ENV=""
[ -n "$DAILY_CAP" ] && EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=${DAILY_CAP}
"
[ -n "$TOKEN_SSM_PREFIX" ] && EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_TOKEN_SSM_PREFIX=${TOKEN_SSM_PREFIX}
"
# #75 / #104.5：demo 預設開啟的多實例預留後端 + CloudWatch 指標上報（值已帶
# 預設，故無論呼叫端有無設都寫入該行；非機敏值，可直接寫入 user-data / unit）。
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_BUDGET_GUARD_BACKEND=${BUDGET_BACKEND}
"
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_BUDGET_COUNTER_TABLE=${COUNTER_TABLE}
"
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_CW_METRICS=${CW_METRICS}
"
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND=${LEASE_BACKEND}
"
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_LEASE_TABLE=${LEASE_TABLE}
"

# update-in-place 用：對單一 Environment 行產生「ensure（有值：sed 取代／
# 沒有該行就插到 PYTHONPATH 後）或 remove（未設：整行刪除，fail-closed）」
# 的 SSM commands JSON 片段（含前導逗號），比照既有 CACHE_BACKEND 等四個
# env 的 grep→sed 慣例，冪等（重跑不重複插入）。
ssm_env_cmd() {
  local key="$1" val="$2"
  if [ -n "$val" ]; then
    printf ',"if grep -q \\"^Environment=%s=\\" /etc/systemd/system/trustforge.service; then sed -i \\"s|^Environment=%s=.*|Environment=%s=%s|\\" /etc/systemd/system/trustforge.service; else sed -i \\"/^Environment=PYTHONPATH=/a Environment=%s=%s\\" /etc/systemd/system/trustforge.service; fi' "$key" "$key" "$key" "$val" "$key" "$val"
  else
    printf ',"sed -i \\"/^Environment=%s=/d\\" /etc/systemd/system/trustforge.service' "$key"
  fi
  printf '"'
}

# update-in-place SSM commands：runtime token PR-B 遷移邏輯 + cap/prefix
# ensure/delete。遷移：無論本次有無帶旗標，先無條件刪除既有實例 unit 檔裡
# #119 時代殘留的 TRUSTFORGE_ADMIN_TOKEN / TRUSTFORGE_LIVE_TOKEN 整行——這是
# 本次 PR 的安全價值所在（token 離開 unit 檔的落地點，改由 app 啟動期自行從
# SSM 讀取）。這兩段刪除透過呼叫 ssm_env_cmd 傳空字串觸發 else 分支，輸出格式
# 與既有 delete 慣例完全一致（同一套 JSON commands 陣列拼接）。接著 cap 與
# token SSM 前綴走標準 ensure/delete（有值 sed 取代／插入，未設整行刪除）。
UNIT_ENV_RECONCILE_CMDS="$(ssm_env_cmd TRUSTFORGE_ADMIN_TOKEN "")$(ssm_env_cmd TRUSTFORGE_LIVE_TOKEN "")$(ssm_env_cmd TRUSTFORGE_BEDROCK_DAILY_USD_CAP "$DAILY_CAP")$(ssm_env_cmd TRUSTFORGE_TOKEN_SSM_PREFIX "$TOKEN_SSM_PREFIX")$(ssm_env_cmd TRUSTFORGE_BUDGET_GUARD_BACKEND "$BUDGET_BACKEND")$(ssm_env_cmd TRUSTFORGE_BUDGET_COUNTER_TABLE "$COUNTER_TABLE")$(ssm_env_cmd TRUSTFORGE_CW_METRICS "$CW_METRICS")$(ssm_env_cmd TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND "$LEASE_BACKEND")$(ssm_env_cmd TRUSTFORGE_LEASE_TABLE "$LEASE_TABLE")"

# 先查一次既有實例，才能判斷「這次到底會不會走首次建置」——這是全腳本第一個
# aws 呼叫（在此之前只做過本機 regex 驗證，零 aws 呼叫），純唯讀
# （describe-instances），跟下面第 3) 節「既有實例？」分流判斷共用同一份
# 結果（不重複查兩次）。EIP 已附著在 tag Name=trustforge-demo 的既有實例上；
# 只要不 terminate 該實例，EIP 就會一直留在上面（stop/start 也保留附著，不用
# 重綁）→ 查到就重用，不建新的。「查詢失敗（憑證/throttle/網路）」與「真的
# 無既有實例」必須分開處理：查詢失敗一律中止，絕不能落到下面的建置流程，
# 否則會建出重複實例（defeat 防 sprawl）。注意：本腳本自己建議「停實例省
# credit」，停止狀態很常見，所以要查「所有非 terminated」的相符實例（不能只
# 查 running，否則 stopped 的 production 會被誤判成無實例 → 建重複 + 拋棄
# EIP）。
if ! MATCHES=$(aws ec2 describe-instances --region "$REGION" \
  --filters Name=tag:Name,Values=trustforge-demo \
    Name=instance-state-name,Values=pending,running,shutting-down,stopping,stopped \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text); then
  echo "[ec2] ❌ 查詢既有實例失敗（describe-instances 非零結束），中止避免建重複實例" >&2
  exit 1
fi
MATCH_COUNT=$(printf '%s\n' "$MATCHES" | grep -c . || true)

ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="trustforge-deploy-${ACCT}"
ROLE=trustforge-ec2
SG=trustforge-ec2-sg
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.micro}"
# Bootstrap provisions IAM and DynamoDB and is deliberately reserved for a
# privileged operator. Release CD deploys only into the already-provisioned
# production account through its narrow OIDC role.
BOOTSTRAP="${TRUSTFORGE_BOOTSTRAP:-1}"

echo "[ec2] 帳號 $ACCT / 區域 $REGION / 模型 ${MODEL:-<離線,無BEDROCK_MODEL_ID,不燒credit>}"

# 排程 fetcher 同步驗證：部署後立刻真的跑一次 fetch-scheduler.service（經
# SSM），用 exit code 判斷成功與否——不能只 enable timer 就當部署成功，否則
# 像「IAM 角色少了 DynamoDB 權限」這種錯，會一路 exit1 到有人手動查 journal
# 才發現，deploy 腳本卻回報成功。首次建置 user-data 可能還在跑，所以先等
# unit 檔出現（逾時 200s）才 daemon-reload + systemctl start（Type=oneshot
# 會同步等它跑完）；update-in-place 呼叫這函式時 unit 檔已經寫好，那段等待
# 迴圈第一輪就會 break，等同無感。首次建置與 update-in-place 兩條路徑都會
# 呼叫本函式（分別在各自流程最後）。
#
# follow-up（真部署發現）：今日真部署時 scheduler 其實已成功寫 37 筆進
# DynamoDB（DynamoDB R/W 完全正常），但因 reddit 在 EC2 共享 IP 上必定 429
# （來源端限流，跟本次部署的基建/程式碼健康完全無關），一般全源排程
# `systemctl start fetch-scheduler.service` 因此 exit 非零，若把它當部署
# gate 就會每次真部署都 false-fail，即使產品完全正常上線。修法：部署
# pass/fail gate 只認「真基建健康檢查」——`--probe`（不依賴任何外部來源，
# 純測 DynamoDB R/W/IAM）+ 下面 update-in-place 路徑既有的 web healthz
# 迴圈；一般全源排程改成 **best-effort seed**（跑、記 log，來源端短暫失敗
# 如 reddit 429 不讓部署失敗），只有 `--probe` 失敗（真正的 DynamoDB/IAM/
# 程式問題）才讓部署 exit 1。
# v0.5.11/v0.5.12 誤報部署失敗的根因：`aws ssm wait command-executed` 內建
# 固定約 100s 逾時（delay 5s * maxAttempts 20），逾時本身視為 waiter 失敗
# （非零結束），但呼叫端用 `2>/dev/null || true` 吞掉這個錯誤後，緊接著只
# 查一次當下的 Status——若遠端指令還沒跑完（仍是 InProgress/Pending），
# 就會把「還在跑」誤判成「失敗」。verify_fetch_scheduler 的遠端指令包含
# 「等 unit 檔出現（up to 200s）+ 一般排程 + --probe」三段，很容易讓整體
# 耗時超過 100s；手動重跑同一個 --probe 卻都是 Success，因為手動重跑時
# 有足夠時間讓它跑到終態。
# 修法：自己輪詢 get-command-invocation 到終態（Success/Failed/Cancelled/
# TimedOut）才判定，並給比 100s 更寬裕、貼合各指令實際可能耗時的自訂
# timeout，逾時仍非終態才真的當失敗（並印當下狀態方便診斷）。
# 再 follow-up（架構級最終解法，見 verify_fetch_scheduler 內部註解）：上面
# 「等 unit 檔 + 一般排程 + --probe 三段塞進同一支 SSM command」的做法後來
# 發現一般排程本身的網路耗時無界（無法用任何固定 timeout 安全涵蓋），已
# 改成兩支完全獨立的 SSM command：一般排程 seed 是 fire-and-forget 不
# gate；`--probe`（有界，只測 DynamoDB R/W）是唯一被輪詢、唯一決定部署
# 成敗的 command。
# 再再 follow-up：光拆出 probe 還不夠，`--probe` 內部若沿用
# `get_cache_backend()`/`get_ledger()` 建構的 DynamoDB client，其 boto3/
# botocore 預設 timeout/重試降級時可達數分鐘，「有界」不成立；已在
# `scripts/fetch_scheduler.py::run_probe()` 改用帶明確短 timeout 的
# `_probe_cache_backend()`/`_probe_ledger_backend()`，poll deadline 依此
# bounded worst-case 推導（見 verify_fetch_scheduler 內部註解）。
# 用法：poll_ssm_terminal_status <command-id> <instance-id> [max_wait_secs] [interval_secs]
# 輸出（stdout）：終態字串；若逾時仍非終態，輸出當下抓到的狀態（可能是空
# 字串）並以非零結束，呼叫端據此判定失敗。
poll_ssm_terminal_status() {
  local cmdid="$1" iid="$2" max_wait="${3:-180}" interval="${4:-5}"
  local waited=0 status
  while :; do
    status=$(aws ssm get-command-invocation --region "$REGION" \
      --command-id "$cmdid" --instance-id "$iid" --query Status --output text 2>/dev/null || echo "")
    case "$status" in
      Success|Failed|Cancelled|TimedOut)
        echo "$status"
        return 0
        ;;
    esac
    if [ "$waited" -ge "$max_wait" ]; then
      echo "${status:-Unknown}"
      return 1
    fi
    sleep "$interval"
    waited=$((waited + interval))
  done
}

verify_fetch_scheduler() {
  local iid="$1"
  # codex HIGH（架構修正，v0.5.11/v0.5.12 誤報部署失敗的最終解法）：早前
  # 兩輪都是在同一支 SSM command 裡先跑「一般全源排程 seed」再跑
  # `--probe`，然後想辦法把 poll timeout 設得比 seed 的最壞耗時更大——
  # 但 seed 對每個真連接器的 HTTP 呼叫延遲本質上是**無界**的（上游慢、
  # 429 backoff、CoinGecko host 節流…），17 個逐幣來源 * 5 幣即使每個
  # connector 只有 5s timeout，序列跑理論上限就有 ~425s，還沒算 CoinGecko
  # 節流器插入的等待——不管 poll timeout 設 180s/300s/更大都可能不夠，
  # 一直在追一個追不完的數字。
  # 正解：把「有界的 probe」跟「無界的 seed」拆成兩個獨立 SSM command。
  # deploy gate 只送出、只 poll、只依賴 probe 這個 command 的結果；seed
  # 是完全獨立的 fire-and-forget SSM command，送出後不等待、不 poll、不
  # 影響部署成敗判定，失敗與否只留在該 instance 的 systemd journal 供事後
  # 人工查（`journalctl -u fetch-scheduler`／`aws ssm get-command-invocation`）。

  # ---- 1) best-effort 全源排程 seed：fire-and-forget，完全不 gate -------
  # 讓 cache 儘早有資料可用，但這裡故意不等待、不檢查結果——seed 的無界
  # 延遲不該擋在部署判定前面（跟部署是否成功無關，見 --probe 的說明）。
  local seed_cmdid
  seed_cmdid=$(aws ssm send-command --region "$REGION" --instance-ids "$iid" \
    --document-name AWS-RunShellScript --parameters commands='["cd /opt/trustforge","systemctl daemon-reload","if systemctl start fetch-scheduler.service; then echo \"[ec2] fetch-scheduler 一般排程執行成功（best-effort seed，fire-and-forget，非部署 gate）\"; else echo \"[ec2] ⚠️ fetch-scheduler 一般排程 exit 非零（best-effort，不影響部署判定——常見原因是來源端短暫限流如 reddit 429，跟基建/程式碼健康無關）\" >&2; journalctl -u fetch-scheduler -n 40 --no-pager >&2 || true; fi"]' \
    --query 'Command.CommandId' --output text 2>/dev/null || echo "")
  if [ -n "$seed_cmdid" ] && [ "$seed_cmdid" != "None" ]; then
    echo "[ec2] fetch-scheduler 一般排程 seed 已送出（CommandId=${seed_cmdid}，fire-and-forget，不等待、不影響部署判定；結果可事後用 aws ssm get-command-invocation 查）"
  else
    echo "[ec2] ⚠️ fetch-scheduler seed 送出失敗（best-effort，不影響部署判定，略過）" >&2
  fi

  # ---- 2) --probe：唯一的部署 gate，有界 ---------------------------------
  # codex HIGH-3：光跑一般排程（無參數）不夠——cache 全新鮮時 0 次真呼叫仍
  # exit 0，PutItem 被拒也測不到（見 fetch_scheduler.py::run_probe 的說明）。
  # `--probe` 完全不碰任何外部連接器 API、不看任何來源新鮮度，只對
  # DynamoDB cache/cost-ledger 兩表各做一次保證會發生的 PutItem/GetItem/
  # Scan（boto3 預設 client/read timeout + 內建重試，正常數秒等級完成，
  # 是貨真價實有界的操作）——獨立送出、獨立輪詢，不跟上面的 seed 共用
  # 同一個 SSM command，也不受 seed 耗時影響。
  local vcmdid
  vcmdid=$(aws ssm send-command --region "$REGION" --instance-ids "$iid" \
    --document-name AWS-RunShellScript --parameters commands='["set -e","cd /opt/trustforge","if ! ( AWS_REGION='"$REGION"' PYTHONPATH=/opt/trustforge CACHE_BACKEND=dynamodb TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger COST_LEDGER_BACKEND=dynamodb /usr/bin/python3 scripts/fetch_scheduler.py --probe ); then echo \"[ec2] fetch-scheduler --probe 失敗（DynamoDB cache/cost-ledger 至少一表 PutItem/GetItem 真的不通，可能被 permission boundary/SCP/table policy 擋，見 trustforge-dynamodb inline policy；probe 不依賴任何外部來源/cache 是否新鮮，一定會真的觸發一次寫入，是唯一的部署 gate）\" >&2; exit 1; fi","echo \"[ec2] fetch-scheduler probe 通過（DynamoDB cache/cost-ledger 讀寫權限已真的驗證過）\""]' \
    --query 'Command.CommandId' --output text)
  if [ -z "$vcmdid" ] || [ "$vcmdid" = "None" ]; then
    echo "[ec2] ❌ fetch-scheduler probe 用 SSM send-command 未取得 CommandId，中止" >&2
    exit 1
  fi
  # timeout 契約：poll deadline 只需要覆蓋 probe 自身的有界耗時，不再被
  # seed 的無界延遲拖累——這就是本輪架構修正的重點：gate 只依賴、只等待
  # probe 這一個獨立 SSM command。
  # codex HIGH（後續一輪）：光「拆出 probe」還不夠——probe 若透過
  # get_cache_backend()/get_ledger() 建構 DynamoDB client，走的是 boto3/
  # botocore 內建預設 connect/read timeout + 標準重試，降級時可達數分鐘，
  # 「有界」名不副實。已改成 `scripts/fetch_scheduler.py::run_probe()` 內部
  # 改用 `_probe_cache_backend()`/`_probe_ledger_backend()`，明確帶
  # connect_timeout=3.0s／read_timeout=3.0s／max_attempts=2（見該檔案常數
  # `_PROBE_DYNAMODB_*`），probe 現在是真正有界的：
  #   bounded worst-case ≈ 5 次序列 DynamoDB 操作
  #     （cache PutItem、cache GetItem、ledger PutItem、ledger GetItem、
  #       ledger Scan）
  #     × (connect_timeout 3s + read_timeout 3s) × max_attempts 2
  #     ≈ 5 × 6s × 2 = 60s
  # 90s 當 poll deadline，對這個 60s bounded worst-case 留 30s margin
  # （SSM 啟動/排程開銷 + 冪等 margin），不再是「猜一個夠大的數字」。
  # `|| true`：poll_ssm_terminal_status 逾時仍非終態時會 return 1，若不擋掉
  # 會被 `set -e` 在這行賦值當場中止腳本，跳過下面印診斷訊息的 if 區塊。
  local vstatus
  vstatus=$(poll_ssm_terminal_status "$vcmdid" "$iid" 90 5) || true
  if [ "$vstatus" != "Success" ]; then
    echo "[ec2] ❌ fetch-scheduler probe 同步驗證失敗：CommandId=$vcmdid Status=${vstatus}（等到終態或輪詢 90s 逾時仍非 Success；DynamoDB cache/cost-ledger 權限或程式有誤，deploy 視為失敗；跟上面的一般排程 seed 完全獨立送出/獨立判定，seed 成功與否不影響這裡）" >&2
    aws ssm get-command-invocation --region "$REGION" --command-id "$vcmdid" --instance-id "$iid" \
      --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
    exit 1
  fi
  echo "[ec2] ✅ fetch-scheduler probe 同步驗證成功（DynamoDB cache/cost-ledger 讀寫權限已真的驗證過；一般全源排程 seed 是獨立 fire-and-forget，不影響此 gate 判定）"
}

# codex HIGH：首次建置路徑原本只跑 verify_fetch_scheduler（DynamoDB probe），
# 沒有像 update-in-place 那樣同步驗證 web 服務本身健康
# （systemctl is-active trustforge + curl /healthz）。user-data 若在寫完
# fetch-scheduler unit 之後才失敗（相依套件裝失敗、web 服務起不來、port 沒
# 綁定等），DynamoDB probe 依然會過（DynamoDB R/W 本身沒問題）——deploy 會
# 回報成功，但公開服務其實是壞的。這裡補一個獨立的同步 SSM healthz gate，
# 邏輯比照 update-in-place 既有那段 for-loop（有限次重試、逾時印 journal、
# exit 非零），跟 verify_fetch_scheduler 各自獨立判定、兩者都過才算成功。
verify_web_healthz() {
  local iid="$1"
  local hcmdid
  # shellcheck disable=SC2016  # 單引號內的 $(seq..)/$i 刻意留給遠端展開，不
  # 是漏加雙引號（跟既有 healthz for-loop 那行同一個模式）。
  hcmdid=$(aws ssm send-command --region "$REGION" --instance-ids "$iid" \
    --document-name AWS-RunShellScript --parameters commands='["for i in $(seq 1 12); do systemctl is-active --quiet trustforge && curl -fsS http://localhost/healthz >/dev/null 2>&1 && exit 0; sleep 3; done; echo \"[ec2] healthz 檢查失敗（trustforge.service 沒 active 或 /healthz 逾時仍不通）\" >&2; journalctl -u trustforge -n 40 --no-pager >&2; exit 1"]' \
    --query 'Command.CommandId' --output text)
  if [ -z "$hcmdid" ] || [ "$hcmdid" = "None" ]; then
    echo "[ec2] ❌ web healthz 驗證用 SSM send-command 未取得 CommandId，中止" >&2
    exit 1
  fi
  # 同一個潛在誤判模式（見 poll_ssm_terminal_status 上方註解），這裡的遠端
  # 指令本身有界（最長 12*3=36s），但仍改用同款終態輪詢，避免日後跟
  # verify_fetch_scheduler 一樣在慢的環境下被 100s 內建逾時誤判。
  # timeout 契約稽核：這支遠端指令只有一個 healthz for-loop（≤36s），
  # 120s poll deadline 對它有寬裕 margin；跟 verify_fetch_scheduler 早前
  # 版本「poll timeout < 指令自身可能耗時」的矛盾不同款，這裡沒有無界
  # 外部依賴，不需要再壓縮/移除任何步驟或拆分 SSM command。
  # `|| true`：理由同 verify_fetch_scheduler 那處，避免 set -e 在逾時時
  # 於賦值行就把腳本靜默中止掉，吃掉後面的診斷訊息。
  local hstatus
  hstatus=$(poll_ssm_terminal_status "$hcmdid" "$iid" 120 5) || true
  if [ "$hstatus" != "Success" ]; then
    echo "[ec2] ❌ web healthz 同步驗證失敗：CommandId=$hcmdid Status=${hstatus}（等到終態或輪詢 120s 逾時仍非 Success；trustforge.service 沒 active 或 /healthz 逾時仍不通，deploy 視為失敗；跟 DynamoDB probe 各自獨立，probe 過不代表這裡也會過）" >&2
    aws ssm get-command-invocation --region "$REGION" --command-id "$hcmdid" --instance-id "$iid" \
      --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
    exit 1
  fi
  echo "[ec2] ✅ web healthz 同步驗證成功（trustforge.service active 且 /healthz 有回應）"
}

# 1) IAM 角色 + instance profile（最小權限）+ DynamoDB reconcile -------------
# 放在「既有實例？」分支判斷之前的共用段：IAM 是從部署主機用 aws cli 直接做
# （不經 SSM），首次建置跟 update-in-place 兩條路徑都會先跑過這裡，確保腳本
# 對 DynamoDB 權限自足——不依賴「CEO 手動補過一次 IAM」這種外部前提。
if [ "$BOOTSTRAP" = "1" ]; then
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  echo "[ec2] 建 IAM 角色 ${ROLE}…"
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore >/dev/null
  aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE" >/dev/null
  # #121 IAM 傳播重試：instance profile 剛建立後，EC2/SSM 端不一定立刻可見，
  # 固定 sleep 12s 在區域延遲較高時可能仍不足。改為有界重試：直到
  # get-instance-profile 真的回傳才繼續（最多 ~30s），避免 instance 啟動後
  # SSM 讀 token 因 AccessDenied 傳播未到而失敗（應用端 get_runtime_token
  # 另有指數退避重試作為第二道防線）。
  echo "[ec2] 等待 instance profile 傳播收斂…"
  for _i in $(seq 1 30); do
    if aws iam get-instance-profile --instance-profile-name "$ROLE" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi
# trustforge-inline（Bedrock + S3）最小權限：每次部署都 reconcile（put-role-policy
# 覆寫同名 policy，冪等安全），跟下面 DynamoDB policy 用同一套模式——不能只放在
# 「建新角色」分支裡，否則 update-in-place（角色已存在，跳過上面 if）會讓舊角色
# 永遠停留在收斂前的 wildcard 版本，region 白名單改了也吃不到（codex review #99
# HIGH：IAM 政策從未到達既有部署）。
# CISO hardening R2（#2b）：region 收斂——bedrock.py 預設 stance_model_id
# 用 `au.` 跨區 inference profile（見 bedrock.py 註解：只能從
# ap-southeast-2/4/6 呼叫，AWS 會在這 3 個 region 之間路由底層
# foundation-model 呼叫），所以 foundation-model Resource 不能收斂成單一
# $REGION，改明確列舉這 3 個白名單 region，不留 region 萬用字元 `*`。
# inference-profile 本身是呼叫端 account+region 的資源，用 $ACCT/$REGION
# 收斂（每次部署固定打單一 region，不會跨區呼叫）。
# #119（SecureString token 搬運）：主動補兩條窄範圍語句，不依賴
# AmazonSSMManagedInstanceCore 託管 policy 恰好放行的巧合（managed policy
# 版本可能演進）——ssm:GetParameter 的 Resource 鎖死本案參數前綴 ARN
# `parameter/trustforge/deploy/*`（不是 `*`）；kms:Decrypt 針對 SecureString
# 用的 AWS 託管 key alias/aws/ssm——alias 不能直接當 IAM Resource 比對
# （IAM 對 kms:Decrypt 只認 key ARN，alias ARN 不會匹配），故 Resource `*`
# + Condition kms:ViaService=ssm.$REGION.amazonaws.com 收斂：只有「經同區
# SSM 服務發起」的 decrypt 才放行，直接打 KMS API 解任何東西都不行。
# PR-A（runtime token SSM 讀取）：再補一條「獨立」的 ssm:GetParameter
# 語句，Resource 鎖死 `parameter/trustforge/runtime/*`（app 啟動期讀
# admin/live token 用的常駐參數前綴），跟上面 `deploy/*` 那條各自獨立、
# 不合併成單一 Resource 陣列，避免未來其中一個前綴需要調整範圍時互相
# 牽動。kms:Decrypt 沿用同一條既有語句（同一支 AWS 託管 key、同一個
# ViaService 收斂條件），runtime/* 讀取不需要新增任何 KMS 權限。
echo "[ec2] reconcile Bedrock/S3 IAM inline policy（${ROLE}）…"
aws iam put-role-policy --role-name "$ROLE" --policy-name trustforge-inline \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":\"bedrock:InvokeModel\",\"Resource\":[
      \"arn:aws:bedrock:ap-southeast-2::foundation-model/anthropic.*\",
      \"arn:aws:bedrock:ap-southeast-4::foundation-model/anthropic.*\",
      \"arn:aws:bedrock:ap-southeast-6::foundation-model/anthropic.*\",
      \"arn:aws:bedrock:$REGION:$ACCT:inference-profile/*anthropic*\"]},
    {\"Effect\":\"Allow\",\"Action\":\"s3:GetObject\",\"Resource\":\"arn:aws:s3:::$BUCKET/*\"},
    {\"Effect\":\"Allow\",\"Action\":[\"ssm:GetParameter\",\"ssm:GetParametersByPath\",\"ssm:DeleteParameter\"],\"Resource\":\"arn:aws:ssm:$REGION:$ACCT:parameter/trustforge/deploy/*\"},
    {\"Effect\":\"Allow\",\"Action\":\"ssm:GetParameter\",\"Resource\":\"arn:aws:ssm:$REGION:$ACCT:parameter/trustforge/runtime/*\"},
    {\"Effect\":\"Allow\",\"Action\":\"kms:Decrypt\",\"Resource\":\"*\",\"Condition\":{\"StringEquals\":{\"kms:ViaService\":\"ssm.$REGION.amazonaws.com\"}}}]}" >/dev/null
# #104.5：dedup fail-open 告警 + #75 後端失效指標都走 CloudWatch
# put_metric_data，必須給實例角色 `cloudwatch:PutMetricData`（該 action 無
# resource-level 權限，只能給 Resource "*"；僅用於送出自定義觀測/降級指標，
# 不影響其他 AWS 資源）。TRUSTFORGE_CW_METRICS=1 會在 unit 內開啟（見下）。
echo "[ec2] reconcile CloudWatch 指標上報 IAM inline policy（${ROLE}）…"
aws iam put-role-policy --role-name "$ROLE" --policy-name trustforge-cloudwatch \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":\"cloudwatch:PutMetricData\",\"Resource\":\"*\"}]}" >/dev/null
# DynamoDB 最小權限：每次部署都 reconcile（put-role-policy 覆寫同名 policy，
# 冪等安全），鎖死兩個 table 各自的 ARN，不給萬用 Resource "*"。
echo "[ec2] reconcile DynamoDB IAM inline policy（${ROLE}）…"
aws iam put-role-policy --role-name "$ROLE" --policy-name trustforge-dynamodb \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":[\"dynamodb:GetItem\",\"dynamodb:PutItem\",\"dynamodb:Scan\",\"dynamodb:Query\"],
     \"Resource\":\"arn:aws:dynamodb:$REGION:$ACCT:table/trustforge-connector-cache\"},
    {\"Effect\":\"Allow\",\"Action\":[\"dynamodb:GetItem\",\"dynamodb:PutItem\",\"dynamodb:Scan\"],
     \"Resource\":\"arn:aws:dynamodb:$REGION:$ACCT:table/trustforge-cost-ledger\"}]}" >/dev/null
# 表本身不歸這支腳本建（CDO/db-ops 範疇），但部署前先確認表存在，不存在就
# 提早、明確地失敗，而不是讓 scheduler 之後每次 exit 1 卻沒人發現。
for T in trustforge-connector-cache trustforge-cost-ledger; do
  if ! aws dynamodb describe-table --region "$REGION" --table-name "$T" >/dev/null 2>&1; then
    echo "[ec2] ❌ DynamoDB 表 $T 在 $REGION 不存在，cache/ledger 會失敗，中止部署" >&2
    exit 1
  fi
done

# #75：建立 budget guard 表 + 掛最小權限 IAM（多實例預留保護）。這支腳本不寫
# 任何 token，只建表 + put-role-policy（鎖該表 ARN 的 UpdateItem/GetItem）。
# 表不存在就建、IAM 每次 reconcile（冪等）。建完確認表真的存在，否則多實例
# 保護會靜默失效（app 端會送 BudgetGuardMultiInstanceProtectionDisabled 指標
# 標記此降級，但部署不該把這種狀態當成功）。
echo "[ec2] 建立 budget guard 表 + IAM（#75）…"
if ! "$(dirname "$0")/setup_budget_guard_dynamodb.sh"; then
  echo "[ec2] ❌ budget guard 表/IAM 建置失敗，中止部署" >&2
  exit 1
fi
echo "[ec2] 建立 durable idempotency lease 表 + IAM（H-19）…"
if ! "$(dirname "$0")/setup_idempotency_lease_dynamodb.sh"; then
  echo "[ec2] ❌ idempotency lease 表/IAM 建置失敗，中止部署" >&2
  exit 1
fi
if ! aws dynamodb describe-table --region "$REGION" --table-name "$LEASE_TABLE" >/dev/null 2>&1; then
  echo "[ec2] ❌ DynamoDB lease 表 $LEASE_TABLE 在 $REGION 不存在，中止部署" >&2
  exit 1
fi
BG_TABLE="${TRUSTFORGE_BUDGET_COUNTER_TABLE:-trustforge-budget-guard}"
if ! aws dynamodb describe-table --region "$REGION" --table-name "$BG_TABLE" >/dev/null 2>&1; then
  echo "[ec2] ❌ DynamoDB 表 $BG_TABLE 在 $REGION 不存在，budget 多實例保護會失效，中止部署" >&2
  exit 1
fi
else
  echo "[ec2] skip AWS bootstrap (TRUSTFORGE_BOOTSTRAP=${BOOTSTRAP}); using provisioned production infrastructure"
fi

# 2) 打包 + 上傳 S3 -----------------------------------------------------------
echo "[ec2] 打包應用 zip…"
B=$(mktemp -d); ZIP="$(pwd)/build/trustforge_app.zip"; mkdir -p build
cp -r src/trustforge "$B/trustforge"; cp -r data "$B/data"; cp -r demo "$B/demo"
# scripts/：排程 fetcher（fetch_scheduler.py）跟著一起打包，否則 systemd timer
# 在 EC2 上會找不到檔案（見下方 fetch-scheduler.service ExecStart）。
cp -r scripts "$B/scripts"
cp -r skills "$B/skills"
# deploy/：reconcile_trustforge_unit.sh 等部署期 glue 需在 EC2 上由 unit 的
# ExecStartPre 呼叫，故一併打包（不含任何 token 值，只含腳本邏輯）。reconcile
# 腳本本身會再呼叫 scripts/sweep_deploy_parameters.sh。
cp -r deploy "$B/deploy"
# Source-control mode bits are not guaranteed for shell helpers in every local
# checkout.  systemd ExecStartPre requires an executable bit after unzip.
chmod +x "$B/scripts/"*.sh "$B/deploy/"*.sh
# 第三輪 AI 友善：`GET /api/openapi.yaml`／`GET /llms.txt` 是純讀檔回傳（見
# `src/trustforge/web.py::_handle_openapi_spec`/`_handle_llms_txt`，路徑解析
# 靠 systemd unit 已設的 `TRUSTFORGE_HOME=/opt/trustforge`），這兩份檔案不
# 隨套件走，需另外打包，否則兩端點在 EC2 生產環境會 404。只帶 `docs/api`
# （實際被 serve 的部分），不帶整棵 `docs/`（archive/plans/qa 等內部規劃
# 文件不對外）。
mkdir -p "$B/docs"; cp -r docs/api "$B/docs/api"; cp llms.txt "$B/llms.txt"
GIT_VER=$(git describe --tags --always --dirty 2>/dev/null || echo dev)
printf 'VERSION = "%s"\n' "$GIT_VER" > "$B/trustforge/_version.py"
echo "[ec2] 版號 = $GIT_VER"
( cd "$B" && zip -qr "$ZIP" trustforge data demo scripts skills deploy docs llms.txt -x '*/__pycache__/*' ); rm -rf "$B"

aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null || \
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
aws s3 cp "$ZIP" "s3://$BUCKET/trustforge_app.zip" --region "$REGION" >/dev/null
echo "[ec2] 已上傳 s3://$BUCKET/trustforge_app.zip"

# 3) 既有實例？→ update-in-place（重用現有 EC2，不 run-instances）-----------
# EIP 已附著在 tag Name=trustforge-demo 的既有實例上；只要不 terminate 該實例，
# EIP 就會一直留在上面（stop/start 也保留附著，不用重綁）→ 查到就重用，不建新的。
# 實際查詢（describe-instances）與 $MATCHES/$MATCH_COUNT 的計算已提前到檔頭
# （見上方 UNIT_ENV_RECONCILE_CMDS 之後那段），這裡不再重查一次——只消費那裡
# 算好的變數，接手做分流判斷。
if [ "$MATCH_COUNT" -gt 1 ]; then
  echo "[ec2] ❌ 找到 $MATCH_COUNT 個相符實例（tag Name=trustforge-demo，非 terminated），無法安全判斷，中止" >&2
  printf '%s\n' "$MATCHES" >&2
  exit 1
elif [ "$MATCH_COUNT" -eq 1 ]; then
  IID=$(printf '%s\n' "$MATCHES" | awk '{print $1}')
  STATE=$(printf '%s\n' "$MATCHES" | awk '{print $2}')
  case "$STATE" in
    running)
      echo "[ec2] 既有實例 ${IID}（running）→ update-in-place（不 run-instances）"
      ;;
    stopped)
      echo "[ec2] 既有實例 ${IID}（stopped，省 credit 常態）→ 先開機再 update-in-place"
      aws ec2 start-instances --region "$REGION" --instance-ids "$IID" >/dev/null
      aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"
      echo "[ec2] 等待 SSM agent 上線…"
      SSM_READY=""
      for _try in $(seq 1 30); do
        PING=$(aws ssm describe-instance-information --region "$REGION" \
          --filters Key=InstanceIds,Values="$IID" \
          --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo "")
        if [ "$PING" = "Online" ]; then
          SSM_READY=1
          break
        fi
        sleep 5
      done
      if [ -z "$SSM_READY" ]; then
        echo "[ec2] ❌ 實例 $IID 已開機但 SSM agent 逾時仍未 Online，中止" >&2
        exit 1
      fi
      echo "[ec2] 既有實例 $IID 已開機且 SSM ready → update-in-place（不 run-instances）"
      ;;
    *)
      echo "[ec2] ❌ 既有實例 $IID 狀態為「${STATE}」（過渡態），無法安全判斷是否重複，中止" >&2
      exit 1
      ;;
  esac

  # 遠端指令最前面加 set -e：任一步失敗就整段中止，不會壞版本仍 restart。
  # 同時把 systemd 的 BEDROCK_MODEL_ID 重寫成本次的 ${MODEL}（離線部署就清空）——
  # 否則曾經開過真模型 online 的實例，離線重部署後 systemd 環境沒被更新，
  # service 仍帶著舊的 BEDROCK_MODEL_ID 繼續跑，等於「離線部署」沒真的離線、
  # 持續燒 credit。
  # shellcheck disable=SC2016  # 單引號內的 $(seq..)/$i 是刻意不在本機展開，
  # 要留給遠端 SSM 執行時才展開，不是漏加雙引號。
  # 同時確保 CACHE_BACKEND/TRUSTFORGE_CACHE_TABLE/TRUSTFORGE_COST_LEDGER_TABLE/
  # COST_LEDGER_BACKEND 四個 env 存在（既有舊實例可能是本次改動前建的、根本
  # 沒這些行）：先 grep 判斷有沒有 → 有就 sed 取代值，沒有就在 PYTHONPATH 那行
  # 後面插入，兩邊都是同一組固定值，冪等（重跑不會重複插入）。同時（重）寫入
  # fetch-scheduler.service/.timer 並 enable，確保排程 fetcher 在既有實例上
  # 也補上，不會因為是「重用舊機器」就漏掉。
  # 管理控制台 PR-5：$UNIT_ENV_RECONCILE_CMDS（見檔頭 ssm_env_cmd）同步
  # reconcile 兩件事——(1) 遷移：無條件刪除 unit 檔裡 #119 時代殘留的
  # TRUSTFORGE_ADMIN_TOKEN / TRUSTFORGE_LIVE_TOKEN 整行（本 PR 的安全價值：
  # token 離開 unit 檔落地點，改由 app 啟動期自行從 SSM 讀取）；(2)
  # TRUSTFORGE_BEDROCK_DAILY_USD_CAP / TRUSTFORGE_TOKEN_SSM_PREFIX 兩行——
  # 有值＝取代或插入，未設＝整行刪除（fail-closed：不是留舊值也不是寫空
  # 值），跟 BEDROCK_MODEL_ID 的「每次部署都重寫成本次的值」同一個精神。
  # 緊接著 `chmod 600` 該 unit 檔（harper CISO L-4）：預設 644
  # world-readable，每次部署都無條件收緊一次（冪等）。
  CMDID=$(aws ssm send-command --region "$REGION" --instance-ids "$IID" \
    --document-name AWS-RunShellScript --parameters commands='["set -e","cd /opt/trustforge","aws s3 cp s3://'"$BUCKET"'/trustforge_app.zip ./app.zip --region '"$REGION"'","unzip -o app.zip","sed -i \"s|^Environment=BEDROCK_MODEL_ID=.*|Environment=BEDROCK_MODEL_ID='"$MODEL"'|\" /etc/systemd/system/trustforge.service","if grep -q \"^Environment=CACHE_BACKEND=\" /etc/systemd/system/trustforge.service; then sed -i \"s|^Environment=CACHE_BACKEND=.*|Environment=CACHE_BACKEND=dynamodb|\" /etc/systemd/system/trustforge.service; else sed -i \"/^Environment=PYTHONPATH=/a Environment=CACHE_BACKEND=dynamodb\" /etc/systemd/system/trustforge.service; fi","if grep -q \"^Environment=TRUSTFORGE_CACHE_TABLE=\" /etc/systemd/system/trustforge.service; then sed -i \"s|^Environment=TRUSTFORGE_CACHE_TABLE=.*|Environment=TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache|\" /etc/systemd/system/trustforge.service; else sed -i \"/^Environment=PYTHONPATH=/a Environment=TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache\" /etc/systemd/system/trustforge.service; fi","if grep -q \"^Environment=TRUSTFORGE_COST_LEDGER_TABLE=\" /etc/systemd/system/trustforge.service; then sed -i \"s|^Environment=TRUSTFORGE_COST_LEDGER_TABLE=.*|Environment=TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger|\" /etc/systemd/system/trustforge.service; else sed -i \"/^Environment=PYTHONPATH=/a Environment=TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger\" /etc/systemd/system/trustforge.service; fi","if grep -q \"^Environment=COST_LEDGER_BACKEND=\" /etc/systemd/system/trustforge.service; then sed -i \"s|^Environment=COST_LEDGER_BACKEND=.*|Environment=COST_LEDGER_BACKEND=dynamodb|\" /etc/systemd/system/trustforge.service; else sed -i \"/^Environment=PYTHONPATH=/a Environment=COST_LEDGER_BACKEND=dynamodb\" /etc/systemd/system/trustforge.service; fi"'"$UNIT_ENV_RECONCILE_CMDS"',"chmod 600 /etc/systemd/system/trustforge.service","bash deploy/install_hermes_scheduler.sh","systemctl restart trustforge","systemctl enable --now fetch-scheduler.timer","for i in $(seq 1 12); do systemctl is-active --quiet trustforge && curl -fsS http://localhost/healthz >/dev/null 2>&1 && exit 0; sleep 3; done; echo \"[ec2] healthz 檢查失敗\"; journalctl -u trustforge -n 40 --no-pager; exit 1"]' \
    --query 'Command.CommandId' --output text)
  if [ -z "$CMDID" ] || [ "$CMDID" = "None" ]; then
    echo "[ec2] ❌ SSM send-command 未取得 CommandId，中止" >&2
    exit 1
  fi
  echo "[ec2] SSM CommandId=${CMDID}，等待遠端執行完成…"
  # send-command 只確認「已接受」是非同步的；輪詢到終態再查真正的執行結果
  # 狀態（同款 poll_ssm_terminal_status，避免 `aws ssm wait command-executed`
  # 內建 ~100s 逾時把還在跑的 InProgress 誤判成失敗）。
  # timeout 契約稽核：這支遠端指令是 s3 cp+unzip+多個 sed+寫兩個 unit
  # 檔+daemon-reload+restart trustforge+`enable --now fetch-scheduler.timer`
  # （只啟用計時器，非同步觸發，不會在這裡阻塞等一般排程跑完）+healthz
  # for-loop（≤36s）——沒有任何一步會同步等「一般排程」跑完，180s poll
  # deadline 有寬裕 margin，跟 verify_fetch_scheduler 的矛盾不同款。
  # `|| true`：理由同上，避免 set -e 在逾時時於賦值行就靜默中止腳本。
  SSM_STATUS=$(poll_ssm_terminal_status "$CMDID" "$IID" 180 5) || true
  if [ "$SSM_STATUS" != "Success" ]; then
    echo "[ec2] ❌ update-in-place 失敗：SSM CommandId=$CMDID Status=${SSM_STATUS}（等到終態或輪詢 180s 逾時仍非 Success）" >&2
    aws ssm get-command-invocation --region "$REGION" --command-id "$CMDID" --instance-id "$IID" \
      --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
    exit 1
  fi
  # #121.6：reconcile 既有實例的 trustforge.service，補上 sweep_deploy_parameters
  # ExecStartPre（冪等，已存在就跳過）。runtime token 直接由 app 啟動期從 SSM
  # 讀取（SSM 路徑，已移除 systemd tmpfs 憑證層）。接著 daemon-reload + restart
  # 讓 unit 變更生效。
  rid=$(aws ssm send-command --region "$REGION" --instance-ids "$IID" \
    --document-name AWS-RunShellScript --parameters commands='["set -e","cd /opt/trustforge","bash /opt/trustforge/scripts/reconcile_trustforge_unit.sh","systemctl daemon-reload","systemctl restart trustforge"]' \
    --query 'Command.CommandId' --output text)
  if [ -z "$rid" ] || [ "$rid" = "None" ]; then
    echo "[ec2] ❌ unit reconcile 用 SSM send-command 未取得 CommandId，中止" >&2
    exit 1
  fi
  rstatus=$(poll_ssm_terminal_status "$rid" "$IID" 120 5) || true
  if [ "$rstatus" != "Success" ]; then
    echo "[ec2] ❌ unit reconcile 失敗：SSM CommandId=$rid Status=${rstatus}" >&2
    aws ssm get-command-invocation --region "$REGION" --command-id "$rid" --instance-id "$IID" \
      --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
    exit 1
  fi
  echo "[ec2] ✅ 既有實例 unit 已 reconcile（sweep）"
  # 主要 config SSM 成功不代表 fetch-scheduler 真的能跑（IAM 權限不足只有實
  # 際執行才會露餡）：再同步跑一次，非成功就讓整支腳本 exit 非零。
  verify_fetch_scheduler "$IID"
  IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
  echo "[ec2] ✅ 既有實例已更新：$IID  公開 IP：${IP}（EIP 未動，模型=${MODEL:-<離線>}）"
  echo "[ec2] 🔗 Live Demo：http://$IP/"
  echo "[ec2]   健康檢查：http://$IP/healthz"
  exit 0
fi
echo "[ec2] 無既有實例（tag Name=trustforge-demo，非 terminated）→ 首次建置流程"

# 4) Security group（開 80 公開）---------------------------------------------
VPC=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SGID=$(aws ec2 describe-security-groups --region "$REGION" --filters Name=group-name,Values=$SG Name=vpc-id,Values=$VPC --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo None)
if [ "$SGID" = "None" ] || [ -z "$SGID" ]; then
  echo "[ec2] 建 security group（開 80）…"
  SGID=$(aws ec2 create-security-group --region "$REGION" --group-name "$SG" \
    --description "TrustForge demo 80" --vpc-id "$VPC" --query GroupId --output text)
  aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SGID" \
    --protocol tcp --port 80 --cidr 0.0.0.0/0 >/dev/null
fi
echo "[ec2] SG=$SGID VPC=$VPC"

# 5) user-data（開機自動裝 + 跑）---------------------------------------------
# runtime token 由 app 啟動期直接從 SSM Parameter Store（SecureString + KMS，
# WithDecryption）讀取——不經 argv / env / 持久碟 / 日誌，fail-closed。本 user-data
# 不含任何 token 值與 systemd tmpfs 憑證層（該層經 codex-review 第三輪實測確認在
# 真實部署路徑完全失效且有「假安全感 + 服務起不來」風險，已移除並回退 SSM 路徑）。
UD=$(mktemp)
cat > "$UD" <<EOF
#!/bin/bash
set -x
dnf install -y python3 python3-pip unzip >/var/log/tf-setup.log 2>&1
pip3 install boto3 >>/var/log/tf-setup.log 2>&1
mkdir -p /opt/trustforge && cd /opt/trustforge
aws s3 cp s3://$BUCKET/trustforge_app.zip ./app.zip --region $REGION >>/var/log/tf-setup.log 2>&1
unzip -o app.zip >>/var/log/tf-setup.log 2>&1
cat > /etc/systemd/system/trustforge.service <<UNIT
[Unit]
Description=TrustForge web
After=network.target
[Service]
Environment=PORT=80
Environment=TRUSTFORGE_HOME=/opt/trustforge
Environment=AWS_REGION=$REGION
Environment=BEDROCK_MODEL_ID=$MODEL
Environment=PYTHONPATH=/opt/trustforge
Environment=CACHE_BACKEND=dynamodb
Environment=TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache
Environment=TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger
Environment=COST_LEDGER_BACKEND=dynamodb
# #121.6：部署期臨時 SSM 參數時間窗 sweep（非致命，失敗不擋啟動）。
ExecStartPre=/opt/trustforge/scripts/sweep_deploy_parameters.sh
${EXTRA_UNIT_ENV}ExecStart=/usr/bin/python3 -m trustforge.web
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
# harper CISO L-4：systemd unit 檔預設權限通常是 644（world-readable），
# 而這份 unit 若有帶 admin/live token（明碼寫在 unit 檔的環境變數行裡），
# 本機任何登入帳號都能用 cat 這個檔案讀到 token——收緊成 600（僅 root 可
# 讀寫），不管這次有沒有帶 token 都一律收緊（防禦性、冪等，也涵蓋往後有人
# 重跑帶 token 更新的情況）。
chmod 600 /etc/systemd/system/trustforge.service
# 排程 fetcher（scripts/fetch_scheduler.py）：唯一打真連接器 API 的地方，寫入
# DynamoDB cache 供上面 web 進程讀。單一 timer 每 15min 觸發一次「全部來源」，
# 由腳本內建的新鮮度守門依各源 refresh 間隔自行決定該不該真的打（見
# deploy/README.md「排程 fetcher」章節，方式 A）。
cat > /etc/systemd/system/fetch-scheduler.service <<UNIT2
[Unit]
Description=TrustForge connector cache fetch scheduler
[Service]
Type=oneshot
WorkingDirectory=/opt/trustforge
Environment=AWS_REGION=$REGION
Environment=PYTHONPATH=/opt/trustforge
Environment=CACHE_BACKEND=dynamodb
Environment=TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache
Environment=TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger
Environment=COST_LEDGER_BACKEND=dynamodb
ExecStartPre=/usr/bin/python3 scripts/fetch_scheduler.py --probe
ExecStart=/usr/bin/python3 scripts/fetch_scheduler.py --allow-partial
UNIT2
cat > /etc/systemd/system/fetch-scheduler.timer <<UNIT3
[Unit]
Description=Run TrustForge fetch scheduler periodically
[Timer]
OnBootSec=1min
OnUnitActiveSec=15min
[Install]
WantedBy=timers.target
UNIT3
systemctl daemon-reload && systemctl enable --now trustforge >>/var/log/tf-setup.log 2>&1
systemctl enable --now fetch-scheduler.timer >>/var/log/tf-setup.log 2>&1
bash /opt/trustforge/deploy/install_hermes_scheduler.sh >>/var/log/tf-setup.log 2>&1
EOF

# 6) AMI + 啟動實例 -----------------------------------------------------------
AMI=$(aws ssm get-parameter --region "$REGION" \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query Parameter.Value --output text)
echo "[ec2] AMI=$AMI 啟動 ${INSTANCE_TYPE}…"
# --client-token：同一次呼叫若被 CLI/SDK 底層重試（網路抖動、throttle），AWS 用
# 這個 token 判斷是同一請求，不會重複建實例。用小時級時間戳當 token：同小時內
# 的重試/短時間並行競態會被 AWS 去重合併成同一個實例；跨小時的正常重跑則會拿
# 到新 token，不會被舊 token 卡住誤判成「已存在」（見檔頭 TOCTOU 假設說明）。
CLIENT_TOKEN="trustforge-demo-$(date -u +%Y%m%dT%H)"
# harper CISO L-3：--metadata-options HttpTokens=required 強制 IMDSv2（拒絕
# IMDSv1 明碼 GET）——若這台 EC2 上跑的應用有 SSRF 漏洞，攻擊者能誘使伺服器
# 對內發請求，IMDSv1 只要一個 GET 就能撈到 instance profile 憑證；IMDSv2
# 要求先 PUT 換一次性 session token 才能讀，SSRF 若不能自訂 HTTP method/
# header（多數 SSRF 只能控制 URL），就打不到 IMDS 憑證，等於堵掉「SSRF →
# 偷 instance role 憑證」這條路。
IID=$(aws ec2 run-instances --region "$REGION" --image-id "$AMI" \
  --instance-type "$INSTANCE_TYPE" --iam-instance-profile Name=$ROLE \
  --security-group-ids "$SGID" --associate-public-ip-address \
  --metadata-options HttpTokens=required \
  --user-data "file://$UD" --client-token "$CLIENT_TOKEN" \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=trustforge-demo}]' \
  --query 'Instances[0].InstanceId' --output text)
rm -f "$UD"
echo "[ec2] 實例 $IID 啟動中…"
aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"
# 建立後複查一次：TOCTOU 沒加跨程序鎖，這裡只做「事後偵測、印警告」的補強，
# 真的建出重複也不自動刪，交人工確認（避免自動刪錯正在服務的實例）。
# 注意：不能用 flat scalar 的 `Instances[].InstanceId` + grep -c 數行——AWS CLI
# text 輸出會把多個 scalar 擠在同一個 tab 分隔行裡，多實例時 grep -c 仍只算
# 出 1 行，「>1 印警告」就會被吃掉。改用 length() 直接數，需要清單時才再查一次。
RECHECK_COUNT=$(aws ec2 describe-instances --region "$REGION" \
  --filters Name=tag:Name,Values=trustforge-demo \
    Name=instance-state-name,Values=pending,running,shutting-down,stopping,stopped \
  --query 'length(Reservations[].Instances[])' --output text 2>/dev/null || echo 0)
if [ "$RECHECK_COUNT" -gt 1 ]; then
  RECHECK_IDS=$(aws ec2 describe-instances --region "$REGION" \
    --filters Name=tag:Name,Values=trustforge-demo \
      Name=instance-state-name,Values=pending,running,shutting-down,stopping,stopped \
    --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null || echo "")
  echo "[ec2] ⚠️  警告：建立後複查發現 ${RECHECK_COUNT} 個相符實例（tag Name=trustforge-demo），疑似並行部署造成重複，請人工確認並清理：${RECHECK_IDS}" >&2
fi
echo "[ec2] 等待 SSM agent 上線（供下面同步驗證 fetch-scheduler 用）…"
SSM_READY=""
for _try in $(seq 1 30); do
  PING=$(aws ssm describe-instance-information --region "$REGION" \
    --filters Key=InstanceIds,Values="$IID" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo "")
  if [ "$PING" = "Online" ]; then
    SSM_READY=1
    break
  fi
  sleep 5
done
if [ -z "$SSM_READY" ]; then
  echo "[ec2] ❌ 實例 $IID 已開機但 SSM agent 逾時仍未 Online，無法驗證 fetch-scheduler，中止" >&2
  exit 1
fi
# 首次建置：不能只 enable --now timer 就當成功——user-data 裡 systemd unit
# 都寫好之後，實際同步跑一次才知道 DynamoDB IAM 權限是否真的夠（這就是本次
# 修的 HIGH：舊版腳本角色只有 Bedrock+S3，scheduler 會一路 exit 1 但 deploy
# 卻回報成功）。
# 另一個 codex HIGH：光 --probe 過只證明 DynamoDB R/W 沒問題，證明不了 user-
# data 裝完套件、web 服務真的有起來——這裡先跑 update-in-place 路徑既有的
# 那種 web healthz gate，兩個 gate 都過才報成功，任一失敗都讓部署 exit 1。
verify_web_healthz "$IID"
verify_fetch_scheduler "$IID"
IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "[ec2] ✅ 實例上線：$IID  公開 IP：$IP"
echo "[ec2] 🔗 Live Demo（開機裝好約 1-2 分後）：http://$IP/"
echo "[ec2]   健康檢查：http://$IP/healthz"
echo "[ec2] 停用省 credit：aws ec2 stop-instances --instance-ids $IID --region $REGION"
