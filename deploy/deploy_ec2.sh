#!/usr/bin/env bash
# TrustForge → EC2 公開部署（純 AWS CLI，冪等）。
# 給「真正跑在 AWS、有公開網址、不靠筆電」的 Live Demo，並完成 EC2 領 $20 credit。
# 最小權限：instance role 只有 bedrock:InvokeModel + S3 讀(自家 bucket) + SSM。
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

# 1) 打包 + 上傳 S3 -----------------------------------------------------------
echo "[ec2] 打包應用 zip…"
B=$(mktemp -d); ZIP="$(pwd)/build/trustforge_app.zip"; mkdir -p build
cp -r src/trustforge "$B/trustforge"; cp -r data "$B/data"; cp -r demo "$B/demo"
GIT_VER=$(git describe --tags --always --dirty 2>/dev/null || echo dev)
printf 'VERSION = "%s"\n' "$GIT_VER" > "$B/trustforge/_version.py"
echo "[ec2] 版號 = $GIT_VER"
( cd "$B" && zip -qr "$ZIP" trustforge data demo -x '*/__pycache__/*' ); rm -rf "$B"

aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null || \
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
aws s3 cp "$ZIP" "s3://$BUCKET/trustforge_app.zip" --region "$REGION" >/dev/null
echo "[ec2] 已上傳 s3://$BUCKET/trustforge_app.zip"

# 2) 既有實例？→ update-in-place（重用現有 EC2，不 run-instances）-----------
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
  CMDID=$(aws ssm send-command --region "$REGION" --instance-ids "$IID" \
    --document-name AWS-RunShellScript --parameters commands='["set -e","cd /opt/trustforge","aws s3 cp s3://'"$BUCKET"'/trustforge_app.zip ./app.zip --region '"$REGION"'","unzip -o app.zip","sed -i \"s|^Environment=BEDROCK_MODEL_ID=.*|Environment=BEDROCK_MODEL_ID='"$MODEL"'|\" /etc/systemd/system/trustforge.service","systemctl daemon-reload","systemctl restart trustforge","for i in $(seq 1 12); do systemctl is-active --quiet trustforge && curl -fsS http://localhost/healthz >/dev/null 2>&1 && exit 0; sleep 3; done; echo \"[ec2] healthz 檢查失敗\"; journalctl -u trustforge -n 40 --no-pager; exit 1"]' \
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
  IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
  echo "[ec2] ✅ 既有實例已更新：$IID  公開 IP：${IP}（EIP 未動，模型=${MODEL:-<離線>}）"
  echo "[ec2] 🔗 Live Demo：http://$IP/"
  echo "[ec2]   健康檢查：http://$IP/healthz"
  exit 0
fi
echo "[ec2] 無既有實例（tag Name=trustforge-demo，非 terminated）→ 首次建置流程"

# 3) IAM 角色 + instance profile（最小權限）----------------------------------
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
ExecStart=/usr/bin/python3 -m trustforge.web
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now trustforge >>/var/log/tf-setup.log 2>&1
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
IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "[ec2] ✅ 實例上線：$IID  公開 IP：$IP"
echo "[ec2] 🔗 Live Demo（開機裝好約 1-2 分後）：http://$IP/"
echo "[ec2]   健康檢查：http://$IP/healthz"
echo "[ec2] 停用省 credit：aws ec2 stop-instances --instance-ids $IID --region $REGION"
