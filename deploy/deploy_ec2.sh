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
# 註：真正開受控 live 需同時設 BEDROCK_MODEL_ID + 傳 TRUSTFORGE_LIVE_TOKEN 進 systemd
# (本腳本尚未傳 token → live 完整化為 follow-up；今日離線部署不需要)。
MODEL="${BEDROCK_MODEL_ID-}"
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="trustforge-deploy-${ACCT}"
ROLE=trustforge-ec2
SG=trustforge-ec2-sg
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.micro}"

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
verify_fetch_scheduler() {
  local iid="$1"
  local vcmdid
  # shellcheck disable=SC2016  # 單引號內的 $(seq..)/$i 刻意留給遠端展開，不
  # 是漏加雙引號（跟既有 healthz for-loop 那行同一個模式）。
  # codex HIGH-3：光跑一般排程（無參數）不夠——cache 全新鮮時 0 次真呼叫仍
  # exit 0，PutItem 被拒也測不到（見 fetch_scheduler.py::run_probe 的說明）。
  # 這裡先跑一次一般排程當 best-effort seed（讓 cache 儘早有資料可用，但
  # 「來源端短暫失敗如 reddit 429」不該讓部署失敗，所以刻意不判斷它的 exit
  # code），真正的部署 gate 是接下來不依賴 freshness、不碰任何外部來源的
  # `--probe`（真的觸發一次 PutItem/GetItem，只測 DynamoDB R/W/IAM）。
  vcmdid=$(aws ssm send-command --region "$REGION" --instance-ids "$iid" \
    --document-name AWS-RunShellScript --parameters commands='["set -e","for i in $(seq 1 40); do [ -f /etc/systemd/system/fetch-scheduler.service ] && break; sleep 5; done","if [ ! -f /etc/systemd/system/fetch-scheduler.service ]; then echo \"[ec2] fetch-scheduler.service 逾時仍未由 user-data 建立\" >&2; exit 1; fi","systemctl daemon-reload","if systemctl start fetch-scheduler.service; then echo \"[ec2] fetch-scheduler 一般排程執行成功（best-effort seed，非部署 gate）\"; else echo \"[ec2] ⚠️ fetch-scheduler 一般排程 exit 非零（best-effort，不讓部署失敗——常見原因是來源端短暫限流如 reddit 429，跟基建/程式碼健康無關；真正的部署 gate 是下一步 --probe）\" >&2; journalctl -u fetch-scheduler -n 40 --no-pager >&2 || true; fi","if ! ( cd /opt/trustforge && AWS_REGION='"$REGION"' PYTHONPATH=/opt/trustforge CACHE_BACKEND=dynamodb TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger COST_LEDGER_BACKEND=dynamodb /usr/bin/python3 scripts/fetch_scheduler.py --probe ); then echo \"[ec2] fetch-scheduler --probe 失敗（DynamoDB cache/cost-ledger 至少一表 PutItem/GetItem 真的不通，可能被 permission boundary/SCP/table policy 擋，見 trustforge-dynamodb inline policy；probe 不依賴任何外部來源/cache 是否新鮮，一定會真的觸發一次寫入，是唯一的部署 gate）\" >&2; exit 1; fi","echo \"[ec2] fetch-scheduler probe 通過（DynamoDB cache/cost-ledger 讀寫權限已真的驗證過）\""]' \
    --query 'Command.CommandId' --output text)
  if [ -z "$vcmdid" ] || [ "$vcmdid" = "None" ]; then
    echo "[ec2] ❌ fetch-scheduler 驗證用 SSM send-command 未取得 CommandId，中止" >&2
    exit 1
  fi
  aws ssm wait command-executed --region "$REGION" --command-id "$vcmdid" --instance-id "$iid" 2>/dev/null || true
  local vstatus
  vstatus=$(aws ssm get-command-invocation --region "$REGION" \
    --command-id "$vcmdid" --instance-id "$iid" --query Status --output text)
  if [ "$vstatus" != "Success" ]; then
    echo "[ec2] ❌ fetch-scheduler 同步驗證失敗：CommandId=$vcmdid Status=${vstatus}（--probe 未通過，DynamoDB cache/cost-ledger 權限或程式有誤，deploy 視為失敗；一般全源排程僅 best-effort seed，其失敗不影響此判定，不可能是它造成這裡非 Success）" >&2
    exit 1
  fi
  echo "[ec2] ✅ fetch-scheduler 同步驗證成功（--probe 通過，DynamoDB cache/cost-ledger 讀寫權限已真的驗證過；一般全源排程結果僅供參考 log，不影響此 gate，避免來源端短暫失敗如 reddit 429 造成部署誤判失敗）"
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
  aws ssm wait command-executed --region "$REGION" --command-id "$hcmdid" --instance-id "$iid" 2>/dev/null || true
  local hstatus
  hstatus=$(aws ssm get-command-invocation --region "$REGION" \
    --command-id "$hcmdid" --instance-id "$iid" --query Status --output text)
  if [ "$hstatus" != "Success" ]; then
    echo "[ec2] ❌ web healthz 同步驗證失敗：CommandId=$hcmdid Status=${hstatus}（trustforge.service 沒 active 或 /healthz 逾時仍不通，deploy 視為失敗；跟 DynamoDB probe 各自獨立，probe 過不代表這裡也會過）" >&2
    exit 1
  fi
  echo "[ec2] ✅ web healthz 同步驗證成功（trustforge.service active 且 /healthz 有回應）"
}

# 1) IAM 角色 + instance profile（最小權限）+ DynamoDB reconcile -------------
# 放在「既有實例？」分支判斷之前的共用段：IAM 是從部署主機用 aws cli 直接做
# （不經 SSM），首次建置跟 update-in-place 兩條路徑都會先跑過這裡，確保腳本
# 對 DynamoDB 權限自足——不依賴「CEO 手動補過一次 IAM」這種外部前提。
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  echo "[ec2] 建 IAM 角色 ${ROLE}…"
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore >/dev/null
  aws iam put-role-policy --role-name "$ROLE" --policy-name trustforge-inline \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
      {\"Effect\":\"Allow\",\"Action\":\"bedrock:InvokeModel\",\"Resource\":[
        \"arn:aws:bedrock:*::foundation-model/anthropic.*\",
        \"arn:aws:bedrock:*:*:inference-profile/*anthropic*\"]},
      {\"Effect\":\"Allow\",\"Action\":\"s3:GetObject\",\"Resource\":\"arn:aws:s3:::$BUCKET/*\"}]}" >/dev/null
  aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE" >/dev/null
  echo "[ec2] 等 instance profile 生效…"; sleep 12
fi
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

# 2) 打包 + 上傳 S3 -----------------------------------------------------------
echo "[ec2] 打包應用 zip…"
B=$(mktemp -d); ZIP="$(pwd)/build/trustforge_app.zip"; mkdir -p build
cp -r src/trustforge "$B/trustforge"; cp -r data "$B/data"; cp -r demo "$B/demo"
# scripts/：排程 fetcher（fetch_scheduler.py）跟著一起打包，否則 systemd timer
# 在 EC2 上會找不到檔案（見下方 fetch-scheduler.service ExecStart）。
cp -r scripts "$B/scripts"
GIT_VER=$(git describe --tags --always --dirty 2>/dev/null || echo dev)
printf 'VERSION = "%s"\n' "$GIT_VER" > "$B/trustforge/_version.py"
echo "[ec2] 版號 = $GIT_VER"
( cd "$B" && zip -qr "$ZIP" trustforge data demo scripts -x '*/__pycache__/*' ); rm -rf "$B"

aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null || \
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
aws s3 cp "$ZIP" "s3://$BUCKET/trustforge_app.zip" --region "$REGION" >/dev/null
echo "[ec2] 已上傳 s3://$BUCKET/trustforge_app.zip"

# 3) 既有實例？→ update-in-place（重用現有 EC2，不 run-instances）-----------
# EIP 已附著在 tag Name=trustforge-demo 的既有實例上；只要不 terminate 該實例，
# EIP 就會一直留在上面（stop/start 也保留附著，不用重綁）→ 查到就重用，不建新的。
# 「查詢失敗（憑證/throttle/網路）」與「真的無既有實例」必須分開處理：查詢失敗
# 一律中止，絕不能落到下面的建置流程，否則會建出重複實例（defeat 防 sprawl）。
# 注意：本腳本自己建議「停實例省 credit」，停止狀態很常見，所以要查「所有非
# terminated」的相符實例（不能只查 running，否則 stopped 的 production 會被
# 誤判成無實例 → 建重複 + 拋棄 EIP）。
if ! MATCHES=$(aws ec2 describe-instances --region "$REGION" \
  --filters Name=tag:Name,Values=trustforge-demo \
    Name=instance-state-name,Values=pending,running,shutting-down,stopping,stopped \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text); then
  echo "[ec2] ❌ 查詢既有實例失敗（describe-instances 非零結束），中止避免建重複實例" >&2
  exit 1
fi
MATCH_COUNT=$(printf '%s\n' "$MATCHES" | grep -c . || true)

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
  # 同時把 systemd 的 BEDROCK_MODEL_ID 重寫成本次的 $MODEL（離線部署就清空）——
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
  CMDID=$(aws ssm send-command --region "$REGION" --instance-ids "$IID" \
    --document-name AWS-RunShellScript --parameters commands='["set -e","cd /opt/trustforge","aws s3 cp s3://'"$BUCKET"'/trustforge_app.zip ./app.zip --region '"$REGION"'","unzip -o app.zip","sed -i \"s|^Environment=BEDROCK_MODEL_ID=.*|Environment=BEDROCK_MODEL_ID='"$MODEL"'|\" /etc/systemd/system/trustforge.service","if grep -q \"^Environment=CACHE_BACKEND=\" /etc/systemd/system/trustforge.service; then sed -i \"s|^Environment=CACHE_BACKEND=.*|Environment=CACHE_BACKEND=dynamodb|\" /etc/systemd/system/trustforge.service; else sed -i \"/^Environment=PYTHONPATH=/a Environment=CACHE_BACKEND=dynamodb\" /etc/systemd/system/trustforge.service; fi","if grep -q \"^Environment=TRUSTFORGE_CACHE_TABLE=\" /etc/systemd/system/trustforge.service; then sed -i \"s|^Environment=TRUSTFORGE_CACHE_TABLE=.*|Environment=TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache|\" /etc/systemd/system/trustforge.service; else sed -i \"/^Environment=PYTHONPATH=/a Environment=TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache\" /etc/systemd/system/trustforge.service; fi","if grep -q \"^Environment=TRUSTFORGE_COST_LEDGER_TABLE=\" /etc/systemd/system/trustforge.service; then sed -i \"s|^Environment=TRUSTFORGE_COST_LEDGER_TABLE=.*|Environment=TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger|\" /etc/systemd/system/trustforge.service; else sed -i \"/^Environment=PYTHONPATH=/a Environment=TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger\" /etc/systemd/system/trustforge.service; fi","if grep -q \"^Environment=COST_LEDGER_BACKEND=\" /etc/systemd/system/trustforge.service; then sed -i \"s|^Environment=COST_LEDGER_BACKEND=.*|Environment=COST_LEDGER_BACKEND=dynamodb|\" /etc/systemd/system/trustforge.service; else sed -i \"/^Environment=PYTHONPATH=/a Environment=COST_LEDGER_BACKEND=dynamodb\" /etc/systemd/system/trustforge.service; fi","cat > /etc/systemd/system/fetch-scheduler.service <<UNIT2","[Unit]","Description=TrustForge connector cache fetch scheduler","[Service]","Type=oneshot","WorkingDirectory=/opt/trustforge","Environment=AWS_REGION='"$REGION"'","Environment=PYTHONPATH=/opt/trustforge","Environment=CACHE_BACKEND=dynamodb","Environment=TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache","Environment=TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger","Environment=COST_LEDGER_BACKEND=dynamodb","ExecStart=/usr/bin/python3 scripts/fetch_scheduler.py","UNIT2","cat > /etc/systemd/system/fetch-scheduler.timer <<UNIT3","[Unit]","Description=Run TrustForge fetch scheduler periodically","[Timer]","OnBootSec=1min","OnUnitActiveSec=15min","[Install]","WantedBy=timers.target","UNIT3","systemctl daemon-reload","systemctl restart trustforge","systemctl enable --now fetch-scheduler.timer","for i in $(seq 1 12); do systemctl is-active --quiet trustforge && curl -fsS http://localhost/healthz >/dev/null 2>&1 && exit 0; sleep 3; done; echo \"[ec2] healthz 檢查失敗\"; journalctl -u trustforge -n 40 --no-pager; exit 1"]' \
    --query 'Command.CommandId' --output text)
  if [ -z "$CMDID" ] || [ "$CMDID" = "None" ]; then
    echo "[ec2] ❌ SSM send-command 未取得 CommandId，中止" >&2
    exit 1
  fi
  echo "[ec2] SSM CommandId=${CMDID}，等待遠端執行完成…"
  # send-command 只確認「已接受」是非同步的；用 wait 等實際跑完，再查真正的
  # 執行結果狀態（wait 在 Failed/Cancelled/TimedOut 時也會回非零，這裡不因此
  # 提早中止腳本，而是繼續往下查 Status 印出明確的成功/失敗）。
  aws ssm wait command-executed --region "$REGION" --command-id "$CMDID" --instance-id "$IID" 2>/dev/null || true
  SSM_STATUS=$(aws ssm get-command-invocation --region "$REGION" \
    --command-id "$CMDID" --instance-id "$IID" --query Status --output text)
  if [ "$SSM_STATUS" != "Success" ]; then
    echo "[ec2] ❌ update-in-place 失敗：SSM CommandId=$CMDID Status=$SSM_STATUS" >&2
    exit 1
  fi
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
ExecStart=/usr/bin/python3 -m trustforge.web
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
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
ExecStart=/usr/bin/python3 scripts/fetch_scheduler.py
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
IID=$(aws ec2 run-instances --region "$REGION" --image-id "$AMI" \
  --instance-type "$INSTANCE_TYPE" --iam-instance-profile Name=$ROLE \
  --security-group-ids "$SGID" --associate-public-ip-address \
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
