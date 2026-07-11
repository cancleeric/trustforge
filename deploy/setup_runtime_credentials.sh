#!/usr/bin/env bash
# ============================================================================
# setup_runtime_credentials.sh  (#121 systemd credentials 整合 glue)
#
# 用途：
#   在 TrustForge 啟動前由獨立 oneshot unit（trustforge-credentials.service，
#   Before=trustforge.service）呼叫，把 SSM Parameter Store 的 SecureString
#   runtime token（admin-token / live-token）以 0600 寫入 **tmpfs 憑證目錄**
#   （預設 /run/trustforge-credentials，非持久磁碟、不在 argv、不在 user-data），
#   檔名為 `trustforge-<name>`。該檔隨後由 trustforge.service 的
#   `LoadCredential=trustforge-<name>:<cred_dir>/trustforge-<name>` 載入，
#   systemd 把內容暴露於 `$CREDENTIALS_DIRECTORY/trustforge-<name>`，app 端經
#   該路徑讀取（見 src/trustforge/ssm_params.py::get_runtime_token 與 web.py /
#   admin_config.py 的 SSM-env 相容層）。
#
#   注意：檔名必須是 `trustforge-<name>`（帶前綴），與 app 讀取層
#   `$CREDENTIALS_DIRECTORY/trustforge-<name>` 及 `LoadCredential=` 的
#   credential 名稱一致——三者缺一提早對齊，否則 app 讀不到 token。
#
# 這讓 runtime token 全程不落持久磁碟、不進 process list / argv，且開機期
# 由 SSM 動態取得（輪替後重啟即生效）。對應 Python 端產生 `LoadCredential=`
# 行的函式：`src/trustforge/ssm_params.py::runtime_token_load_credential_line`。
#
# 安全邊界：
#   - 未設 `TRUSTFORGE_TOKEN_SSM_PREFIX` → 直接 no-op 退出（離線 demo 不依賴
#     AWS，也不該建立任何憑證檔）。
#   - 憑證目錄建 0700、檔案建 0600（僅 root 可讀），符合 harper CISO L-4。
#   - token 值來自 `aws ssm get-parameter --with-decryption --query
#     Parameter.Value --output text`，不經任何中間變數印到 log；寫入失敗時
#     不留下空檔（fail-closed：刪除半成品），並以非 0 退出讓 systemd 標記
#     unit 啟動失敗（不放行一個「讀不到 token」的服務悄悄跑）。
#   - 指定 `TRUSTFORGE_TOKEN_KMS_KEY_ID` 時，get-parameter 仍走預設解密
#     （EncryptionContext 由 SSM + 客戶自管 KMS key policy 收斂），本腳本不
#     另帶 key 參數。
#
# 在 deploy_ec2.sh 的 trustforge.service unit 中啟用（當 TRUSTFORGE_TOKEN_SSM_
# PREFIX 有值時）：
#   [Service]
#   LoadCredential=trustforge-admin-token:/run/trustforge-credentials/trustforge-admin-token
#   LoadCredential=trustforge-live-token:/run/trustforge-credentials/trustforge-live-token
#   Environment=TRUSTFORGE_TOKEN_SSM_PREFIX=/trustforge/runtime
#
#   # 憑證檔由獨立 oneshot unit trustforge-credentials.service（Before=trustforge.service）
#   # 在 trustforge.service 啟動「前」產生，故 LoadCredential 載入時檔案已存在。
#
# 執行（通常由 systemd 自動呼叫，亦可手動演練）：
#   TRUSTFORGE_TOKEN_SSM_PREFIX=/trustforge/runtime \
#     ./deploy/setup_runtime_credentials.sh
# ============================================================================
set -euo pipefail

PREFIX="${TRUSTFORGE_TOKEN_SSM_PREFIX:-}"
CRED_DIR="${TRUSTFORGE_CRED_DIR:-/run/trustforge-credentials}"
REGION="${REGION:-ap-southeast-2}"

# 未設前綴 → 離線 demo / 非 SSM 部署，no-op。
if [[ -z "$PREFIX" ]]; then
  exit 0
fi

# tmpfs 憑證目錄（/run 本身即 tmpfs，重啟即失，非持久磁碟）。
install -d -m 0700 "$CRED_DIR"

for name in admin-token live-token; do
  target="$CRED_DIR/trustforge-$name"
  tmp="$(umask 077 && mktemp)"
  # trap 最後防線：寫入失敗也要清掉半成品暫存檔，不留空/半截 token 檔。
  trap 'rm -f "${tmp:-}"' EXIT INT TERM

  if ! aws ssm get-parameter \
        --region "$REGION" \
        --name "$PREFIX/$name" \
        --with-decryption \
        --query Parameter.Value \
        --output text > "$tmp" 2>/dev/null; then
    echo "錯誤：從 SSM 讀取 $PREFIX/$name 失敗，不放行服務啟動。" >&2
    rm -f "$tmp"
    exit 1
  fi

  # 空值視同失敗（不寫空 token 檔）。
  if [[ ! -s "$tmp" ]]; then
    echo "錯誤：$PREFIX/$name 為空值，不放行服務啟動。" >&2
    rm -f "$tmp"
    exit 1
  fi

  # 原子替換為 0600 憑證檔（檔名帶 trustforge- 前綴，對齊 app 讀取層 / LoadCredential）。
  chmod 0600 "$tmp"
  mv -f "$tmp" "$target"
  echo "已寫入 tmpfs 憑證：$target（0600）"
done

echo "完成：runtime token 已就緒於 tmpfs（$CRED_DIR）。"
