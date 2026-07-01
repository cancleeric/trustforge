#!/usr/bin/env bash
# TrustForge → EC2 公開部署（純 AWS CLI，冪等）。
# 給「真正跑在 AWS、有公開網址、不靠筆電」的 Live Demo，並完成 EC2 領 $20 credit。
# 最小權限：instance role 只有 bedrock:InvokeModel + S3 讀(自家 bucket) + SSM。
# 無 SSH key pair（走 SSM Session Manager）。
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

# 2) IAM 角色 + instance profile（最小權限）----------------------------------
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

# 3) Security group（開 80 公開）---------------------------------------------
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

# 4) user-data（開機自動裝 + 跑）---------------------------------------------
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

# 5) AMI + 啟動實例 -----------------------------------------------------------
AMI=$(aws ssm get-parameter --region "$REGION" \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query Parameter.Value --output text)
echo "[ec2] AMI=$AMI 啟動 ${INSTANCE_TYPE}…"
IID=$(aws ec2 run-instances --region "$REGION" --image-id "$AMI" \
  --instance-type "$INSTANCE_TYPE" --iam-instance-profile Name=$ROLE \
  --security-group-ids "$SGID" --associate-public-ip-address \
  --user-data "file://$UD" \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=trustforge-demo}]' \
  --query 'Instances[0].InstanceId' --output text)
rm -f "$UD"
echo "[ec2] 實例 $IID 啟動中…"
aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"
IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "[ec2] ✅ 實例上線：$IID  公開 IP：$IP"
echo "[ec2] 🔗 Live Demo（開機裝好約 1-2 分後）：http://$IP/"
echo "[ec2]   健康檢查：http://$IP/healthz"
echo "[ec2] 停用省 credit：aws ec2 stop-instances --instance-ids $IID --region $REGION"
