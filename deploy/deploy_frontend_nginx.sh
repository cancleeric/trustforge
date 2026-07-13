#!/usr/bin/env bash
# TrustForge → 前後端分離 Phase 3（task #28）：在既有 EC2 實例（由
# deploy/deploy_ec2.sh 建立）上「加一層 nginx」，把 python 收斂成只對內
# 監聽（127.0.0.1:8080），並把 React 前端 build 好的靜態檔案上傳備用。
#
# ⛔ 這支腳本**不會**把使用者導向 React 前端——安裝完之後，nginx 預設啟用
# `deploy/nginx-legacy.conf`（bootstrap：純 HTTP，全部原樣轉發給 python），
# 功能上跟「沒有 nginx、python 直接對外聽 :80」逐字等價，只是多一層 nginx
# （TLS 就緒、單一對外入口，見 deploy/TLS-SETUP.md）。
#
# 真正切到 React 前端拓樸（cutover）是**另一個獨立、需要 CEO+CISO+CPO
# 三審+老闆簽核才能執行**的步驟，見 deploy/cutover_switch.sh。這支腳本只
# 負責「把拓樸架好、隨時可以切、但預設不切」。
#
# 前置：deploy/deploy_ec2.sh 已跑過至少一次（有一個 tag Name=trustforge-demo
# 的既有實例）。本腳本用 SSM Session Manager 對它下指令，不需要 SSH key。
#
# 可調環境變數：REGION（預設同 deploy_ec2.sh，ap-southeast-2）
set -euo pipefail
cd "$(dirname "$0")/.."

REGION="${REGION:-ap-southeast-2}"
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="trustforge-deploy-${ACCT}"
SG=trustforge-ec2-sg

# 這幾行狀態訊息刻意都印到 stderr（不是 stdout）：`TF_BOOTSTRAP_DRY_RUN=1`
# 逃生口是用 `$(...)` 擷取這支腳本的 stdout 當成組好的遠端指令內容（見下面
# CMDS 那段），如果進度訊息混進 stdout 會污染擷取結果（比照 deploy/cutover_switch.sh
# 同一個設計）。
echo "[fe-nginx] region=$REGION account=$ACCT bucket=$BUCKET" >&2

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

# 1) 找既有實例（不建新的；沒有就叫人先跑 deploy_ec2.sh）------------------
if ! MATCHES=$(aws ec2 describe-instances --region "$REGION" \
  --filters Name=tag:Name,Values=trustforge-demo \
    Name=instance-state-name,Values=running,stopped \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text); then
  echo "[fe-nginx] ❌ 查詢既有實例失敗，中止" >&2
  exit 1
fi
MATCH_COUNT=$(printf '%s\n' "$MATCHES" | grep -c . || true)
if [ "$MATCH_COUNT" -ne 1 ]; then
  echo "[fe-nginx] ❌ 找到 $MATCH_COUNT 個相符實例（tag Name=trustforge-demo，running/stopped），需要剛好 1 個。" >&2
  echo "[fe-nginx]    請先跑 deploy/deploy_ec2.sh 建好基礎 EC2，再跑本腳本疊加 nginx 層。" >&2
  exit 1
fi
IID=$(printf '%s\n' "$MATCHES" | awk '{print $1}')
STATE=$(printf '%s\n' "$MATCHES" | awk '{print $2}')
# codex 複審 HIGH：TF_BOOTSTRAP_DRY_RUN=1 原本只短路最後的 `aws ssm
# send-command`，前面這些真的會 start-instances/npm build/上傳 S3/改
# security group——真部署誤呼叫 dry-run 卻沒帶 mock aws 時，會真的動到
# production（已實測發生過一次：S3 dist/conf 被真的覆蓋、SG authorize
# 打了一次 no-op）。以下每一段「會真的 mutate AWS 或跑真的本機
# build/上傳」的步驟都加 dry-run 短路：dry-run 只需要 IID/BUCKET/REGION
# 這些純文字組出 CMDS 內容，不需要真的開機/build/上傳/開 port，所以即使
# 沒有 mock aws 在 PATH 上，dry-run 也不會碰到任何真的寫入操作（唯讀查詢
# 如上面的 describe-instances 仍會打真 AWS，但唯讀不會造成 mutation）。
if [ "$STATE" = "stopped" ]; then
  if [ "${TF_BOOTSTRAP_DRY_RUN:-}" = "1" ]; then
    echo "[fe-nginx]（dry-run）略過真的開機（aws ec2 start-instances/wait instance-running），只組遠端指令內容" >&2
  else
    echo "[fe-nginx] 既有實例 $IID 已停機 → 先開機" >&2
    aws ec2 start-instances --region "$REGION" --instance-ids "$IID" >/dev/null
    aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"
  fi
fi
echo "[fe-nginx] 目標實例 $IID" >&2

# 2) 本機 build 前端 + 3) 打包上傳：前端 dist + 四份 nginx conf（直接用
#    repo 裡實際被 `nginx -t` 驗證過的檔案，避免跟 SSM 內嵌字串產生
#    drift）----------------------------------------------------------------
#    legacy=SSR 全轉發（HTTP-only）、react=React+TLS（有 domain 才能用）、
#    react-http=React HTTP-only（bare-IP 現況用，見 deploy/nginx-react-http.conf）、
#    legacy-tls=SSR 全轉發（443 HTTPS，保留 HSTS）——codex 複審 HIGH：react→
#    legacy 緊急回滾時若憑證已存在，cutover_switch.sh 會改選這份，避免
#    HSTS 破壞 HTTP-only 回滾（見 deploy/nginx-legacy-tls.conf 檔頭註解）。
#    dry-run 不需要真的 build/zip/上傳（CMDS 只嵌 __BUCKET__/__REGION__
#    這種純文字佔位，不依賴本機 dist 是否存在），全部略過。
if [ "${TF_BOOTSTRAP_DRY_RUN:-}" = "1" ]; then
  echo "[fe-nginx]（dry-run）略過本機 npm build/zip 打包與真的 aws s3 cp 上傳，只組遠端指令內容，不寫入真的 S3 bucket" >&2
else
  echo "[fe-nginx] build 前端（npm ci && npm run build）…" >&2
  (
    cd frontend
    export VITE_GIT_SHA="${VITE_GIT_SHA:-$(git rev-parse --short HEAD)}"
    export VITE_RELEASE_VERSION="${VITE_RELEASE_VERSION:-v$(sed -n 's/^version = "\(.*\)"/\1/p' ../pyproject.toml)}"
    npm ci && npm run build
  )
  if [ ! -d frontend/dist ]; then
    echo "[fe-nginx] ❌ frontend/dist 不存在，build 失敗" >&2
    exit 1
  fi
  DIST_ZIP="$(pwd)/build/trustforge_frontend_dist.zip"
  mkdir -p build
  ( cd frontend/dist && zip -qr "$DIST_ZIP" . )
  aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null || \
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  aws s3 cp "$DIST_ZIP" "s3://$BUCKET/trustforge_frontend_dist.zip" --region "$REGION" >/dev/null
  aws s3 cp deploy/nginx-legacy.conf "s3://$BUCKET/nginx-legacy.conf" --region "$REGION" >/dev/null
  aws s3 cp deploy/nginx.conf "s3://$BUCKET/nginx-react.conf" --region "$REGION" >/dev/null
  aws s3 cp deploy/nginx-react-http.conf "s3://$BUCKET/nginx-react-http.conf" --region "$REGION" >/dev/null
  aws s3 cp deploy/nginx-legacy-tls.conf "s3://$BUCKET/nginx-legacy-tls.conf" --region "$REGION" >/dev/null
  echo "[fe-nginx] 已上傳前端 dist + 四份 nginx conf 到 s3://$BUCKET/" >&2
fi

# 4) Security group：加開 443（80 應該已由 deploy_ec2.sh 開好）-----------
#    VPC/SGID 查詢是唯讀，dry-run 也保留（純文字資訊，CMDS 不需要它）；
#    真的 authorize-security-group-ingress 是 mutating，dry-run 略過。
VPC=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SGID=$(aws ec2 describe-security-groups --region "$REGION" --filters Name=group-name,Values=$SG Name=vpc-id,Values=$VPC --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo None)
if [ "$SGID" = "None" ] || [ -z "$SGID" ]; then
  echo "[fe-nginx] ❌ 找不到 security group ${SG}，deploy_ec2.sh 應該已經建過，中止" >&2
  exit 1
fi
if [ "${TF_BOOTSTRAP_DRY_RUN:-}" = "1" ]; then
  echo "[fe-nginx]（dry-run）略過真的 authorize-security-group-ingress，只組遠端指令內容，不改真的 SG 規則" >&2
else
  if ! aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SGID" \
    --protocol tcp --port 443 --cidr 0.0.0.0/0 >/dev/null 2>&1; then
    echo "[fe-nginx] 443 規則已存在（authorize 回錯通常代表 duplicate，忽略）" >&2
  fi
  echo "[fe-nginx] SG=$SGID 已開 443（TLS 就緒，見 deploy/TLS-SETUP.md）" >&2
fi

# 5) SSM：裝 nginx、佈署 conf（預設啟用 legacy）、前端 dist、收斂 python 綁定
#    ----------------------------------------------------------------------
# 設計（guarded transaction，比照 deploy/cutover_switch.sh，codex 五次複審
# HIGH：以前的順序是「改 live symlink+systemd service → restart python 綁
# 127.0.0.1 → 之後才驗證/啟用 nginx」，任一步失敗都沒有回滾——python 已經
# 只聽 localhost（外部連不到）、nginx 又還沒真的起來，就是真 outage，而且
# 不會自己好）：
#   1. 先把 nginx 套件裝好、React dist/兩份候選 conf 佈署到 staging 位置
#      （/etc/nginx/trustforge-sites/、/opt/trustforge/frontend/dist）——
#      這段完全不動目前在跑的 python/nginx，失敗也不需要回滾。
#   2. 候選設定驗證：用 scratch nginx harness 對 legacy.conf 跑 `nginx -t`，
#      完全不碰 live 的 conf.d/trustforge.conf。驗證失敗直接中止，一律
#      exit 1（還沒開始 mutation，不需要也不會觸發回滾）。
#   3. 記錄跑前狀態（trustforge.service 備份、live symlink 是否存在＋
#      指向誰、default.conf 是否存在＋內容、nginx/trustforge 目前的
#      active/enabled 狀態）——之後才開始動 live 狀態，掛 ERR trap。
#   4. **收斂 python 綁定（narrow exposure）+ 起 nginx**：因為 nginx（80）
#      跟目前 python（也是 80）搶同一個 port，物理上不可能讓 nginx 先
#      綁好 80 才收窄 python——這段唯一可行、且已經做到的順序保證是：
#      candidate config 100% 通過 `nginx -t` 驗證＋跑前狀態已完整快照，
#      都在**這段唯一會動 live 狀態的區段之前**完成；這段本身（narrow
#      python bind → 起 nginx → post-switch 驗證）整段掛在同一個 ERR
#      trap 下，任一步（含 public path healthz 驗證失敗）都會觸發下面
#      的 ROLLBACK，把 trustforge.service／live symlink／default.conf／
#      nginx 服務狀態全部還原成跑前的樣子，不會停在「python 已收窄、
#      nginx 沒起來」的半殘 outage 狀態。
#      ⛔ **issue #69（冪等性）**：live symlink 若在跑前就已經指到別份 conf
#      （react.conf／react-http.conf／legacy-tls.conf，代表已經 cutover
#      過），本步驟**不會**重指 symlink、不改 CSP mode——只用 Step 1 剛
#      更新過的 dist/candidate conf 內容驗證+reload 現行拓樸，避免「單純
#      重跑本腳本更新前端 dist」把 React/TLS 拓樸打回 legacy（見 Step 1.5
#      的偵測邏輯）。只有跑前 live symlink 不存在（初次 bootstrap）才會
#      走這裡描述的完整流程。
#   5. ROLLBACK 比照 cutover_switch.sh：每一步都不用 `|| true` 吞掉失敗，
#      全部跑完才誠實印「已回滾」；回滾本身也失敗 → 印 distinct 的
#      `ROLLBACK-FAILED`（exit 97，跟一般失敗 exit 1 不同）+ 具體手動
#      復原指示，不謊報成功。ROLLBACK 本身不分岔（不管 Step 4 走了哪條
#      分支，都是同一套 PREV_* 快照 + 同一套還原邏輯）。
#
# TF_BOOTSTRAP_DRY_RUN=1：測試用逃生口，只印出組好的遠端指令內容、不真的
# 呼叫 `aws ssm send-command`。給 deploy/test_deploy_frontend_nginx_transaction.sh
# 擷取這段內容後在本機沙箱（假 /etc + mock nginx/systemctl/dnf/unzip/curl）
# 實際執行，驗證 guarded transaction／失敗回滾的控制流程真的正確。生產
# 環境不會設這個變數，行為不受影響。
#
# codex 複審 HIGH：dry-run 現在是「真 dry-run」——上面 1)~4) 每一段會真的
# mutate AWS（start-instances、npm build+zip、S3 head/create-bucket+cp、
# authorize-security-group-ingress）都已經個別加了 dry-run 短路，dry-run
# 時完全不執行。即使**沒有**在 PATH 上放 mock aws，dry-run 也只會打幾個
# 唯讀查詢（sts get-caller-identity/ec2 describe-instances/describe-vpcs/
# describe-security-groups，純讀取不 mutate），不會寫入任何真的 S3
# object、不會開真的 EC2 機、不會改真的 security group 規則、不會呼叫
# 真的 SSM send-command。驗證腳本改動時仍建議搭配 mock aws（更快、更
# 決定性、不依賴真的 AWS 帳號存在），但即使忘記 mock，dry-run 本身也不
# 會造成真的部署副作用（2026-07 曾發生一次忘記 mock 直接跑 dry-run，
# 因為當時 dry-run 只短路 SSM 這一段，S3 upload/SG authorize 仍照跑，
# 已修正）。
CMDS=$(cat <<'CMDEOF'
set -e

# ---- Step 0：host-wide 交易鎖（issue #74，deploy 並行防禦）──────────────
# 這支 bootstrap 跟 deploy/cutover_switch.sh 都會在**同一台 EC2 host** 上動
# live 的 nginx symlink／systemd service／conf 檔——兩者並行（或兩個 bootstrap
# 並行）就會 race、互相破壞彼此的 guarded transaction 中間狀態（symlink 指到
# 一半、service file 被兩邊 sed 交錯改）。比照 cutover_switch.sh 的 Step 0
# host-wide flock：在候選驗證／記錄跑前狀態／任何 mutation **之前**先搶一個
# host-wide 的 `flock -n`，一路持有到腳本結束（成功收尾或 ROLLBACK 完成）才
# 靠 process 結束自動釋放 fd。拿不到鎖就直接中止，零 mutation、exit 98。
#
# ⚠️ lock 路徑刻意跟 cutover_switch.sh **共用同一個**（同一 env var
# TF_CUTOVER_LOCK、同一預設 /var/lock/tf-cutover.lock）——這正是 #74 的重點：
# 唯有 deploy 跟 cutover 搶同一把鎖，兩者在同 host 才真的互斥。若各用各的 lock
# 檔，flock 形同虛設、race 依舊。生產環境不設 TF_CUTOVER_LOCK，兩支腳本都用
# /var/lock/tf-cutover.lock（行為一致）；沙箱測試把兩者一起覆寫成 sandbox 內
# 路徑即可。
LOCKFILE="${TF_CUTOVER_LOCK:-/var/lock/tf-cutover.lock}"
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  echo "❌ [fe-nginx] 另一個 deploy/cutover 正在進行中（拿不到 host-wide lock：${LOCKFILE}），本次不做任何變更，直接中止。" >&2
  exit 98
fi

ETC="${TF_BOOTSTRAP_ETC:-/etc}"
OPT_DIR="${TF_BOOTSTRAP_OPT:-/opt/trustforge}"
LIVE_LINK="$ETC/nginx/conf.d/trustforge.conf"
DEFAULT_CONF="$ETC/nginx/conf.d/default.conf"
CONF_DIR="$ETC/nginx/trustforge-sites"
CANDIDATE="$CONF_DIR/legacy.conf"
# codex 複審 HIGH（conf 原子化，同 dist）：這 4 份是每次都會重新佈署的
# candidate conf，全部走「先落地 versioned staging 驗證，Step 4 guarded
# transaction 內才 atomic replace 進 live 路徑」──不再是 Step 1 直接
# 覆寫 live 檔案。
CONF_FILES=(legacy.conf react.conf react-http.conf legacy-tls.conf)
REACT_TLS_DOMAIN="trustforge.hurricanesoft.com.tw"
SERVICE_FILE="$ETC/systemd/system/trustforge.service"
BACKUP_SERVICE_FILE="/tmp/tf-bootstrap-service.bak.$$"
BACKUP_DEFAULT_CONF="/tmp/tf-bootstrap-default-conf.bak.$$"
BACKUP_CONF_DIR="/tmp/tf-bootstrap-conf-backup.$$"
DNF_LOG="${TF_BOOTSTRAP_DNF_LOG:-/var/log/tf-nginx-setup.log}"

# ---- Step 1：安裝 nginx + 佈署靜態檔/candidate conf 到 staging 位置
#      （全部非破壞性，不動目前在跑的 python/nginx，失敗不用回滾）----
# codex 複審 HIGH（真部署踩到過一次）：以前這裡直接
# `rm -rf "$OPT_DIR/frontend/dist"` 砍掉「現在活著、nginx 正在 serve」的
# 那份 dist，才開始下載/解壓新版——這段完全在 Step 3 快照/ERR trap 保護
# 之外：下載/解壓中間 real user 打進來就是缺檔；下載/解壓本身失敗（網路
# 中斷、zip 壞掉）active 站直接壞掉、且沒有任何 rollback 機制救得回來
# （舊 ROLLBACK() 只備份過 SERVICE_FILE/DEFAULT_CONF，從沒備份過 dist 內
# 容）。改法：下載/解壓到全新的 versioned release 目錄
# frontend/releases/<ts>-<pid>/，完全不碰現在活著的 frontend/current
# symlink（也就完全不碰目前 nginx root 指到的內容）——這裡就算失敗，也
# 只是留下一個半成品 release 目錄被 abandon，live 站毫髮無傷、無需
# rollback（跟 Step 1 其他步驟失敗一樣安全）。真正的 atomic 切換（把
# frontend/current 這個 symlink 指到新 release）留到 Step 4，在 ERR trap
# 保護下才做，失敗可回滾；前一版 release 目錄刻意保留不砍，讓 rollback
# 切得回去、資產內容真的能還原。
#
# codex 複審 HIGH（conf 原子化，同 dist）：以前 4 份 candidate conf
# （legacy/react/react-http/legacy-tls.conf）是直接 `aws s3 cp` 覆寫到
# `$ETC/nginx/trustforge-sites/` 這個 live 路徑——已配置(post-cutover)時
# 正 active 的那份會被就地覆寫，比 Step 2 驗證、Step 3 快照、ERR trap
# 都還早：下載中/新內容本身有問題，active 的檔案內容已經壞了；就算這次
# 沒有其他步驟失敗（workers 記憶體裡還撐著舊設定），下次任何原因觸發
# reload 都會直接吃到壞掉的內容而爆——舊 ROLLBACK() 只還原 live symlink
# 指標，從沒還原過檔案內容本身，救不回來。改法跟 dist 一致：下載到全新
# 的 versioned staging 目錄，完全不碰 live 的 trustforge-sites/*.conf，
# 驗證（Step 2）也是驗 staging 內容；真正寫進 live 路徑留到 Step 4，在
# ERR trap 保護下用 atomic rename 替換，且 Step 3 會先把 live 檔案目前
# 的 byte 內容備份起來，失敗時 ROLLBACK() 能逐檔還原成跑前內容。
dnf install -y nginx unzip >"$DNF_LOG" 2>&1
RELEASE_TS="$(date -u +%Y%m%d%H%M%S)-$$"
mkdir -p "$CONF_DIR" "$OPT_DIR/frontend/releases"
CONF_STAGING_DIR="$ETC/nginx/trustforge-sites-staging/$RELEASE_TS"
mkdir -p "$CONF_STAGING_DIR"
aws s3 cp s3://__BUCKET__/nginx-legacy.conf "$CONF_STAGING_DIR/legacy.conf" --region __REGION__
aws s3 cp s3://__BUCKET__/nginx-react.conf "$CONF_STAGING_DIR/react.conf" --region __REGION__
aws s3 cp s3://__BUCKET__/nginx-react-http.conf "$CONF_STAGING_DIR/react-http.conf" --region __REGION__
aws s3 cp s3://__BUCKET__/nginx-legacy-tls.conf "$CONF_STAGING_DIR/legacy-tls.conf" --region __REGION__
for _f in "${CONF_FILES[@]}"; do [ -f "$CONF_STAGING_DIR/$_f" ]; done
aws s3 cp s3://__BUCKET__/trustforge_frontend_dist.zip "$OPT_DIR/frontend/dist.zip" --region __REGION__
CURRENT_LINK="$OPT_DIR/frontend/current"
RELEASE_DIR="$OPT_DIR/frontend/releases/$RELEASE_TS"
mkdir -p "$RELEASE_DIR"
unzip -o -q "$OPT_DIR/frontend/dist.zip" -d "$RELEASE_DIR"
[ -f "$RELEASE_DIR/index.html" ]
echo "[fe-nginx] 已裝 nginx + 佈署靜態檔/candidate conf 到 staging 位置（新版 dist 在 ${RELEASE_DIR}、candidate conf 在 ${CONF_STAGING_DIR}，未動 live conf.d/trustforge-sites/systemd/frontend/current）"

# ---- Step 1.5（issue #69）：偵測 live symlink 是否已配置（唯讀，不算
#      mutation）——決定 Step 2 要驗哪份 candidate、Step 4/5 走「已配置
#      (post-cutover，保留現有拓樸+CSP mode 不動，只換 dist/reload)」還是
#      「未配置(初次 bootstrap，legacy 全轉發)」，避免 React/TLS cutover
#      後重跑本腳本（例如單純更新前端 dist）把拓樸打回 legacy ----
if [ -L "$LIVE_LINK" ] && PREV_LINK="$(readlink "$LIVE_LINK" 2>/dev/null)" && [ -n "$PREV_LINK" ]; then
  PREV_LINK_EXISTED=1
  ACTIVE_CANDIDATE="$PREV_LINK"
  echo "[fe-nginx] 偵測到 live symlink 已配置（$(basename "$PREV_LINK")）→ 視為 post-cutover，本次只更新 dist/candidate conf，不重指 symlink、不改 CSP mode"
else
  PREV_LINK_EXISTED=0
  PREV_LINK=""
  ACTIVE_CANDIDATE="$CANDIDATE"
  echo '[fe-nginx] 偵測到 live symlink 尚未配置 → 視為初次 bootstrap，套用 legacy 全轉發預設'
fi
# codex 複審 HIGH（conf 原子化）：Step 2 驗證的必須是「這次會生效的新
# 內容」，但 live 路徑（$ACTIVE_CANDIDATE）現在到 Step 4 前都還是舊內容
# （Step 1 只下載到 staging，沒有覆寫 live）——驗證對象改指到 staging 裡
# 同檔名的那份。
ACTIVE_CANDIDATE_STAGED="$CONF_STAGING_DIR/$(basename "$ACTIVE_CANDIDATE")"

# 同一批唯讀偵測，補記錄 frontend/current（dist）跑前指到哪個 release，
# 供 Step 4 的 atomic symlink 切換 + ROLLBACK() 還原用（codex 複審 HIGH：
# dist swap 原子化的一部分——現在活著的 release 目錄本身完全不動，這裡
# 只是記住指標，rollback 時要能把指標切回去）。
if [ -L "$CURRENT_LINK" ] && PREV_CURRENT_TARGET="$(readlink "$CURRENT_LINK" 2>/dev/null)" && [ -n "$PREV_CURRENT_TARGET" ]; then
  PREV_CURRENT_EXISTED=1
else
  PREV_CURRENT_EXISTED=0
  PREV_CURRENT_TARGET=""
fi

# ---- Step 2：候選設定驗證（scratch harness），完全不動 live conf.d ----
#      驗證對象是「這次真的會生效」的那份 conf 的**新內容**：已配置
#      (post-cutover)驗目前 active 那個檔名對應的 staging 新版本，未配置
#      (初次)驗 legacy.conf 的 staging 新版本（bootstrap 預設）——codex
#      複審 HIGH（conf 原子化）：驗的是 staging，不是還沒被 Step 4 換過
#      的 live 舊內容，也完全不會像舊版那樣提早覆寫 live ----
VALIDATE_CONF="/tmp/tf-bootstrap-validate-$$.conf"
cat > "$VALIDATE_CONF" <<VALIDATE_EOF
worker_processes 1;
error_log /tmp/tf-bootstrap-validate-$$.err.log;
pid /tmp/tf-bootstrap-validate-$$.pid;
events { worker_connections 16; }
http { include $ACTIVE_CANDIDATE_STAGED; }
VALIDATE_EOF
if ! nginx -t -c "$VALIDATE_CONF" 2>/tmp/tf-bootstrap-validate-$$.stderr; then
  echo "❌ [fe-nginx] 候選 nginx 設定驗證失敗（$(basename "$ACTIVE_CANDIDATE")，staging 新內容），完全沒動 live conf.d/trustforge-sites/systemd，中止" >&2
  cat /tmp/tf-bootstrap-validate-$$.stderr >&2 2>/dev/null || true
  rm -f "$VALIDATE_CONF" "/tmp/tf-bootstrap-validate-$$.err.log" "/tmp/tf-bootstrap-validate-$$.pid" "/tmp/tf-bootstrap-validate-$$.stderr"
  exit 1
fi
rm -f "$VALIDATE_CONF" "/tmp/tf-bootstrap-validate-$$.err.log" "/tmp/tf-bootstrap-validate-$$.pid" "/tmp/tf-bootstrap-validate-$$.stderr"
echo '[fe-nginx] 候選設定驗證通過（未動 live conf.d/trustforge-sites/systemd）'

# ---- Step 3：記錄跑前狀態，掛失敗回滾（PREV_LINK/PREV_LINK_EXISTED 已在
#      Step 1.5 記錄，這裡只補其餘跑前狀態）----
PREV_DEFAULT_CONF_EXISTED=0
if [ -f "$DEFAULT_CONF" ]; then
  PREV_DEFAULT_CONF_EXISTED=1
  cp "$DEFAULT_CONF" "$BACKUP_DEFAULT_CONF"
fi
# codex 複審 HIGH（conf 原子化）：Step 4 才會真的把 staging 內容 atomic
# replace 進 $CONF_DIR/*.conf，這裡先把每份 live conf 檔目前的 byte 內容
# snapshot 起來（不存在就不備份，靠 ROLLBACK() 裡檢查備份檔是否存在來
# 判斷跑前是否存在）——光靠上面 PREV_LINK 記住 symlink 指標還不夠，
# symlink 指向的檔案內容本身也要能還原，不然 rollback 只是把 symlink
# 切回同一份已經被覆寫壞掉的檔案。
mkdir -p "$BACKUP_CONF_DIR"
for _f in "${CONF_FILES[@]}"; do
  if [ -f "$CONF_DIR/$_f" ]; then
    cp "$CONF_DIR/$_f" "$BACKUP_CONF_DIR/$_f"
  fi
done
cp "$SERVICE_FILE" "$BACKUP_SERVICE_FILE"
PREV_PORT="$(grep '^Environment=PORT=' "$SERVICE_FILE" | head -1 | cut -d= -f3)"
PREV_PORT="${PREV_PORT:-80}"
PREV_TRUSTFORGE_ACTIVE="$(systemctl is-active trustforge 2>/dev/null || echo inactive)"
PREV_NGINX_ACTIVE="$(systemctl is-active nginx 2>/dev/null || echo inactive)"
PREV_NGINX_ENABLED="$(systemctl is-enabled nginx 2>/dev/null || echo disabled)"

ROLLBACK() {
  local ec=$?
  trap - ERR
  echo "❌ [fe-nginx] bootstrap 中失敗（exit=${ec}），回滾到跑前狀態…" >&2
  local ROLLBACK_OK=1

  if ! cp "$BACKUP_SERVICE_FILE" "$SERVICE_FILE"; then
    echo "❌ [fe-nginx] rollback：trustforge.service 還原失敗" >&2
    ROLLBACK_OK=0
  fi
  if ! systemctl daemon-reload; then
    echo "❌ [fe-nginx] rollback：daemon-reload 失敗" >&2
    ROLLBACK_OK=0
  fi
  if [ "$PREV_TRUSTFORGE_ACTIVE" = "active" ]; then
    if ! systemctl restart trustforge; then
      echo "❌ [fe-nginx] rollback：trustforge 重啟失敗（還原跑前 bind/port）" >&2
      ROLLBACK_OK=0
    fi
  else
    systemctl stop trustforge 2>/dev/null || true
  fi

  if [ "$PREV_LINK_EXISTED" = 1 ]; then
    if ! ln -sfn "$PREV_LINK" "$LIVE_LINK"; then
      echo "❌ [fe-nginx] rollback：live symlink 還原失敗（ln -sfn ${PREV_LINK} -> ${LIVE_LINK}）" >&2
      ROLLBACK_OK=0
    fi
  else
    # 跑前本來就沒有（或讀不到）live symlink，正確的還原目標是「移除」
    # 這次新建的 symlink，不是留著不管（那樣才是誤還原）
    if ! rm -f "$LIVE_LINK"; then
      echo "❌ [fe-nginx] rollback：live symlink 移除失敗（跑前無 symlink，理應還原成無：${LIVE_LINK}）" >&2
      ROLLBACK_OK=0
    fi
  fi

  # codex 複審 HIGH（conf 原子化，同 dist）：上面只還原了 live symlink
  # 指標，這裡要把每份 conf 檔本身的內容還原成 Step 3 snapshot 的
  # byte-for-byte 跑前內容——不存在就刪掉（跑前本來就沒有這份檔案）。
  # 光切 symlink 指標救不回被 Step 4 覆寫過的檔案內容。
  for _f in "${CONF_FILES[@]}"; do
    if [ -f "$BACKUP_CONF_DIR/$_f" ]; then
      if ! cp "$BACKUP_CONF_DIR/$_f" "$CONF_DIR/$_f"; then
        echo "❌ [fe-nginx] rollback：${_f} 內容還原失敗（byte-for-byte 還原跑前版本）" >&2
        ROLLBACK_OK=0
      fi
    else
      if ! rm -f "$CONF_DIR/$_f"; then
        echo "❌ [fe-nginx] rollback：${_f} 移除失敗（跑前不存在，理應還原成無）" >&2
        ROLLBACK_OK=0
      fi
    fi
  done

  # codex 複審 HIGH：dist swap 原子化——frontend/current 這個 symlink 才
  # 是真正決定 nginx serve 哪份 dist 的指標（前一版 release 目錄本身從
  # 頭到尾都沒被動過，只要把指標切回去，前一版立刻恢復完整可服務）
  if [ "$PREV_CURRENT_EXISTED" = 1 ]; then
    if ! ln -sfn "$PREV_CURRENT_TARGET" "$CURRENT_LINK"; then
      echo "❌ [fe-nginx] rollback：frontend/current 還原失敗（ln -sfn ${PREV_CURRENT_TARGET} -> ${CURRENT_LINK}）" >&2
      ROLLBACK_OK=0
    fi
  else
    if ! rm -f "$CURRENT_LINK"; then
      echo "❌ [fe-nginx] rollback：frontend/current 移除失敗（跑前無 symlink，理應還原成無：${CURRENT_LINK}）" >&2
      ROLLBACK_OK=0
    fi
  fi

  if [ "$PREV_DEFAULT_CONF_EXISTED" = 1 ]; then
    if ! cp "$BACKUP_DEFAULT_CONF" "$DEFAULT_CONF"; then
      echo "❌ [fe-nginx] rollback：default.conf 還原失敗" >&2
      ROLLBACK_OK=0
    fi
  else
    rm -f "$DEFAULT_CONF" || true
  fi

  if [ "$PREV_NGINX_ACTIVE" = "active" ]; then
    if ! { nginx -t && systemctl reload nginx; }; then
      echo "❌ [fe-nginx] rollback：nginx reload 失敗（跑前 nginx 本來在跑）" >&2
      ROLLBACK_OK=0
    fi
  else
    systemctl stop nginx 2>/dev/null || true
    if [ "$PREV_NGINX_ENABLED" != "enabled" ]; then
      systemctl disable nginx 2>/dev/null || true
    fi
  fi

  # 回滾後主動驗證：python 直接對外（跑前的 port，通常是 80）healthz
  # 真的有回應——不是只看每一步指令的 exit code，指令都成功不代表服務
  # 真的健康（比照 cutover_switch.sh 的回滾誠實性設計）
  sleep 1
  if ! curl -fsS "http://localhost:${PREV_PORT}/healthz" >/dev/null 2>&1; then
    echo "❌ [fe-nginx] rollback 驗證失敗：healthz（port ${PREV_PORT}，跑前狀態）沒有回應" >&2
    ROLLBACK_OK=0
  fi

  rm -f "$BACKUP_SERVICE_FILE" "$BACKUP_DEFAULT_CONF"
  rm -rf "$BACKUP_CONF_DIR"

  if [ "$ROLLBACK_OK" = 1 ]; then
    echo '✅ [fe-nginx] 已回滾到跑前狀態（bootstrap 失敗，但服務已還原成執行前的樣子，不留半殘）' >&2
    exit "$ec"
  fi

  echo '🆘 [fe-nginx] ROLLBACK-FAILED：自動回滾沒有完全成功，請立即人工介入，不要假設服務已還原！' >&2
  echo "   1) 檢查 python service：systemctl status trustforge；grep Environment= ${SERVICE_FILE}" >&2
  echo "      （跑前備份，若還在：${BACKUP_SERVICE_FILE}）" >&2
  echo "   2) 檢查 nginx symlink：ls -l ${LIVE_LINK}（跑前 PREV_LINK_EXISTED=${PREV_LINK_EXISTED} PREV_LINK=${PREV_LINK}）" >&2
  echo "   3) 檢查 healthz：curl -v http://localhost:${PREV_PORT}/healthz" >&2
  exit 97
}
trap 'ROLLBACK' ERR

# ---- Step 4：唯一會動 live 狀態的區段，全程在上面的 ERR trap 保護下 ----
#      issue #69：已配置(post-cutover，PREV_LINK_EXISTED=1)時**不重指
#      symlink、不改 CSP mode**，只用剛被 Step 1 更新過的 dist/candidate
#      conf reload 現行拓樸；只有未配置(初次 bootstrap)才做完整的「收斂
#      python 綁定 + 建 legacy symlink」流程 ----
# codex 複審 HIGH（conf 原子化，同 dist）：唯一真的把新版 conf 內容寫
# 進 live 路徑（$CONF_DIR/*.conf）的地方，且完全在 ERR trap 保護下——用
# 暫存檔 + 同檔案系統內的 `mv` 做 atomic rename（不是直接 `cp` 蓋過去，
# 避免中途讀到半份寫入的檔案），後續任何一步失敗，ROLLBACK() 都能把每
# 份檔案還原成 Step 3 snapshot 的 byte-for-byte 跑前內容。
for _f in "${CONF_FILES[@]}"; do
  cp "$CONF_STAGING_DIR/$_f" "$CONF_DIR/$_f.new.$$"
  mv "$CONF_DIR/$_f.new.$$" "$CONF_DIR/$_f"
done

# codex 複審 HIGH：dist swap 真正的 atomic 切換點——Step 1 已經把新版
# dist 解壓驗證(有 index.html)完，Step 2 也驗過 candidate conf，這裡才
# 是唯一真的把 frontend/current 指到新 release 的地方，且完全在 ERR
# trap 保護下：後面任何一步失敗，ROLLBACK() 都能把這個 symlink 切回
# PREV_CURRENT_TARGET，前一版 release 目錄自始至終沒被動過，直接可服務。
ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"
if [ "$PREV_LINK_EXISTED" = 1 ]; then
  echo "[fe-nginx] 已配置(post-cutover)：保留現有 symlink（$(basename "$PREV_LINK")）與 CSP mode，只切新版 dist（frontend/current -> $(basename "$RELEASE_DIR")）+ 驗證+reload"
  nginx -t
  systemctl reload nginx || systemctl restart nginx
else
  rm -f "$DEFAULT_CONF"
  ln -sfn "$CANDIDATE" "$LIVE_LINK"
  # 收斂 python 綁定：只對內監聽，nginx 是唯一對外入口（見 web.py::main() 說明）。
  sed -i "s|^Environment=PORT=.*|Environment=PORT=8080|" "$SERVICE_FILE"
  grep -q '^Environment=TRUSTFORGE_BIND_HOST=' "$SERVICE_FILE" || \
    sed -i '/^Environment=PORT=/a Environment=TRUSTFORGE_BIND_HOST=127.0.0.1' "$SERVICE_FILE"
  grep -q '^Environment=TRUSTFORGE_TRUST_PROXY=' "$SERVICE_FILE" || \
    sed -i '/^Environment=TRUSTFORGE_BIND_HOST=/a Environment=TRUSTFORGE_TRUST_PROXY=1' "$SERVICE_FILE"
  grep -q '^Environment=TRUSTFORGE_CSP_MODE=' "$SERVICE_FILE" || \
    sed -i '/^Environment=TRUSTFORGE_TRUST_PROXY=/a Environment=TRUSTFORGE_CSP_MODE=legacy' "$SERVICE_FILE"
  systemctl daemon-reload
  systemctl restart trustforge
  nginx -t
  systemctl enable --now nginx
  systemctl reload nginx || systemctl restart nginx
fi

# ---- Step 5：post-switch 驗證（bare test/不用 `|| true` 吞掉——public
#      path 驗證失敗要能觸發上面的 ROLLBACK，這正是 codex 五次複審點名的
#      「public-path 驗證失敗、任一 mutation 邊界後失敗都要能回滾」）。
#      依「現行 active 拓樸」驗（不是永遠假設 legacy）：react.conf／
#      legacy-tls.conf 且憑證已存在 → HTTPS --resolve；其餘 → HTTP。
#      比照 cutover_switch.sh：加 retry（見共用的 _tf_retry），撐過 nginx
#      reload 之後極短暫的 worker 交接窗口，避免單發 curl 誤判失敗、白白
#      觸發 rollback ----
sleep 1
[ "$(systemctl is-active trustforge)" = "active" ]
[ "$(systemctl is-active nginx)" = "active" ]

if [ "$PREV_LINK_EXISTED" = 1 ]; then
  ACTIVE_BASENAME="$(basename "$PREV_LINK")"
else
  ACTIVE_BASENAME="$(basename "$CANDIDATE")"
fi

TF_BOOTSTRAP_SMOKE_RETRIES="${TF_BOOTSTRAP_SMOKE_RETRIES:-10}"
TF_BOOTSTRAP_SMOKE_DELAY="${TF_BOOTSTRAP_SMOKE_DELAY:-2}"
_tf_retry() {
  local _tf_retry_i _tf_retry_out
  _tf_retry_out="$(mktemp)"
  for _tf_retry_i in $(seq 1 "$TF_BOOTSTRAP_SMOKE_RETRIES"); do
    if "$@" >"$_tf_retry_out" 2>&1; then
      rm -f "$_tf_retry_out"
      return 0
    fi
    if [ "$_tf_retry_i" -lt "$TF_BOOTSTRAP_SMOKE_RETRIES" ]; then
      sleep "$TF_BOOTSTRAP_SMOKE_DELAY"
    fi
  done
  # codex 複審 HIGH：全部重試用完仍失敗才把最後一次嘗試的實際輸出吐回
  # stderr，不要整段吞掉——舊寫法靠 retry 之後再補一個無保護的裸重複探測
  # 來洩漏這段訊息，裸探測移除後改成這裡直接重播最後一次的輸出。
  cat "$_tf_retry_out" >&2
  rm -f "$_tf_retry_out"
  return 1
}

CERT_FILE="$ETC/letsencrypt/live/${REACT_TLS_DOMAIN}/fullchain.pem"
if { [ "$ACTIVE_BASENAME" = "react.conf" ] || [ "$ACTIVE_BASENAME" = "legacy-tls.conf" ]; } && [ -f "$CERT_FILE" ]; then
  # codex 複審 HIGH：_tf_retry 剛判定健康，緊接著又跟一個無保護的裸重複
  # 探測，等於白包 retry——那一次若撞上瞬斷會誤觸發 rollback。移除裸重複
  # 探測，重試耗盡就直接在 if body 內用 false 觸發 ERR trap。
  if ! _tf_retry curl -fsS --resolve "${REACT_TLS_DOMAIN}:443:127.0.0.1" -o /dev/null "https://${REACT_TLS_DOMAIN}/healthz"; then
    echo "❌ [fe-nginx] 完成後驗證失敗：HTTPS https://${REACT_TLS_DOMAIN}/healthz 沒有回應（重試 ${TF_BOOTSTRAP_SMOKE_RETRIES} 次，間隔 ${TF_BOOTSTRAP_SMOKE_DELAY}s，仍失敗；現行拓樸 ${ACTIVE_BASENAME}）" >&2
    false
  fi
else
  if ! _tf_retry curl -fsS http://localhost/healthz -o /dev/null; then
    echo "❌ [fe-nginx] 完成後驗證失敗：public nginx /healthz 沒有回應（重試 ${TF_BOOTSTRAP_SMOKE_RETRIES} 次，間隔 ${TF_BOOTSTRAP_SMOKE_DELAY}s，仍失敗；現行拓樸 ${ACTIVE_BASENAME}）" >&2
    false
  fi
fi

trap - ERR
echo "[fe-nginx] nginx+python 拓樸已就緒（現行拓樸：${ACTIVE_BASENAME}，完成後驗證通過）"
CMDEOF
)
CMDS="${CMDS//__BUCKET__/$BUCKET}"
CMDS="${CMDS//__REGION__/$REGION}"

if [ "${TF_BOOTSTRAP_DRY_RUN:-}" = "1" ]; then
  printf '%s' "$CMDS"
  exit 0
fi

# --parameters 用 file:// JSON（{"commands":[...]}）傳，避開 aws CLI shorthand
# parser 對含逗號/空白/unicode 的 JSON array 解析失敗（真 AWS 才現的 bug，
# mock 假 aws 沒驗 CLI param 格式）。
_TF_PARAMS_FILE=$(mktemp "${TMPDIR:-/tmp}/tf-fe-ssm-params.XXXXXX.json")
python3 -c 'import json,sys; print(json.dumps({"commands": sys.stdin.read().splitlines()}))' <<<"$CMDS" > "${_TF_PARAMS_FILE}"
CMDID=$(aws ssm send-command --region "$REGION" --instance-ids "$IID" \
  --document-name AWS-RunShellScript \
  --parameters "file://${_TF_PARAMS_FILE}" \
  --query 'Command.CommandId' --output text)
rm -f "${_TF_PARAMS_FILE}"
if [ -z "$CMDID" ] || [ "$CMDID" = "None" ]; then
  echo "[fe-nginx] ❌ SSM send-command 未取得 CommandId，中止" >&2
  exit 1
fi
STATUS=$(poll_ssm_terminal_status "$CMDID" "$IID" 180 5) || true
# codex 五次複審：跟 cutover_switch.sh 同一個 wrapper 慣例——不能只看
# Status（連 ResponseCode 一起驗），且失敗時遠端腳本會發 distinct exit
# code（97=ROLLBACK-FAILED），讀 ResponseCode 把它原樣傳遞到這層 wrapper
# 的 top-level exit code，別全塌成 1（見 deploy/README.md exit code 慣例）。
RESPONSE_CODE=$(aws ssm get-command-invocation --region "$REGION" --command-id "$CMDID" --instance-id "$IID" --query ResponseCode --output text 2>/dev/null || true)
if [ "$STATUS" = "Success" ] && [ "$RESPONSE_CODE" = "0" ]; then
  echo "[fe-nginx] ✅ nginx 層 + python 內收斂完成、healthz 走 nginx 驗證成功（Status=${STATUS}）"
  echo "[fe-nginx] 目前拓樸：nginx(legacy, http-only) → python(127.0.0.1:8080)"
  echo "[fe-nginx] 下一步（需三審+簽核）："
  echo "[fe-nginx]   bare-IP 現況（無 domain）：deploy/cutover_switch.sh react-http"
  echo "[fe-nginx]   有 domain（trustforge.hurricanesoft.com.tw 已指好）："
  echo "[fe-nginx]     先 deploy/setup_tls.sh 簽 TLS 憑證，再 deploy/cutover_switch.sh react"
  exit 0
fi
echo "[fe-nginx] ❌ nginx+python 拓樸部署失敗：CommandId=$CMDID Status=${STATUS} ResponseCode=${RESPONSE_CODE}" >&2
aws ssm get-command-invocation --region "$REGION" --command-id "$CMDID" --instance-id "$IID" \
  --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
if [ "$RESPONSE_CODE" = "97" ]; then
  exit 97
fi
exit 1
