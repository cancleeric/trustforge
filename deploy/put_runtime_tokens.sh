#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# put_runtime_tokens.sh
#
# 用途：
#   給老闆 / CEO 一次性（或輪替時）手動執行，將 admin / live token 的明文值
#   寫入「常駐」SSM SecureString 參數：
#     - ${PREFIX}/admin-token
#     - ${PREFIX}/live-token
#   預設 PREFIX=/trustforge/runtime，可用環境變數 TRUSTFORGE_TOKEN_SSM_PREFIX
#   覆寫。參數帶 Overwrite: true，可重複執行以輪替更新值。
#
# 安全邊界：
#   - 本腳本本身「絕對不含」任何 token 值；值一律來自呼叫當下的 shell 環境
#     變數 TRUSTFORGE_ADMIN_TOKEN / TRUSTFORGE_LIVE_TOKEN。把值寫死在腳本裡
#     是嚴重違規。
#   - token 值透過 0600 暫存檔以 --cli-input-json file:// 方式傳給 aws-cli，
#     避免 token 出現在 argv / process list（避免 ps / /proc 洩漏）。
#   - 暫存檔用完即刪；本腳本不開啟 set -x 或任何 verbose/debug 輸出，
#     避免值外洩到終端 / log。輸出只含參數名稱，絕不印出 token 值。
#   - 字元集鐵則：非空值必須符合 ^[A-Za-z0-9._~-]+$（用 bash 原生 [[ =~ ]]，
#     不用 grep -q，因為 grep 逐行匹配對多行輸入有繞過漏洞）。
#   - 長度下限 ≥ 24 字元（比照 web.py _ADMIN_LIVE_TOKEN_MIN_LEN = 24）。
#   - 長度上限 ≤ 512 字元（比照 web.py _ADMIN_LIVE_TOKEN_MAX_LEN = 512，
#     與 PUT /api/admin/config 的 live_token 上限對稱一致）。
#   - 【啟動期凍結提醒】SSM / env bootstrap 分量是「啟動期凍結」的：
#     跑完本腳本後，admin token 需要重啟 TrustForge process 才會生效
#     （live token 的 SSM/env 分量也是啟動期凍結，但 config 層──
#     /admin 主控台──才是唯一的動態即時生效路徑）。請勿跑完腳本後
#     誤以為 admin/live token（SSM/env 這兩層）會立刻生效卻沒重啟。
#
# 與 deploy_ec2.sh 部署期臨時參數的差異（重要）：
#   - deploy_ec2.sh 管理的是 /trustforge/deploy/* 「部署期臨時參數」，
#     屬於一次性、部署結束會被 sweep / trap 清除的暫時性參數，且其
#     put_secure_param 不帶 --overwrite（撞名 fail-loud 中止）。
#   - 本腳本管理的是 /trustforge/runtime/* 「常駐參數」，給應用程式啟動時
#     讀取 admin / live token 用，**不會被任何 trap / sweep 邏輯自動刪除**，
#     並明確使用 Overwrite: true 以支援輪替覆寫。
#   - 【禁止事項】本腳本「刻意不包含任何 delete SSM 參數或 trap cleanup
#     邏輯」。常駐參數若被 trap 自動刪除，會導致線上服務啟動時讀不到 token
#     而整個掛掉。請勿從 deploy_ec2.sh 複製 trap cleanup 進來。這裡唯一
#     會清理的是「本機暫存檔」，不是 SSM 參數。
#
# 成本歸戶 / 盤點：
#   每個參數 put 完後另呼叫 add-tags-to-resource 打上 tag Key=app,
#   Value=trustforge。tag 失敗只印警告不中止（盤點用途，非安全關鍵）。
#
# 執行方式範例：
#   TRUSTFORGE_ADMIN_TOKEN=xxx TRUSTFORGE_LIVE_TOKEN=yyy \
#     ./deploy/put_runtime_tokens.sh
#   或只帶其中一個：
#   TRUSTFORGE_ADMIN_TOKEN=xxx ./deploy/put_runtime_tokens.sh
#   或輪替時覆寫（同樣指令重跑即可，因為 Overwrite: true）：
#   TRUSTFORGE_LIVE_TOKEN=newyyy ./deploy/put_runtime_tokens.sh
# ============================================================================

REGION="${REGION:-ap-southeast-2}"
PREFIX="${TRUSTFORGE_TOKEN_SSM_PREFIX:-/trustforge/runtime}"
# 去掉尾端斜線，避免使用者帶尾斜線（如 /trustforge/runtime/）時
# 組出雙斜線參數名（//trustforge/runtime//admin-token）。
PREFIX="${PREFIX%/}"

# token 值一律來自呼叫當下的 env，不寫死、不讀檔。
ADMIN_TOKEN="${TRUSTFORGE_ADMIN_TOKEN-}"
LIVE_TOKEN="${TRUSTFORGE_LIVE_TOKEN-}"
# 選用：客戶自管 KMS key ARN/alias。指定後 SSM 以該 key 加密 SecureString，
# 並自動套用 EncryptionContext（aws:ssm:parameter-arn），由 key policy 收斂
# 解密權限（#121：kms EncryptionContext 強化）。未設則用 AWS 託管預設 key。
KMS_KEY_ID="${TRUSTFORGE_TOKEN_KMS_KEY_ID-}"

# 兩者至少要有一個非空，否則沒事可做。
if [[ -z "$ADMIN_TOKEN" && -z "$LIVE_TOKEN" ]]; then
  echo "錯誤：TRUSTFORGE_ADMIN_TOKEN 與 TRUSTFORGE_LIVE_TOKEN 兩個都沒帶，沒事可做。" >&2
  exit 1
fi

# ----------------------------------------------------------------------------
# put_runtime_secure_param <pname> <val>
#
# 沿用 deploy_ec2.sh put_secure_param 的安全手法：
#   - umask 077 + mktemp 建 0600 暫存檔
#   - JSON 寫進暫存檔，用 --cli-input-json file:// 傳給 aws
#     （避免 token 出現在 argv / process list）
#   - 用完即刪暫存檔；另加 trap 作為中斷時（SIGINT / SIGTERM）的最後防線
#
# 差異：本函式 JSON body 含 "Overwrite": true，支援常駐參數輪替覆寫。
#
# 【再次強調】本函式的 trap 只清理「本機暫存檔」，不刪除 SSM 參數。
#            常駐參數絕對不能被自動刪除。
# ----------------------------------------------------------------------------
put_runtime_secure_param() {
  local pname="$1"
  local val="$2"

  # 字元集驗證：只允許 [A-Za-z0-9._~-]。
  # 用 bash 原生 [[ =~ ]]，不用 grep -q（grep 逐行匹配對多行輸入有繞過漏洞）。
  if [[ ! "$val" =~ ^[A-Za-z0-9._~-]+$ ]]; then
    echo "錯誤：參數 $pname 的值含不允許的字元（僅允許 A-Z a-z 0-9 . _ ~ -）。未執行 put。" >&2
    return 1
  fi

  # 長度下限 ≥ 24 字元。
  if [[ ${#val} -lt 24 ]]; then
    echo "錯誤：參數 $pname 的值長度不足（最少 24 字元，目前 ${#val}）。未執行 put。" >&2
    return 1
  fi

  # 長度上限 ≤ 512 字元（比照 web.py _ADMIN_LIVE_TOKEN_MAX_LEN = 512）。
  if [[ ${#val} -gt 512 ]]; then
    echo "錯誤：參數 $pname 的值長度超過上限（最多 512 字元，目前 ${#val}）。未執行 put。" >&2
    return 1
  fi

  # 建 0600 暫存檔，寫 JSON body（含 Overwrite: true）。指定 KMS key 時一併
  # 帶入 KeyId（啟用客戶自管 KMS key + EncryptionContext 收斂解密權限）。
  # 【#121.8 JSON 注入修復】絕不再用 printf 裸插 token / KMS_KEY_ID 進 JSON——
  # 任何含引號 / 反斜線 / 控制字元的輸入都會破壞 JSON 結構（即便本腳本已在前面
  # 用字元集正則擋掉非法 token，KMS_KEY_ID 是 ARN/alias 不在該正則範圍內，仍
  # 可能被注入）。改用 python（值經環境變數傳入，不用 shell 插值）做 json.dumps
  # 正確轉義，產出語法合法的 JSON。
  local tmp
  tmp="$(umask 077 && mktemp)"
  trap 'rm -f "${tmp:-}"' EXIT INT TERM

  TF_PARAM_NAME="$pname" TF_PARAM_VAL="$val" TF_PARAM_KEYID="$KMS_KEY_ID" \
    python3 - <<'PY' > "$tmp" || { rm -f "$tmp"; echo "錯誤：組裝 JSON body 失敗：$pname" >&2; return 1; }
import json, os, sys
doc = {
    "Name": os.environ["TF_PARAM_NAME"],
    "Value": os.environ["TF_PARAM_VAL"],
    "Type": "SecureString",
    "Overwrite": True,
}
keyid = os.environ.get("TF_PARAM_KEYID")
if keyid:
    doc["KeyId"] = keyid
sys.stdout.write(json.dumps(doc))
PY

  # put 參數（不開 verbose，stdout/stderr 不含 token 值）。
  if ! aws ssm put-parameter --region "$REGION" --cli-input-json "file://$tmp" >/dev/null; then
    rm -f "$tmp"
    echo "錯誤：put-parameter 失敗：$pname" >&2
    return 1
  fi
  rm -f "$tmp"

  # 打 tag（成本歸戶 / 盤點）。tag 失敗只警告不中止——參數已成功 put，
  # tag 不是安全關鍵。用 if ! 包起來避免 set -e 中止整個腳本。
  if ! aws ssm add-tags-to-resource --region "$REGION" \
        --resource-type "Parameter" \
        --resource-id "$pname" \
        --tags "Key=app,Value=trustforge" >/dev/null 2>&1; then
    echo "警告：add-tags-to-resource 失敗：${pname}（tag 僅盤點用途，不中止；參數已成功 put）。" >&2
  fi

  # 只印參數名稱，絕不印 token 值。
  echo "已 put 常駐 SecureString 參數：${pname}（region=${REGION}, overwrite=true, tag=app:trustforge）"
}

# 依序處理有值的 token（admin / live 可能只帶一個）。
if [[ -n "$ADMIN_TOKEN" ]]; then
  put_runtime_secure_param "${PREFIX}/admin-token" "$ADMIN_TOKEN"
fi

if [[ -n "$LIVE_TOKEN" ]]; then
  put_runtime_secure_param "${PREFIX}/live-token" "$LIVE_TOKEN"
fi

echo "完成。"
echo "提醒：這些是常駐參數（/trustforge/runtime/*），不會被部署腳本的 trap / sweep 清除。若需輪替，重跑本腳本即可（Overwrite: true）。"

# #121.9：客戶自管 KMS key 的 key policy 收斂提醒（與 ssm_params.py 讀取端共用
# 同一套 EncryptionContext 語意；runtime token 由 app 啟動期經 SSM 讀取）。
if [[ -n "$KMS_KEY_ID" ]]; then
  ACCT="${ACCT:-$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo '<acct>')}"
  echo "──────────────────────────────────────────────────────────────────────"
  echo "已用客戶自管 KMS key 加密 SecureString：$KMS_KEY_ID"
  echo "請確認該 CMK 的 key policy 允許本角色 kms:Decrypt，並（建議）以 condition"
  echo "收斂解密權限——SSM 寫入 SecureString 時會自動帶入 EncryptionContext"
  echo "aws:ssm:parameter-arn，收斂後只有「經 SSM 且目標為該前綴參數」的解密才被放行："
  echo '  "Condition": { "StringEquals": {'
  echo '    "kms:EncryptionContext:aws:ssm:parameter-arn":'
  echo "    \"arn:aws:ssm:${REGION}:${ACCT}:parameter/trustforge/runtime/*\" } }"
  echo "（直接用 KMS API 解任意東西都不符此 condition，被拒。詳見 deploy/README.md"
  echo "「#121 runtime token KMS EncryptionContext 收斂」章節。）"
  echo "──────────────────────────────────────────────────────────────────────"
fi
