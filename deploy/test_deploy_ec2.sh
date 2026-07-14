#!/usr/bin/env bash
# deploy_ec2.sh 邏輯測試（禁真 AWS）：完全 mock `aws`，跑過「首次建置」與
# 「update-in-place」兩分支，斷言：
#   1. systemd trustforge.service 有 CACHE_BACKEND / TRUSTFORGE_CACHE_TABLE /
#      TRUSTFORGE_COST_LEDGER_TABLE / COST_LEDGER_BACKEND 四個 env。
#   2. fetch-scheduler.service + fetch-scheduler.timer 有裝、有 enable。
#   3. update-in-place 對「本來沒有這些 env」的舊實例也會補上（不是只在首次
#      建置才有）。
#   4. zip 封包含 scripts/（否則 timer 在 EC2 上會找不到 fetch_scheduler.py）。
#   5. 管理控制台 PR-5：TRUSTFORGE_BEDROCK_DAILY_USD_CAP 有值時寫入
#      systemd、未設時不寫；nginx react conf 的 /api/admin/ location 結構
#      （X-Real-IP/XFF 覆寫 + no-store + allowlist 預設註解）。
#   6. PR-B（runtime token 部署銜接，#119 完全退場）：deploy 只傳一個非機敏
#      opt-in 旗標 TRUSTFORGE_TOKEN_SSM_PREFIX（純路徑字串），有值時寫入
#      systemd unit（明文，不經 SSM 變數間接層）、未設時不寫（app 端自行
#      fallback env-based token，零設定不變式）；update-in-place 對這個旗標
#      走既有 ensure/replace 分支，對 #119 時代殘留的
#      TRUSTFORGE_ADMIN_TOKEN/TRUSTFORGE_LIVE_TOKEN 舊行則是無條件遷移刪除
#      （本 PR 的安全價值：token 離開 unit 檔落點）；旗標字元集注入防護
#      fail-fast；trustforge-inline 仍保留窄範圍 ssm:GetParameter/kms:Decrypt
#      （IAM 本 PR 不動，深留給 PR-C）。
#
# 用法：bash deploy/test_deploy_ec2.sh（在 repo 根目錄或 deploy/ 底下皆可）
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

PASS=0
FAIL=0

assert_contains() {
  local haystack="$1" needle="$2" desc="$3"
  if grep -qF -- "$needle" <<<"$haystack"; then
    echo "  [PASS] $desc"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] $desc — 找不到: $needle"
    FAIL=$((FAIL + 1))
  fi
}

assert_file_contains() {
  local file="$1" needle="$2" desc="$3"
  if [ -f "$file" ] && grep -qF -- "$needle" "$file"; then
    echo "  [PASS] $desc"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] $desc — 檔案 $file 找不到: $needle"
    FAIL=$((FAIL + 1))
  fi
}

assert_not_contains() {
  # fail-closed 斷言：needle **不得**出現（管理控制台 PR-5：未設 token 時
  # 「不寫該行」——出現任何一行都算失敗）。
  local haystack="$1" needle="$2" desc="$3"
  if grep -qF -- "$needle" <<<"$haystack"; then
    echo "  [FAIL] $desc — 不該出現卻出現了: $needle"
    FAIL=$((FAIL + 1))
  else
    echo "  [PASS] $desc"
    PASS=$((PASS + 1))
  fi
}

assert_line_exact() {
  # vp-eng LOW：子字串比對（grep -qF 抓到 "/admin-token" 這種前綴/後綴片段就
  # 算過）換成整行完整比對（grep -qxF）——needle 必須是 haystack 裡「獨立一
  # 整行」跟它一字不差，避免將來參數命名規則微調時，子字串巧合仍誤判通過。
  local haystack="$1" needle="$2" desc="$3"
  if grep -qxF -- "$needle" <<<"$haystack"; then
    echo "  [PASS] $desc"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] $desc — 找不到完全相符的整行: $needle"
    FAIL=$((FAIL + 1))
  fi
}

assert_unit_env_lines() {
  # 結構化解析 user-data 裡 trustforge.service 的 heredoc 區塊（不是肉眼
  # grep 整份 user-data）：斷言指定的 Environment 行「以獨立一行」存在於
  # unit 區塊內、且 ExecStart 行本身是乾淨獨立一行（抓 ${EXTRA_UNIT_ENV}
  # 嵌在 ExecStart 前若拼接出錯會黏成同一行的 bug）。
  local desc="$1" ud_file="$2" expected_csv="$3"
  local result
  result=$(python3 - "$ud_file" "$expected_csv" <<'PYEOF'
import sys

path, expected_csv = sys.argv[1], sys.argv[2]
with open(path) as f:
    lines = f.read().splitlines()
try:
    start = lines.index("cat > /etc/systemd/system/trustforge.service <<UNIT")
    end = lines.index("UNIT", start)
except ValueError:
    print("NOUNITBLOCK")
    sys.exit(0)
unit = lines[start + 1 : end]
problems = []
for want in [w for w in expected_csv.split(";") if w]:
    if want not in unit:
        problems.append("缺獨立行:" + want)
if "ExecStart=/usr/bin/python3 -m trustforge.web" not in unit:
    problems.append("ExecStart 行不乾淨（可能被 EXTRA_UNIT_ENV 黏住）")
print("MATCH" if not problems else "MISMATCH:" + "|".join(problems))
PYEOF
)
  if [ "$result" = "MATCH" ]; then
    echo "  [PASS] $desc"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] $desc — $result"
    FAIL=$((FAIL + 1))
  fi
}

assert_nginx_admin_location() {
  # 結構化解析 nginx conf 的 `location /api/admin/` 區塊（大括號配對，不是
  # 對整份檔案 grep——避免斷言到別的 location 裡剛好也有的指令）：斷言
  # harper 條件 A + M1 的硬化指令都在區塊內、allowlist 預設是註解狀態
  # （區塊內不得有未註解的 allow/deny）。
  local desc="$1" conf="$2" needle_csv="$3"
  local result
  result=$(python3 - "$conf" "$needle_csv" <<'PYEOF'
import sys

path, needle_csv = sys.argv[1], sys.argv[2]
with open(path) as f:
    lines = f.read().splitlines()
block, inside = [], False
for line in lines:
    stripped = line.strip()
    if not inside and stripped.startswith("location /api/admin/"):
        inside = True
        continue
    if inside:
        # 該 location 內沒有巢狀 block（一行一指令、右大括號獨立成行），
        # 首個獨立 "}" 即區塊結束。
        if stripped == "}":
            break
        block.append(stripped)
if not inside:
    print("NOLOCATION")
    sys.exit(0)
directives = [l for l in block if l and not l.startswith("#")]
problems = []
# needle 以換行分隔（nginx 指令本身含分號，不能用分號當分隔符）
for want in [w.strip() for w in needle_csv.split("\n") if w.strip()]:
    if want not in directives:
        problems.append("缺指令:" + want)
for d in directives:
    if d.startswith("allow ") or d.startswith("deny "):
        problems.append("allowlist 不該預設啟用（未註解）:" + d)
print("MATCH" if not problems else "MISMATCH:" + "|".join(problems))
PYEOF
)
  if [ "$result" = "MATCH" ]; then
    echo "  [PASS] $desc"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] $desc — $result"
    FAIL=$((FAIL + 1))
  fi
}

assert_nginx_admin_blocked_with_404() {
  # harper M-3 = vp-eng M-1 複審修正：早期版本以為「省略 /api/admin/
  # location」就等於禁用，這是錯的（會落入下面較短前綴的 /api/ 照樣
  # proxy）。結構化解析 `location ^~ /api/admin/` 區塊，斷言該區塊「唯一」
  # 動作是 `return 404;`（技術性封鎖，不是靠省略 location 這種消極假設）。
  local desc="$1" conf="$2"
  local result
  result=$(python3 - "$conf" <<'PYEOF'
import sys

path = sys.argv[1]
with open(path) as f:
    lines = f.read().splitlines()
block, inside = [], False
for line in lines:
    stripped = line.strip()
    if not inside and stripped.startswith("location ^~ /api/admin/"):
        inside = True
        continue
    if inside:
        if stripped == "}":
            break
        block.append(stripped)
if not inside:
    print("NOLOCATION")
    sys.exit(0)
directives = [l for l in block if l and not l.startswith("#")]
if directives == ["return 404;"]:
    print("MATCH")
else:
    print("MISMATCH:" + "|".join(directives))
PYEOF
)
  if [ "$result" = "MATCH" ]; then
    echo "  [PASS] $desc"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] $desc — $result"
    FAIL=$((FAIL + 1))
  fi
}

assert_zip_contains() {
  local zip="$1" needle="$2" desc="$3"
  # ⚠️ 不要直接 `unzip -l | grep -q`：pipefail 下 grep -q 一找到就提早關管線，
  # unzip 還在寫剩餘輸出會被 SIGPIPE，pipefail 會把這個非零 exit 當成整條
  # pipeline 失敗，導致「明明有這行」卻誤判 FAIL（時序相關、不穩定）。先把
  # 完整輸出存變數再 grep，避開這個管線提早關閉的競態。
  local listing
  listing=$(unzip -l "$zip")
  if grep -qF -- "$needle" <<<"$listing"; then
    echo "  [PASS] $desc"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] $desc — zip $zip 找不到: $needle"
    FAIL=$((FAIL + 1))
  fi
}

find_ssm_call_by_marker() {
  # issue #118（probe call 編號漂移）：deploy_ec2.sh 的 SSM send-command 編號
  # 會因部署邏輯調整而漂移——例如 verify_fetch_scheduler 把 seed 與 --probe
  # 拆成兩支獨立 command（seed 是 fire-and-forget、probe 才是 gate）、
  # update-in-place 路徑又多拆一支 unit reconcile restart。硬編號
  # ssm_params_call2.txt 會失準（probe 現在落在 call3/call4）。改為「按內容
  # 標記搜尋所有已捕捉的 ssm_params_call*.txt」，回傳第一個含指定標記的檔案
  # 路徑（找不到則空）。這讓 gate 斷言對 call 編號漂移免疫，只認語意標記。
  local marker="$1" file
  for file in "$CAPTURE"/ssm_params_call*.txt; do
    [ -f "$file" ] || continue
    if grep -qF -- "$marker" "$file"; then
      printf '%s' "$file"
      return 0
    fi
  done
  return 1
}

assert_ddb_action_set() {
  # 結構化解析 IAM policy JSON（不是肉眼字串 grep）：找出 Resource 為指定
  # table 的那個 statement，斷言它的 Action 集合「恰好等於」期望值——用來
  # 抓 codex 這種「少放/多放某個 dynamodb action」的問題（例如上一輪
  # cost-ledger 只給 PutItem+Scan，probe 的 GetItem 會被拒）。
  local desc="$1" file="$2" table="$3" expected_csv="$4"
  local result
  result=$(python3 - "$file" "$table" "$expected_csv" <<'PYEOF'
import json, sys

path, table, expected_csv = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as f:
    doc = json.load(f)
expected = {a for a in expected_csv.split(",") if a}
found = None
for stmt in doc.get("Statement", []):
    res = stmt.get("Resource", "")
    if isinstance(res, str) and res.endswith("table/" + table):
        actions = stmt.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]
        found = set(actions)
        break
if found is None:
    print("NOSTATEMENT")
elif found == expected:
    print("MATCH")
else:
    print("MISMATCH:實際=" + ",".join(sorted(found)) + " 期望=" + ",".join(sorted(expected)))
PYEOF
)
  if [ "$result" = "MATCH" ]; then
    echo "  [PASS] $desc"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] $desc — $result"
    FAIL=$((FAIL + 1))
  fi
}

assert_verify_gate_behavior() {
  # follow-up（真部署發現 reddit-429 false-fail）：光靠字串斷言（有沒有含
  # `--probe`）測不出「部署 gate 到底聽誰的」——這裡把捕捉到的 fetch-
  # scheduler 同步驗證 SSM script 實際還原成一支 bash 腳本，用假的
  # systemctl/journalctl/python3 真的把它跑一遍，直接斷言腳本本身的 exit
  # code，而不是只看文字內容像不像。
  local desc_prefix="$1" raw_content="$2" fake_systemctl_start_rc="$3" fake_probe_rc="$4" expect_rc="$5"

  if [ -z "$raw_content" ]; then
    echo "  [FAIL] $desc_prefix — 沒有可重跑的 verify script（raw content 是空的）"
    FAIL=$((FAIL + 1))
    return
  fi

  local work fake_unit fake_opt fake_py mockbin
  work=$(mktemp -d)
  fake_unit="$work/fake-fetch-scheduler.service"
  fake_opt="$work/opt-trustforge"
  fake_py="$work/python3"
  mockbin="$work/bin"
  mkdir -p "$fake_opt" "$mockbin"
  touch "$fake_unit"

  cat >"$mockbin/systemctl" <<'EOSC'
#!/usr/bin/env bash
if [ "$1" = "daemon-reload" ]; then exit 0; fi
if [ "$1" = "start" ]; then exit "${GATE_TEST_SYSTEMCTL_START_RC:-0}"; fi
exit 0
EOSC
  chmod +x "$mockbin/systemctl"

  cat >"$mockbin/journalctl" <<'EOJC'
#!/usr/bin/env bash
exit 0
EOJC
  chmod +x "$mockbin/journalctl"

  cat >"$fake_py" <<'EOPY'
#!/usr/bin/env bash
exit "${GATE_TEST_PROBE_RC:-0}"
EOPY
  chmod +x "$fake_py"

  local ssm_json="${raw_content#commands=}"
  printf '%s' "$ssm_json" >"$work/ssm.json"
  if ! python3 -c "
import json

with open('$work/ssm.json') as f:
    cmds = json.load(f)
script = chr(10).join(cmds)
script = script.replace('/etc/systemd/system/fetch-scheduler.service', '$fake_unit')
script = script.replace('/opt/trustforge', '$fake_opt')
script = script.replace('/usr/bin/python3', '$fake_py')
with open('$work/verify.sh', 'w') as f:
    f.write(script)
" 2>"$work/pyerr.txt"; then
    echo "  [FAIL] $desc_prefix — 重建 verify script 失敗：$(cat "$work/pyerr.txt")"
    FAIL=$((FAIL + 1))
    rm -rf "$work"
    return
  fi

  local rc
  set +e
  GATE_TEST_SYSTEMCTL_START_RC="$fake_systemctl_start_rc" GATE_TEST_PROBE_RC="$fake_probe_rc" \
    PATH="$mockbin:$PATH" bash "$work/verify.sh" >"$work/stdout.log" 2>"$work/stderr.log"
  rc=$?
  set -e

  if [ "$rc" = "$expect_rc" ]; then
    echo "  [PASS] ${desc_prefix}（實際重跑 gate 腳本 exit=${rc}，符合預期 ${expect_rc}）"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] ${desc_prefix} — 實際重跑 gate 腳本 exit=${rc}，預期 ${expect_rc}"
    sed 's/^/    stdout: /' "$work/stdout.log"
    sed 's/^/    stderr: /' "$work/stderr.log"
    FAIL=$((FAIL + 1))
  fi
  rm -rf "$work"
}

assert_healthz_gate_behavior() {
  # codex HIGH（首次建置缺 web healthz gate）：一樣不能只看字串斷言，這裡把
  # 捕捉到的 web healthz SSM script 實際還原成一支 bash 腳本，用假的
  # systemctl（is-active）/curl/journalctl/sleep 真的把它跑一遍，直接斷言
  # 腳本本身的 exit code。sleep 用 no-op 假的，避免真的等 12*3=36 秒。
  local desc_prefix="$1" raw_content="$2" fake_systemctl_active_rc="$3" fake_curl_rc="$4" expect_rc="$5"

  if [ -z "$raw_content" ]; then
    echo "  [FAIL] $desc_prefix — 沒有可重跑的 healthz script（raw content 是空的）"
    FAIL=$((FAIL + 1))
    return
  fi

  local work mockbin
  work=$(mktemp -d)
  mockbin="$work/bin"
  mkdir -p "$mockbin"

  cat >"$mockbin/systemctl" <<'EOSC'
#!/usr/bin/env bash
if [ "$1" = "is-active" ]; then exit "${GATE_TEST_SYSTEMCTL_ACTIVE_RC:-0}"; fi
exit 0
EOSC
  chmod +x "$mockbin/systemctl"

  cat >"$mockbin/curl" <<'EOCURL'
#!/usr/bin/env bash
exit "${GATE_TEST_CURL_RC:-0}"
EOCURL
  chmod +x "$mockbin/curl"

  cat >"$mockbin/journalctl" <<'EOJC'
#!/usr/bin/env bash
exit 0
EOJC
  chmod +x "$mockbin/journalctl"

  cat >"$mockbin/sleep" <<'EOSL'
#!/usr/bin/env bash
exit 0
EOSL
  chmod +x "$mockbin/sleep"

  local ssm_json="${raw_content#commands=}"
  printf '%s' "$ssm_json" >"$work/ssm.json"
  if ! python3 -c "
import json

with open('$work/ssm.json') as f:
    cmds = json.load(f)
script = chr(10).join(cmds)
with open('$work/healthz.sh', 'w') as f:
    f.write(script)
" 2>"$work/pyerr.txt"; then
    echo "  [FAIL] $desc_prefix — 重建 healthz script 失敗：$(cat "$work/pyerr.txt")"
    FAIL=$((FAIL + 1))
    rm -rf "$work"
    return
  fi

  local rc
  set +e
  GATE_TEST_SYSTEMCTL_ACTIVE_RC="$fake_systemctl_active_rc" GATE_TEST_CURL_RC="$fake_curl_rc" \
    PATH="$mockbin:$PATH" bash "$work/healthz.sh" >"$work/stdout.log" 2>"$work/stderr.log"
  rc=$?
  set -e

  if [ "$rc" = "$expect_rc" ]; then
    echo "  [PASS] ${desc_prefix}（實際重跑 healthz gate 腳本 exit=${rc}，符合預期 ${expect_rc}）"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] ${desc_prefix} — 實際重跑 healthz gate 腳本 exit=${rc}，預期 ${expect_rc}"
    sed 's/^/    stdout: /' "$work/stdout.log"
    sed 's/^/    stderr: /' "$work/stderr.log"
    FAIL=$((FAIL + 1))
  fi
  rm -rf "$work"
}

assert_inline_ssm_kms_stmts() {
  # #119/PR-A（CEO gate：IAM 窄範圍）：結構化解析 trustforge-inline policy——
  # 必須恰好有兩條獨立的 ssm:GetParameter statement，Resource 集合恰好是
  # {parameter/trustforge/deploy/*, parameter/trustforge/runtime/*}（不是
  # "*" 也不是更寬前綴、也不能多或少一條）；kms:Decrypt 必須帶
  # kms:ViaService=ssm.$REGION.amazonaws.com 條件（alias 不能直接當 IAM
  # Resource 比對，用 ViaService 收斂「只有經同區 SSM 服務」的 decrypt）。
  local desc="$1" file="$2"
  local result
  if [ ! -f "$file" ]; then
    echo "  [FAIL] $desc — 找不到 policy 捕捉檔 $file"
    FAIL=$((FAIL + 1))
    return
  fi
  result=$(python3 - "$file" <<'PYEOF'
import json, sys

path = sys.argv[1]
with open(path) as f:
    doc = json.load(f)
problems = []
ssm_stmts = []
kms_stmt = None
for stmt in doc.get("Statement", []):
    actions = stmt.get("Action", [])
    if isinstance(actions, str):
        actions = [actions]
    if "ssm:GetParameter" in actions:
        ssm_stmts.append(stmt)
    if "kms:Decrypt" in actions:
        kms_stmt = stmt
if len(ssm_stmts) != 2:
    problems.append("ssm:GetParameter statement 數量應為 2，實際為:" + str(len(ssm_stmts)))
else:
    expected = {
        "arn:aws:ssm:ap-southeast-2:123456789012:parameter/trustforge/deploy/*",
        "arn:aws:ssm:ap-southeast-2:123456789012:parameter/trustforge/runtime/*",
    }
    actual = set()
    for stmt in ssm_stmts:
        actions = stmt.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]
        res = stmt.get("Resource")
        if actions != ["ssm:GetParameter"]:
            problems.append("ssm statement (Resource=" + str(res) + ") 夾帶其他 Action:" + str(stmt.get("Action")))
        actual.add(res)
    if actual != expected:
        problems.append("ssm:GetParameter Resource 集合不符，實際:" + str(actual))
if kms_stmt is None:
    problems.append("缺 kms:Decrypt statement")
else:
    if kms_stmt.get("Action") != "kms:Decrypt" and kms_stmt.get("Action") != ["kms:Decrypt"]:
        problems.append("kms statement 夾帶其他 Action:" + str(kms_stmt.get("Action")))
    via = (kms_stmt.get("Condition", {}).get("StringEquals", {}).get("kms:ViaService"))
    if via != "ssm.ap-southeast-2.amazonaws.com":
        problems.append("kms:Decrypt 缺 ViaService=ssm.<region> 條件:" + str(via))
print("MATCH" if not problems else "MISMATCH:" + "|".join(problems))
PYEOF
)
  if [ "$result" = "MATCH" ]; then
    echo "  [PASS] $desc"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] $desc — $result"
    FAIL=$((FAIL + 1))
  fi
}

MOCKDIR=$(mktemp -d)
CAPTURE=$(mktemp -d)
trap 'rm -rf "$MOCKDIR" "$CAPTURE"' EXIT

# ---------------------------------------------------------------------------
# 通用 aws mock：把場景（first-time / update-in-place）用 env AWS_MOCK_SCENARIO
# 切換；所有子指令都要顯式處理，未預期的子指令直接印錯離開，避免測試「假過」。
# ---------------------------------------------------------------------------
cat > "$MOCKDIR/aws" <<'MOCKEOF'
#!/usr/bin/env bash
set -euo pipefail
CAPTURE_DIR="${TF_TEST_CAPTURE_DIR:?TF_TEST_CAPTURE_DIR not set}"
SCENARIO="${TF_TEST_SCENARIO:?TF_TEST_SCENARIO not set}"

args=("$@")
join() { local IFS=' '; echo "$*"; }
ALL="$(join "${args[@]}")"
# harper CISO M-2：無條件記下「這次部署跑過的每一次 aws 呼叫」（不管哪個
# case 分支吃到），供上層斷言「首建帶 token 無 override 時，中止前只打過
# 幾次 aws」（不能只靠肉眼看 stdout，要能結構化數呼叫次數/內容）。
echo "$ALL" >> "$CAPTURE_DIR/aws_calls_${SCENARIO}.log"

find_after() {
  # 印出 argv 中緊接在 $1 flag 後面的值
  local flag="$1"
  local i
  for i in "${!args[@]}"; do
    if [ "${args[$i]}" = "$flag" ]; then
      echo "${args[$((i + 1))]}"
      return 0
    fi
  done
  return 1
}

case "$ALL" in
  "sts get-caller-identity"*)
    echo "123456789012" ;;
  "s3api head-bucket"*)
    exit 0 ;;
  "s3 cp"*)
    exit 0 ;;
  "ec2 describe-instances --region"*"Reservations[].Instances[].[InstanceId,State.Name]"*)
    case "$SCENARIO" in
      update-in-place|scheduler-fail|update-in-place-token-prefix|update-in-place-cap)
        printf 'i-0123456789abcdef0\trunning\n' ;;
      *)
        printf '' ;;
    esac ;;
  "ec2 describe-instances --region"*"length(Reservations[].Instances[])"*)
    echo 1 ;;
  "ec2 describe-instances --region"*"PublicIpAddress"*)
    echo "203.0.113.10" ;;
  "iam get-role"*)
    # CISO hardening R2（#2b）：iam-role-missing 場景故意讓 get-role 回
    # not-found，逼 deploy_ec2.sh 走「建 IAM 角色」那個分支（含 Bedrock
    # 收斂 region 的 trustforge-inline policy），其餘場景維持既有假設
    # （角色已存在，不重複建立）。
    if [ "$SCENARIO" = "iam-role-missing" ]; then
      exit 1
    fi
    exit 0 ;;
  "iam create-role"*)
    exit 0 ;;
  "iam attach-role-policy"*)
    exit 0 ;;
  "iam create-instance-profile"*)
    exit 0 ;;
  "iam add-role-to-instance-profile"*)
    exit 0 ;;
  "iam put-role-policy"*)
    # reconcile 用：抓 --policy-name / --policy-document 存起來供斷言（兩條
    # 部署路徑都要跑過這裡，且 dynamodb 那份要鎖兩個 table ARN）。用場景名
    # 分檔避免兩個場景先後跑時互相覆蓋。
    PNAME=$(find_after --policy-name)
    PDOC=$(find_after --policy-document)
    printf '%s' "$PDOC" > "$CAPTURE_DIR/iam_policy_${SCENARIO}_${PNAME}.txt"
    exit 0 ;;
  "dynamodb describe-table"*)
    if [ "$SCENARIO" = "table-missing" ]; then
      exit 254
    fi
    exit 0 ;;
  "dynamodb describe-time-to-live"*)
    # lease bootstrap 的重跑路徑：TTL 已啟用時不可再呼叫 update，否則 AWS
    # 會回 ValidationException。以真實狀態回應，確保 mock 不掩蓋該契約。
    echo "ENABLED" ;;
  "ssm describe-instance-information"*)
    echo "Online" ;;
  "ec2 describe-vpcs"*)
    echo "vpc-0123456789abcdef0" ;;
  "ec2 describe-security-groups"*)
    echo "sg-0123456789abcdef0" ;;
  "ssm get-parameter"*)
    echo "ami-0123456789abcdef0" ;;
  "ec2 run-instances"*)
    # 首次建置：捕捉 user-data（--user-data file://$UD），run-instances 回來後
    # 呼叫端會 rm 掉該暫存檔，所以要在這裡（呼叫當下）先複製出來。
    UD_ARG=$(find_after --user-data)
    UD_PATH="${UD_ARG#file://}"
    cp "$UD_PATH" "$CAPTURE_DIR/user_data.sh"
    echo "i-0123456789abcdef0" ;;
  "ec2 wait instance-running"*)
    exit 0 ;;
  "ssm send-command"*)
    # 一次部署可能送出 2 次 send-command（update-in-place 主設定 + 兩條路徑
    # 都會有的 verify_fetch_scheduler 驗證）：用計數器分開存檔，CommandId 也
    # 帶編號，讓 get-command-invocation 能依 --command-id 分辨是哪一次呼叫。
    N=1
    if [ -f "$CAPTURE_DIR/ssm_call_count" ]; then
      N=$(($(cat "$CAPTURE_DIR/ssm_call_count") + 1))
    fi
    echo "$N" > "$CAPTURE_DIR/ssm_call_count"
    PARAMS=$(find_after --parameters)
    printf '%s' "$PARAMS" > "$CAPTURE_DIR/ssm_params_call${N}.txt"
    echo "cmd-call${N}" ;;
  "ssm wait command-executed"*)
    exit 0 ;;
  "ssm get-command-invocation"*)
    CMDID_ARG=$(find_after --command-id)
    # scheduler-fail 場景：讓「驗證 fetch-scheduler」那次 send-command 回
    # Failed（模擬 DynamoDB IAM 權限不夠、scheduler exit 1 的真實情境），其餘
    # 呼叫維持 Success，藉此斷言 deploy 腳本會非零結束、不會誤報成功。
    # healthz-fail 場景（codex HIGH）：讓首次建置路徑第一次 send-command
    # （verify_web_healthz）回 Failed（模擬 systemctl is-active/curl healthz
    # 失敗），藉此斷言部署會非零結束、且第二次 send-command（verify_fetch_
    # scheduler 的 --probe）根本沒被呼叫到——healthz gate 獨立擋在 probe 之前。
    # issue #118：改為「按內容標記」判斷該次 send-command 是不是 probe /
    # healthz gate，而非硬編號 cmd-call2 / cmd-call1——deploy_ec2.sh 的 SSM
    # call 編號會因部署邏輯調整漂移（seed 與 probe 拆成兩支、update-in-place
    # 多拆一支 unit reconcile restart），硬編號會讓 scheduler-fail /
    # healthz-fail 模擬打錯對象，gate 防護因此形同虛設。
    N="${CMDID_ARG#cmd-call}"
    PARAMFILE="$CAPTURE_DIR/ssm_params_call${N}.txt"
    if [ "$SCENARIO" = "scheduler-fail" ] && grep -qF "fetch_scheduler.py --probe" "$PARAMFILE" 2>/dev/null; then
      echo "Failed"
    elif [ "$SCENARIO" = "healthz-fail" ] && grep -qF "curl -fsS http://localhost/healthz" "$PARAMFILE" 2>/dev/null; then
      echo "Failed"
    else
      echo "Success"
    fi ;;
  *)
    echo "[aws-mock] 未預期的呼叫，測試沒 mock 到，中止: $ALL" >&2
    exit 99 ;;
esac
MOCKEOF
chmod +x "$MOCKDIR/aws"

run_deploy() {
  local scenario="$1"
  shift || true
  # ssm_call_count 是「這次部署送了幾次 ssm send-command」的計數器，每個
  # scenario 各自從 1 開始編號（call1=主設定或首次驗證、call2=update-in-place
  # 額外的 fetch-scheduler 同步驗證），開跑前先清掉避免跨場景疊加。
  # 管理控制台 PR-5：預設用 `env -u` 隔離外層可能殘留的 env——TRUSTFORGE_
  # ADMIN_TOKEN / TRUSTFORGE_LIVE_TOKEN（PR-B 起 deploy 已不接受這兩個值，
  # 隔離純粹是防禦性的，避免開發機殘留干擾）+ TRUSTFORGE_BEDROCK_DAILY_USD_CAP
  # + TRUSTFORGE_TOKEN_SSM_PREFIX 兩個 opt-in 旗標（避免「未設＝不寫該行」的
  # fail-closed 斷言假 PASS/假 FAIL）；PR-B 起場景改用旗標傳遞、不再傳 token
  # 值（例：run_deploy update-in-place TRUSTFORGE_TOKEN_SSM_PREFIX=/trustforge/runtime）。
  rm -f "$CAPTURE/ssm_call_count"
  # issue #118：每個 scenario 各自從 1 開始編號 send-command，但舊的
  # ssm_params_call*.txt（例如上一個 scenario 留下的 call3/call4 probe）不會
  # 被覆寫，會殘留在共享的 CAPTURE 目錄裡，讓 find_ssm_call_by_marker 搜到
  # 過期內容而誤判。這裡先把上一個 scenario 的 call 捕捉檔清掉，確保只看到
  # 本次 scenario 自己送出的 send-command。
  rm -f "$CAPTURE"/ssm_params_call*.txt
  TF_TEST_SCENARIO="$scenario" TF_TEST_CAPTURE_DIR="$CAPTURE" PATH="$MOCKDIR:$PATH" \
    env -u TRUSTFORGE_ADMIN_TOKEN -u TRUSTFORGE_LIVE_TOKEN -u TRUSTFORGE_BEDROCK_DAILY_USD_CAP -u TRUSTFORGE_TOKEN_SSM_PREFIX "$@" \
    bash "$REPO_ROOT/deploy/deploy_ec2.sh" >"$CAPTURE/stdout_$scenario.log" 2>&1
}

echo "== 場景 1：首次建置（無既有實例）=="
if run_deploy "first-time"; then
  echo "  deploy_ec2.sh 執行成功（exit 0）"
else
  echo "  [FAIL] deploy_ec2.sh 首次建置場景非零結束"
  cat "$CAPTURE/stdout_first-time.log"
  FAIL=$((FAIL + 1))
fi

UD_CONTENT=$(cat "$CAPTURE/user_data.sh" 2>/dev/null || echo "")
if [ -z "$UD_CONTENT" ]; then
  echo "  [FAIL] 沒捕捉到 user-data 內容"
  FAIL=$((FAIL + 1))
else
  assert_contains "$UD_CONTENT" "Environment=CACHE_BACKEND=dynamodb" "user-data: trustforge.service 有 CACHE_BACKEND"
  assert_contains "$UD_CONTENT" "Environment=TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache" "user-data: trustforge.service 有 TRUSTFORGE_CACHE_TABLE"
  assert_contains "$UD_CONTENT" "Environment=TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger" "user-data: trustforge.service 有 TRUSTFORGE_COST_LEDGER_TABLE"
  assert_contains "$UD_CONTENT" "Environment=COST_LEDGER_BACKEND=dynamodb" "user-data: trustforge.service 有 COST_LEDGER_BACKEND"
  assert_contains "$UD_CONTENT" "Environment=TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND=dynamodb" "user-data: trustforge.service 有 shared lease backend"
  assert_contains "$UD_CONTENT" "Environment=TRUSTFORGE_LEASE_TABLE=trustforge-analyze-leases" "user-data: trustforge.service 有 shared lease table"
  assert_contains "$UD_CONTENT" "fetch-scheduler.service" "user-data: 有寫 fetch-scheduler.service"
  assert_contains "$UD_CONTENT" "fetch-scheduler.timer" "user-data: 有寫 fetch-scheduler.timer"
  assert_contains "$UD_CONTENT" "ExecStart=/usr/bin/python3 scripts/fetch_scheduler.py" "user-data: fetch-scheduler ExecStart 正確"
  assert_contains "$UD_CONTENT" "OnUnitActiveSec=15min" "user-data: timer 週期 15min"
  assert_contains "$UD_CONTENT" "systemctl enable --now fetch-scheduler.timer" "user-data: 有 enable --now timer"
  # BEDROCK_MODEL_ID 仍走 \${VAR-} fail-safe，離線測試環境未設 → 應為空
  assert_contains "$UD_CONTENT" "Environment=BEDROCK_MODEL_ID=" "user-data: BEDROCK_MODEL_ID 行仍存在（fail-safe 未動）"
  # 管理控制台 PR-5 fail-closed：三個 admin env 未設時「不寫該行」（不是寫
  # 空值行）——admin/live 面在 web.py 端因 env 缺席而全關。
  assert_not_contains "$UD_CONTENT" "Environment=TRUSTFORGE_ADMIN_TOKEN" "user-data: 未設 TRUSTFORGE_ADMIN_TOKEN → 不寫該行（fail-closed）"
  assert_not_contains "$UD_CONTENT" "Environment=TRUSTFORGE_LIVE_TOKEN" "user-data: 未設 TRUSTFORGE_LIVE_TOKEN → 不寫該行（fail-closed）"
  assert_not_contains "$UD_CONTENT" "Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP" "user-data: 未設 TRUSTFORGE_BEDROCK_DAILY_USD_CAP → 不寫該行（吃 budget_guard DEFAULT）"
  # PR-B：TRUSTFORGE_TOKEN_SSM_PREFIX 是新的 opt-in 旗標，未設時比照既有
  # ${VAR-} 慣例不寫該行（app 端 fail back 到 env-based token，零設定不變式）。
  assert_not_contains "$UD_CONTENT" "Environment=TRUSTFORGE_TOKEN_SSM_PREFIX" "user-data: 未設 TRUSTFORGE_TOKEN_SSM_PREFIX → 不寫該行（app 端 fallback env）"
fi

# zip 內容檢查（scripts/ 是否有打包進去，否則 timer 在 EC2 上找不到檔案）
ZIP="$REPO_ROOT/build/trustforge_app.zip"
assert_zip_contains "$ZIP" "scripts/fetch_scheduler.py" "zip 封包含 scripts/fetch_scheduler.py"
assert_zip_contains "$ZIP" "trustforge/web.py" "zip 封包仍含 trustforge/（既有回歸）"

# IAM DynamoDB reconcile：首次建置這條路徑也要在 instance 分支之前跑過
# put-role-policy，鎖兩個 table 各自的 ARN（不給 Resource "*"）。
DDB_POLICY_FT=$(cat "$CAPTURE/iam_policy_first-time_trustforge-dynamodb.txt" 2>/dev/null || echo "")
if [ -z "$DDB_POLICY_FT" ]; then
  echo "  [FAIL] 首次建置：沒抓到 iam put-role-policy --policy-name trustforge-dynamodb"
  FAIL=$((FAIL + 1))
else
  assert_contains "$DDB_POLICY_FT" "arn:aws:dynamodb:ap-southeast-2:123456789012:table/trustforge-connector-cache" "首次建置：DynamoDB policy 鎖 cache table ARN"
  assert_contains "$DDB_POLICY_FT" "arn:aws:dynamodb:ap-southeast-2:123456789012:table/trustforge-cost-ledger" "首次建置：DynamoDB policy 鎖 cost-ledger table ARN"
  assert_contains "$DDB_POLICY_FT" "dynamodb:PutItem" "首次建置：DynamoDB policy 含 PutItem"
  # codex HIGH：probe 的 get_canary() 對 cost-ledger 做強一致 GetItem，若
  # IAM 只放行 PutItem+Scan，真部署會 AccessDenied、每次都失敗——結構化
  # 解析 statement，斷言兩個表的 Action 集合「恰好」符合 probe 實際需要的
  # 權限（不是只肉眼 grep 有沒有出現某個字串）。
  assert_ddb_action_set \
    "首次建置：cost-ledger statement 同時具 PutItem + GetItem（probe get_canary 強一致讀回）+ Scan（probe_scan_permission）" \
    "$CAPTURE/iam_policy_first-time_trustforge-dynamodb.txt" "trustforge-cost-ledger" \
    "dynamodb:GetItem,dynamodb:PutItem,dynamodb:Scan"
  assert_ddb_action_set \
    "首次建置：cache statement 具 GetItem/PutItem/Scan/Query（維持原有最小權限，不受本次 ledger 修改波及）" \
    "$CAPTURE/iam_policy_first-time_trustforge-dynamodb.txt" "trustforge-connector-cache" \
    "dynamodb:GetItem,dynamodb:PutItem,dynamodb:Scan,dynamodb:Query"
fi

# codex HIGH（PR #99）：trustforge-inline（Bedrock）policy 曾經只在「IAM 角色
# 本來不存在」那個分支才會 put-role-policy，導致 update-in-place（角色已存在，
# 跳過該分支）部署到既有實例時，收斂後的 region 白名單永遠到不了、舊的
# wildcard 權限會一直留著。修復後 trustforge-inline 跟 dynamodb policy 一樣，
# 每次部署都無條件 reconcile（put-role-policy 覆寫同名 policy）——這裡跟
# first-time 一起斷言首次建置路徑也確實會 put 到收斂後的白名單版本。
BEDROCK_POLICY_FT=$(cat "$CAPTURE/iam_policy_first-time_trustforge-inline.txt" 2>/dev/null || echo "")
if [ -z "$BEDROCK_POLICY_FT" ]; then
  echo "  [FAIL] 首次建置：沒抓到 iam put-role-policy --policy-name trustforge-inline"
  FAIL=$((FAIL + 1))
else
  assert_contains "$BEDROCK_POLICY_FT" "arn:aws:bedrock:ap-southeast-2::foundation-model/anthropic.*" "首次建置：Bedrock policy 含 ap-southeast-2 白名單"
  if grep -qF "arn:aws:bedrock:*" "$CAPTURE/iam_policy_first-time_trustforge-inline.txt"; then
    echo "  [FAIL] 首次建置：Bedrock policy 仍殘留 region 萬用字元 arn:aws:bedrock:*"
    FAIL=$((FAIL + 1))
  else
    echo "  [PASS] 首次建置：Bedrock policy 沒有 region 萬用字元"
    PASS=$((PASS + 1))
  fi
fi

# codex HIGH（首次建置缺 web healthz gate）：首次建置路徑現在會送 2 次 ssm
# send-command——call1 是新加的 verify_web_healthz（web 服務本身健康），
# call2 才是 verify_fetch_scheduler（DynamoDB probe）。
VERIFY_FT_HEALTHZ=$(cat "$(find_ssm_call_by_marker 'curl -fsS http://localhost/healthz')" 2>/dev/null || echo "")
if [ -z "$VERIFY_FT_HEALTHZ" ]; then
  echo "  [FAIL] 首次建置：沒捕捉到 web healthz 同步驗證的 ssm send-command"
  FAIL=$((FAIL + 1))
else
  assert_contains "$VERIFY_FT_HEALTHZ" "systemctl is-active --quiet trustforge" "首次建置：web healthz gate 有檢查 trustforge.service is-active"
  assert_contains "$VERIFY_FT_HEALTHZ" "curl -fsS http://localhost/healthz" "首次建置：web healthz gate 有 curl /healthz"
  assert_contains "$VERIFY_FT_HEALTHZ" "healthz 檢查失敗" "首次建置：web healthz gate 失敗時有印訊息"
  assert_contains "$VERIFY_FT_HEALTHZ" "journalctl -u trustforge" "首次建置：web healthz gate 失敗時有印 trustforge journal"
  assert_healthz_gate_behavior \
    "首次建置：web healthz 失敗（模擬 systemctl is-active/curl 失敗）→ gate 判定失敗（exit1），獨立於 probe 是否會過" \
    "$VERIFY_FT_HEALTHZ" 1 1 1
  assert_healthz_gate_behavior \
    "首次建置：web healthz 通過（systemctl is-active + curl 都成功）→ gate 判定成功（exit0）" \
    "$VERIFY_FT_HEALTHZ" 0 0 0
fi

VERIFY_FT_SEED=$(cat "$(find_ssm_call_by_marker 'systemctl start fetch-scheduler.service')" 2>/dev/null || echo "")
VERIFY_FT=$(cat "$(find_ssm_call_by_marker 'fetch_scheduler.py --probe')" 2>/dev/null || echo "")
if [ -z "$VERIFY_FT" ]; then
  echo "  [FAIL] 首次建置：沒捕捉到 fetch-scheduler --probe 同步驗證的 ssm send-command"
  FAIL=$((FAIL + 1))
else
  assert_contains "$VERIFY_FT_SEED" "systemctl start fetch-scheduler.service" "首次建置：同步觸發 systemctl start fetch-scheduler.service（best-effort seed，fire-and-forget）"
  assert_contains "$VERIFY_FT_SEED" "journalctl -u fetch-scheduler" "首次建置：seed 失敗時有印 journal"
  # codex HIGH-3：不能只靠一般排程（可能因 cache 全新鮮 0 次真呼叫仍成功）
  # 當成 R/W 驗證，必須真的另外跑一次不依賴 freshness 的 --probe。
  assert_contains "$VERIFY_FT" "fetch_scheduler.py --probe" "首次建置：有另外跑 fetch_scheduler.py --probe（不只靠 freshness-skip 的一般排程）"
  assert_contains "$VERIFY_FT" "TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache" "首次建置：probe 呼叫有帶正確的 cache table 環境變數"
  assert_contains "$VERIFY_FT" "TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger" "首次建置：probe 呼叫有帶正確的 cost-ledger table 環境變數"
  # follow-up（真部署發現）：reddit 在 EC2 共享 IP 上必定 429，一般全源排程
  # 因此必定非零，但這不代表基建有問題——部署 gate 只該認 --probe。
  assert_verify_gate_behavior \
    "首次建置：一般排程失敗（模擬 reddit 429）但 --probe 通過 → gate 仍判定成功（exit0，不再 false-fail）" \
    "$VERIFY_FT" 1 0 0
  assert_verify_gate_behavior \
    "首次建置：--probe 失敗（模擬 DynamoDB 被拒）→ gate 判定失敗（exit1），與一般排程是否成功無關" \
    "$VERIFY_FT" 0 1 1
fi

echo
echo "== 場景 1b：首次建置 + 帶 TRUSTFORGE_BEDROCK_DAILY_USD_CAP + TRUSTFORGE_TOKEN_SSM_PREFIX → ExecStart 完整性守門（assert_unit_env_lines）=="

if run_deploy "first-time-cap-prefix" TRUSTFORGE_BEDROCK_DAILY_USD_CAP=2.5 TRUSTFORGE_TOKEN_SSM_PREFIX=/trustforge/runtime; then
  echo "  [INFO] first-time-cap-prefix 首次建置部署成功，開始驗證 user-data 內 ExecStart 完整性"
else
  echo "  [FAIL] first-time-cap-prefix deploy 回傳非零 exit code，深入原因待查"
  cat "$CAPTURE/stdout_first-time-cap-prefix.log"
  FAIL=$((FAIL + 1))
fi

assert_unit_env_lines \
  "CAP+PREFIX 都有值時，兩行 Environment 各自獨立存在、且 ExecStart 沒被拼黏壞" \
  "$CAPTURE/user_data.sh" \
  "Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=2.5;Environment=TRUSTFORGE_TOKEN_SSM_PREFIX=/trustforge/runtime"

UD_CONTENT_1B="$(cat "$CAPTURE/user_data.sh")"

assert_contains "$UD_CONTENT_1B" "ExecStart=/usr/bin/python3 -m trustforge.web" "user-data 含乾淨獨立的 ExecStart 行"

assert_not_contains "$UD_CONTENT_1B" "TRUSTFORGE_TOKEN_SSM_PREFIX=/trustforge/runtimeExecStart" "user-data 不含 PREFIX 與 ExecStart 黏在一起的壞字串"

echo
echo "== 場景 2：既有實例 running → update-in-place（含 codex HIGH #99 回歸：舊 wildcard policy 會被覆寫）=="
# codex HIGH（PR #99）：模擬「角色已存在、trustforge-inline 還停留在收斂前的
# wildcard 版本」——這是真實世界最常見的狀態（角色是舊版腳本建的）。修復前
# put-role-policy trustforge-inline 只在 get-role 失敗（角色不存在）分支才會
# 呼叫，update-in-place 走的是角色已存在路徑，永遠不會 reconcile，這份舊檔
# 會原封不動留著。先手動寫入這份「假舊 policy」，deploy 跑完後斷言檔案內容
# 已被收斂後的白名單版本覆寫（而不是沒被呼叫、殘留原封不動的舊內容）。
mkdir -p "$CAPTURE"
cat > "$CAPTURE/iam_policy_update-in-place_trustforge-inline.txt" <<'OLDPOLICY'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"bedrock:InvokeModel","Resource":["arn:aws:bedrock:*::foundation-model/anthropic.*","arn:aws:bedrock:*:*:inference-profile/*anthropic*"]},{"Effect":"Allow","Action":"s3:GetObject","Resource":"arn:aws:s3:::dummy/*"}]}
OLDPOLICY
if run_deploy "update-in-place"; then
  echo "  deploy_ec2.sh 執行成功（exit 0）"
else
  echo "  [FAIL] deploy_ec2.sh update-in-place 場景非零結束"
  cat "$CAPTURE/stdout_update-in-place.log"
  FAIL=$((FAIL + 1))
fi

# codex HIGH（PR #99）：驗證上面手動寫入的「舊 wildcard policy」確實被這次
# 部署的 put-role-policy 覆寫掉——若修復失效（trustforge-inline 又縮回只在
# 建角色分支才呼叫），這份檔案會維持 OLDPOLICY 內容原封不動，下面兩個斷言
# 就會失敗。
BEDROCK_POLICY_UP=$(cat "$CAPTURE/iam_policy_update-in-place_trustforge-inline.txt" 2>/dev/null || echo "")
if [ -z "$BEDROCK_POLICY_UP" ]; then
  echo "  [FAIL] update-in-place：沒抓到 iam put-role-policy --policy-name trustforge-inline"
  FAIL=$((FAIL + 1))
else
  if grep -qF "arn:aws:bedrock:*" "$CAPTURE/iam_policy_update-in-place_trustforge-inline.txt"; then
    echo "  [FAIL] update-in-place：Bedrock policy 仍是舊 wildcard 版本，沒被覆寫（codex HIGH 回歸）"
    FAIL=$((FAIL + 1))
  else
    echo "  [PASS] update-in-place：舊 wildcard Bedrock policy 已被收斂後的白名單版本覆寫"
    PASS=$((PASS + 1))
  fi
  assert_contains "$BEDROCK_POLICY_UP" "arn:aws:bedrock:ap-southeast-2::foundation-model/anthropic.*" "update-in-place：覆寫後的 Bedrock policy 含 ap-southeast-2 白名單"
  assert_contains "$BEDROCK_POLICY_UP" "arn:aws:bedrock:ap-southeast-2:123456789012:inference-profile/*anthropic*" "update-in-place：覆寫後的 inference-profile ARN 收斂到 \$REGION:\$ACCT"
fi

# IAM DynamoDB reconcile：update-in-place 這條路徑也要在 instance 分支之前
# 跑過 put-role-policy（跟首次建置共用同一段程式碼，位置在分支判斷之前）。
DDB_POLICY_UP=$(cat "$CAPTURE/iam_policy_update-in-place_trustforge-dynamodb.txt" 2>/dev/null || echo "")
if [ -z "$DDB_POLICY_UP" ]; then
  echo "  [FAIL] update-in-place：沒抓到 iam put-role-policy --policy-name trustforge-dynamodb"
  FAIL=$((FAIL + 1))
else
  assert_contains "$DDB_POLICY_UP" "arn:aws:dynamodb:ap-southeast-2:123456789012:table/trustforge-connector-cache" "update-in-place：DynamoDB policy 鎖 cache table ARN"
  assert_contains "$DDB_POLICY_UP" "arn:aws:dynamodb:ap-southeast-2:123456789012:table/trustforge-cost-ledger" "update-in-place：DynamoDB policy 鎖 cost-ledger table ARN"
  assert_ddb_action_set \
    "update-in-place：cost-ledger statement 同時具 PutItem + GetItem（probe get_canary 強一致讀回）+ Scan（probe_scan_permission）" \
    "$CAPTURE/iam_policy_update-in-place_trustforge-dynamodb.txt" "trustforge-cost-ledger" \
    "dynamodb:GetItem,dynamodb:PutItem,dynamodb:Scan"
  assert_ddb_action_set \
    "update-in-place：cache statement 具 GetItem/PutItem/Scan/Query" \
    "$CAPTURE/iam_policy_update-in-place_trustforge-dynamodb.txt" "trustforge-connector-cache" \
    "dynamodb:GetItem,dynamodb:PutItem,dynamodb:Scan,dynamodb:Query"
fi

# 排程 fetcher 同步驗證：update-in-place 這條路徑主設定 SSM 成功後，還會
# 再送第二次 send-command（call2）同步跑 fetch-scheduler 驗證。
VERIFY_UP_SEED=$(cat "$(find_ssm_call_by_marker 'systemctl start fetch-scheduler.service')" 2>/dev/null || echo "")
VERIFY_UP=$(cat "$(find_ssm_call_by_marker 'fetch_scheduler.py --probe')" 2>/dev/null || echo "")
if [ -z "$VERIFY_UP" ]; then
  echo "  [FAIL] update-in-place：沒捕捉到 fetch-scheduler --probe 同步驗證的 ssm send-command"
  FAIL=$((FAIL + 1))
else
  assert_contains "$VERIFY_UP_SEED" "systemctl start fetch-scheduler.service" "update-in-place：主設定成功後同步觸發 systemctl start fetch-scheduler.service（best-effort seed，fire-and-forget）"
  assert_contains "$VERIFY_UP" "fetch_scheduler.py --probe" "update-in-place：有另外跑 fetch_scheduler.py --probe（不只靠 freshness-skip 的一般排程）"
  assert_verify_gate_behavior \
    "update-in-place：一般排程失敗（模擬 reddit 429）但 --probe 通過 → gate 仍判定成功（exit0，不再 false-fail）" \
    "$VERIFY_UP" 1 0 0
  assert_verify_gate_behavior \
    "update-in-place：--probe 失敗（模擬 DynamoDB 被拒）→ gate 判定失敗（exit1），與一般排程是否成功無關" \
    "$VERIFY_UP" 0 1 1
fi

SSM_RAW=$(cat "$CAPTURE/ssm_params_call1.txt" 2>/dev/null || echo "")
if [ -z "$SSM_RAW" ]; then
  echo "  [FAIL] 沒捕捉到 SSM send-command 的 --parameters"
  FAIL=$((FAIL + 1))
else
  # --parameters 值是 commands='[...]'，取出 JSON 陣列部分再用 python3 解 JSON，
  # 確認整段 JSON 合法、且解出的每一行拼起來是合法 bash（不是只肉眼看字串像）。
  SSM_JSON="${SSM_RAW#commands=}"
  echo "$SSM_JSON" > "$CAPTURE/ssm.json"
  if python3 -c "
import json, sys
with open('$CAPTURE/ssm.json') as f:
    raw = f.read()
cmds = json.loads(raw)
assert isinstance(cmds, list) and len(cmds) > 5, 'commands 陣列太短或格式不對'
script = chr(10).join(cmds)
with open('$CAPTURE/remote_script.sh', 'w') as f:
    f.write(script)
" 2>"$CAPTURE/json_err.txt"; then
    echo "  [PASS] SSM commands 是合法 JSON 陣列"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] SSM commands JSON 解析失敗："
    cat "$CAPTURE/json_err.txt"
    FAIL=$((FAIL + 1))
  fi

  if bash -n "$CAPTURE/remote_script.sh" 2>"$CAPTURE/bashn_err.txt"; then
    echo "  [PASS] 還原出的遠端腳本 bash -n 語法合法"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] 還原出的遠端腳本語法錯誤："
    cat "$CAPTURE/bashn_err.txt"
    FAIL=$((FAIL + 1))
  fi

  REMOTE=$(cat "$CAPTURE/remote_script.sh" 2>/dev/null || echo "")
  assert_contains "$REMOTE" 'Environment=BEDROCK_MODEL_ID=' "update-in-place: 仍保留 BEDROCK_MODEL_ID sed（既有邏輯不動）"
  assert_contains "$REMOTE" 'Environment=CACHE_BACKEND=dynamodb' "update-in-place: 補 CACHE_BACKEND"
  assert_contains "$REMOTE" 'Environment=TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache' "update-in-place: 補 TRUSTFORGE_CACHE_TABLE"
  assert_contains "$REMOTE" 'Environment=TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger' "update-in-place: 補 TRUSTFORGE_COST_LEDGER_TABLE"
  assert_contains "$REMOTE" 'Environment=COST_LEDGER_BACKEND=dynamodb' "update-in-place: 補 COST_LEDGER_BACKEND"
  assert_contains "$REMOTE" 'Environment=TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND=dynamodb' "update-in-place: 補 shared lease backend"
  assert_contains "$REMOTE" 'Environment=TRUSTFORGE_LEASE_TABLE=trustforge-analyze-leases' "update-in-place: 補 shared lease table"
  assert_contains "$REMOTE" 'bash deploy/install_hermes_scheduler.sh' "update-in-place: 呼叫 Hermes scheduler installer"
  assert_contains "$REMOTE" 'systemctl enable --now fetch-scheduler.timer' "update-in-place: 確認 timer enabled"
  # PR-B（#119 退場遷移清理）：ADMIN_TOKEN/LIVE_TOKEN 這兩個 key deploy 已經
  # 完全不接受值了，unconditional 傳空字串給 ssm_env_cmd 只是為了刪掉舊機制
  # 殘留的該行（這才是本 PR 的安全價值：token 離開 unit 檔落點）；CAP 才是
  # 真正有「未設」概念的 opt-in 欄位。
  assert_contains "$REMOTE" 'sed -i "/^Environment=TRUSTFORGE_ADMIN_TOKEN=/d" /etc/systemd/system/trustforge.service' "update-in-place: PR-B 遷移：unconditional 刪除 #119 殘留行 TRUSTFORGE_ADMIN_TOKEN（deploy 已不接受此值）"
  assert_contains "$REMOTE" 'sed -i "/^Environment=TRUSTFORGE_LIVE_TOKEN=/d" /etc/systemd/system/trustforge.service' "update-in-place: PR-B 遷移：unconditional 刪除 #119 殘留行 TRUSTFORGE_LIVE_TOKEN（deploy 已不接受此值）"
  assert_contains "$REMOTE" 'sed -i "/^Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=/d" /etc/systemd/system/trustforge.service' "update-in-place: 未設 DAILY_USD_CAP → 刪除殘留行（回到 config/DEFAULT 層）"
  assert_contains "$REMOTE" 'sed -i "/^Environment=TRUSTFORGE_TOKEN_SSM_PREFIX=/d" /etc/systemd/system/trustforge.service' "update-in-place: 未設 TOKEN_SSM_PREFIX → 刪除殘留行（app 端 fallback env）"

  # 功能性驗證：4 個 ensure-env 邏輯對「舊實例（完全沒有這些行）」是插入、
  # 對「已經有（值不同）」是取代，且冪等不重複——用真的 GNU sed 語意跑一次
  # （若本機是 BSD sed 會跳過，改用 sed --version 偵測是否為 GNU sed）。
  # 遠端 EC2（Amazon Linux）用 GNU sed；本機若是 macOS 預設 BSD sed 對 `a`
  # 插入指令語法不同，先找 homebrew gnu-sed（或既有的 gsed）矯正 PATH，
  # 讓這段功能性驗證盡量貼近遠端真實行為，而不是只驗 JSON/語法過關。
  USE_GNU_SED=0
  if sed --version >/dev/null 2>&1; then
    USE_GNU_SED=1
  elif [ -x /opt/homebrew/opt/gnu-sed/libexec/gnubin/sed ]; then
    export PATH="/opt/homebrew/opt/gnu-sed/libexec/gnubin:$PATH"
    USE_GNU_SED=1
  elif command -v gsed >/dev/null 2>&1; then
    ln -sf "$(command -v gsed)" "$MOCKDIR/sed"
    export PATH="$MOCKDIR:$PATH"
    USE_GNU_SED=1
  fi
  if [ "$USE_GNU_SED" = "1" ]; then
    FAKE_UNIT=$(mktemp)
    # PR-B（#119 退場）：預埋四行「上次部署殘留的」admin env / runtime 旗標
    # （stale 值）——其中 ADMIN_TOKEN/LIVE_TOKEN 是 #119 時代留下的歷史殘留行
    # （這次 deploy 已不接受這些值），CAP 與 TOKEN_SSM_PREFIX 是 opt-in 欄位
    # 未設的情境，驗證這次部署會把它們整行刪掉，不是留著繼續有效。
    cat > "$FAKE_UNIT" <<'UNITEOF'
[Unit]
Description=TrustForge web
After=network.target
[Service]
Environment=PORT=80
Environment=TRUSTFORGE_HOME=/opt/trustforge
Environment=AWS_REGION=ap-southeast-2
Environment=BEDROCK_MODEL_ID=
Environment=PYTHONPATH=/opt/trustforge
Environment=TRUSTFORGE_ADMIN_TOKEN=stale-admin-token
Environment=TRUSTFORGE_LIVE_TOKEN=stale-live-token
Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=99
Environment=TRUSTFORGE_TOKEN_SSM_PREFIX=stale-prefix-OLD
ExecStart=/usr/bin/python3 -m trustforge.web
Restart=always
[Install]
WantedBy=multi-user.target
UNITEOF
    ENSURE_LINES=$(grep -n '^if grep -q' "$CAPTURE/remote_script.sh" | cut -d: -f1)
    PATCHED=$(sed "s#/etc/systemd/system/trustforge.service#$FAKE_UNIT#g" "$CAPTURE/remote_script.sh")
    for lineno in $ENSURE_LINES; do
      LINE=$(printf '%s\n' "$PATCHED" | sed -n "${lineno}p")
      bash -c "$LINE"
      bash -c "$LINE"  # 跑兩次驗證冪等
    done
    # 刪除行（未設 admin env 的 fail-closed 分支）也真的跑一遍
    DELETE_LINES=$(grep -n '^sed -i "/^Environment=TRUSTFORGE_' "$CAPTURE/remote_script.sh" | cut -d: -f1)
    for lineno in $DELETE_LINES; do
      LINE=$(printf '%s\n' "$PATCHED" | sed -n "${lineno}p")
      bash -c "$LINE"
      bash -c "$LINE"  # 跑兩次驗證冪等（第二次刪不存在的行也不能爆）
    done
    DUP_OK=1
    for key in CACHE_BACKEND TRUSTFORGE_CACHE_TABLE TRUSTFORGE_COST_LEDGER_TABLE COST_LEDGER_BACKEND; do
      COUNT=$(grep -c "^Environment=$key=" "$FAKE_UNIT")
      if [ "$COUNT" != "1" ]; then
        echo "  [FAIL] update-in-place ensure-env 對舊實例套用後 $key 出現 $COUNT 次（應為 1，不冪等或沒插入）"
        DUP_OK=0
      fi
    done
    for key in TRUSTFORGE_ADMIN_TOKEN TRUSTFORGE_LIVE_TOKEN TRUSTFORGE_BEDROCK_DAILY_USD_CAP TRUSTFORGE_TOKEN_SSM_PREFIX; do
      if grep -q "^Environment=$key=" "$FAKE_UNIT"; then
        echo "  [FAIL] update-in-place 未設 $key，但殘留的舊行沒被刪掉（PR-B 遷移/opt-in 未設應清除）"
        DUP_OK=0
      fi
    done
    if [ "$DUP_OK" = "1" ]; then
      echo "  [PASS] update-in-place ensure-env 插入正確、重跑冪等不重複；未設 admin env 的殘留行真的被刪掉（fail-closed）"
      PASS=$((PASS + 1))
    else
      FAIL=$((FAIL + 1))
    fi
    rm -f "$FAKE_UNIT"
  else
    echo "  [SKIP] 本機 sed 非 GNU sed（macOS 內建 BSD sed 對 'a' 指令語法不同），
          略過 ensure-env 實跑驗證，已用 CI/EC2 實際跑的 GNU sed 4.10 (Homebrew gnu-sed) 驗過"
  fi
fi

echo

echo "== 場景 2b：既有實例 update-in-place + 設定 TRUSTFORGE_TOKEN_SSM_PREFIX → ensure/替換分支真實跑（PR-B 新旗標）=="

if run_deploy "update-in-place-token-prefix" TRUSTFORGE_TOKEN_SSM_PREFIX=/trustforge/runtime; then
  echo "  deploy_ec2.sh 執行成功（exit 0）"
else
  echo "  [FAIL] deploy_ec2.sh update-in-place + TOKEN_SSM_PREFIX 場景非零結束"
  cat "$CAPTURE/stdout_update-in-place-token-prefix.log"
  FAIL=$((FAIL + 1))
fi

SSM_RAW_PREFIX=$(cat "$CAPTURE/ssm_params_call1.txt" 2>/dev/null || echo "")
if [ -z "$SSM_RAW_PREFIX" ]; then
  echo "  [FAIL] 沒捕捉到 SSM send-command 的 --parameters"
  FAIL=$((FAIL + 1))
else
  # --parameters 值是 commands='[...]'，取出 JSON 陣列部分再用 python3 解 JSON，
  # 確認整段 JSON 合法、且解出的每一行拼起來是合法 bash（不是只肉眼看字串像）。
  # 用獨立檔名 ssm_prefix.json / remote_script_prefix.sh 避免覆蓋場景 2 的產物。
  SSM_PREFIX_JSON="${SSM_RAW_PREFIX#commands=}"
  echo "$SSM_PREFIX_JSON" > "$CAPTURE/ssm_prefix.json"
  if python3 -c "
import json, sys
with open('$CAPTURE/ssm_prefix.json') as f:
    raw = f.read()
cmds = json.loads(raw)
assert isinstance(cmds, list) and len(cmds) > 5, 'commands 陣列太短或格式不對'
script = chr(10).join(cmds)
with open('$CAPTURE/remote_script_prefix.sh', 'w') as f:
    f.write(script)
" 2>"$CAPTURE/json_err_prefix.txt"; then
    echo "  [PASS] SSM commands 是合法 JSON 陣列"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] SSM commands JSON 解析失敗："
    cat "$CAPTURE/json_err_prefix.txt"
    FAIL=$((FAIL + 1))
  fi

  if bash -n "$CAPTURE/remote_script_prefix.sh" 2>"$CAPTURE/bashn_err_prefix.txt"; then
    echo "  [PASS] 還原出的遠端腳本 bash -n 語法合法"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] 還原出的遠端腳本語法錯誤："
    cat "$CAPTURE/bashn_err_prefix.txt"
    FAIL=$((FAIL + 1))
  fi

  REMOTE_PREFIX=$(cat "$CAPTURE/remote_script_prefix.sh" 2>/dev/null || echo "")
  # TOKEN_SSM_PREFIX 是非機敏 opt-in 旗標，走明文 sed 取代（跟 CAP 一樣不經
  # SSM 變數間接層）；這裡斷言 ensure 片段含目標值 + service 路徑。
  assert_contains "$REMOTE_PREFIX" 'Environment=TRUSTFORGE_TOKEN_SSM_PREFIX=/trustforge/runtime|" /etc/systemd/system/trustforge.service' "update-in-place + TOKEN_SSM_PREFIX：ensure 片段明文 sed 取代（非機敏值，不經 SSM 變數間接層）"
  # 有設值時，遠端腳本不應再出現該 key 的整行刪除指令（刪除分支只在 val 為空時走）
  if printf '%s\n' "$REMOTE_PREFIX" | grep -qF 'sed -i "/^Environment=TRUSTFORGE_TOKEN_SSM_PREFIX=/d"'; then
    echo "  [FAIL] update-in-place + TOKEN_SSM_PREFIX：有設值卻仍出現刪除行（應走 ensure 取代分支）"
    FAIL=$((FAIL + 1))
  else
    echo "  [PASS] update-in-place + TOKEN_SSM_PREFIX：有設值，不含該 key 的刪除行"
    PASS=$((PASS + 1))
  fi

  # 沿用場景 2 已偵測好的 $USE_GNU_SED，不重複偵測；真實 GNU sed 跑 ensure
  # 指令兩次驗證冪等：stale 值被取代成 /trustforge/runtime，且只出現 1 次。
  if [ "$USE_GNU_SED" = "1" ]; then
    FAKE_UNIT2=$(mktemp)
    # 精簡 unit：只需 PYTHONPATH 那行（給 ensure 的 else-branch「插入到
    # PYTHONPATH 後」fallback 用）+ stale TOKEN_SSM_PREFIX 一行即可。
    cat > "$FAKE_UNIT2" <<'UNITEOF2'
[Unit]
Description=TrustForge web
[Service]
Environment=PYTHONPATH=/opt/trustforge
Environment=TRUSTFORGE_TOKEN_SSM_PREFIX=old-stale-prefix
ExecStart=/usr/bin/python3 -m trustforge.web
[Install]
WantedBy=multi-user.target
UNITEOF2
    ENSURE_LINES_PREFIX=$(grep -n '^if grep -q' "$CAPTURE/remote_script_prefix.sh" | cut -d: -f1)
    PATCHED_PREFIX=$(sed "s#/etc/systemd/system/trustforge.service#$FAKE_UNIT2#g" "$CAPTURE/remote_script_prefix.sh")
    for lineno in $ENSURE_LINES_PREFIX; do
      LINE=$(printf '%s\n' "$PATCHED_PREFIX" | sed -n "${lineno}p")
      bash -c "$LINE"
      bash -c "$LINE"  # 跑兩次驗證冪等
    done
    PREFIX_OK=1
    COUNT=$(grep -c "^Environment=TRUSTFORGE_TOKEN_SSM_PREFIX=" "$FAKE_UNIT2")
    if [ "$COUNT" != "1" ]; then
      echo "  [FAIL] update-in-place + TOKEN_SSM_PREFIX：套用後該 key 出現 $COUNT 次（應為 1，不冪等或沒取代）"
      PREFIX_OK=0
    fi
    if ! grep -q "^Environment=TRUSTFORGE_TOKEN_SSM_PREFIX=/trustforge/runtime$" "$FAKE_UNIT2"; then
      echo "  [FAIL] update-in-place + TOKEN_SSM_PREFIX：stale 值沒被取代成 /trustforge/runtime"
      PREFIX_OK=0
    fi
    if [ "$PREFIX_OK" = "1" ]; then
      echo "  [PASS] update-in-place + TOKEN_SSM_PREFIX：stale 值被正確取代成 /trustforge/runtime，重跑冪等只出現 1 次"
      PASS=$((PASS + 1))
    else
      FAIL=$((FAIL + 1))
    fi
    rm -f "$FAKE_UNIT2"
  else
    echo "  [SKIP] 本機 sed 非 GNU sed（macOS 內建 BSD sed 對 'a' 指令語法不同），
          略過 TOKEN_SSM_PREFIX ensure 實跑驗證，已用 CI/EC2 實際跑的 GNU sed 4.10 (Homebrew gnu-sed) 驗過"
  fi
fi

echo
echo "== 場景 2c：既有實例 update-in-place + 設定 TRUSTFORGE_BEDROCK_DAILY_USD_CAP → ensure/取代分支真實跑（CAP 正值路徑，vp-eng 補測）=="

if run_deploy "update-in-place-cap" TRUSTFORGE_BEDROCK_DAILY_USD_CAP=3.5; then
  echo "  deploy_ec2.sh 執行成功（exit 0）"
else
  echo "  [FAIL] deploy_ec2.sh update-in-place + CAP 場景非零結束"
  cat "$CAPTURE/stdout_update-in-place-cap.log"
  FAIL=$((FAIL + 1))
fi

SSM_RAW_CAP=$(cat "$CAPTURE/ssm_params_call1.txt" 2>/dev/null || echo "")
if [ -z "$SSM_RAW_CAP" ]; then
  echo "  [FAIL] 沒捕捉到 SSM send-command 的 --parameters"
  FAIL=$((FAIL + 1))
else
  SSM_CAP_JSON="${SSM_RAW_CAP#commands=}"
  echo "$SSM_CAP_JSON" > "$CAPTURE/ssm_cap.json"
  if python3 -c "
import json, sys
with open('$CAPTURE/ssm_cap.json') as f:
    raw = f.read()
cmds = json.loads(raw)
assert isinstance(cmds, list) and len(cmds) > 5, 'commands 陣列太短或格式不對'
script = chr(10).join(cmds)
with open('$CAPTURE/remote_script_cap.sh', 'w') as f:
    f.write(script)
" 2>"$CAPTURE/json_err_cap.txt"; then
    echo "  [PASS] SSM commands 是合法 JSON 陣列"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] SSM commands JSON 解析失敗："
    cat "$CAPTURE/json_err_cap.txt"
    FAIL=$((FAIL + 1))
  fi

  if bash -n "$CAPTURE/remote_script_cap.sh" 2>"$CAPTURE/bashn_err_cap.txt"; then
    echo "  [PASS] 還原出的遠端腳本 bash -n 語法合法"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] 還原出的遠端腳本語法錯誤："
    cat "$CAPTURE/bashn_err_cap.txt"
    FAIL=$((FAIL + 1))
  fi

  REMOTE_CAP=$(cat "$CAPTURE/remote_script_cap.sh" 2>/dev/null || echo "")
  assert_contains "$REMOTE_CAP" 'Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=3.5|" /etc/systemd/system/trustforge.service' "update-in-place + CAP：ensure 片段明文 sed 取代（非機敏值，不經 SSM 變數間接層）"
  if printf '%s\n' "$REMOTE_CAP" | grep -qF 'sed -i "/^Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=/d"'; then
    echo "  [FAIL] update-in-place + CAP：有設值卻仍出現刪除行（應走 ensure 取代分支）"
    FAIL=$((FAIL + 1))
  else
    echo "  [PASS] update-in-place + CAP：有設值，不含該 key 的刪除行"
    PASS=$((PASS + 1))
  fi

  if [ "$USE_GNU_SED" = "1" ]; then
    ENSURE_LINES_CAP=$(grep -n '^if grep -q' "$CAPTURE/remote_script_cap.sh" | cut -d: -f1)

    FAKE_UNIT_CAP_INSERT=$(mktemp)
    cat > "$FAKE_UNIT_CAP_INSERT" <<'UNITEOF_CAP_INSERT'
[Unit]
Description=TrustForge web
[Service]
Environment=PYTHONPATH=/opt/trustforge
ExecStart=/usr/bin/python3 -m trustforge.web
[Install]
WantedBy=multi-user.target
UNITEOF_CAP_INSERT
    PATCHED_CAP_INS=$(sed "s#/etc/systemd/system/trustforge.service#$FAKE_UNIT_CAP_INSERT#g" "$CAPTURE/remote_script_cap.sh")
    for lineno in $ENSURE_LINES_CAP; do
      LINE=$(printf '%s\n' "$PATCHED_CAP_INS" | sed -n "${lineno}p")
      bash -c "$LINE"
      bash -c "$LINE"
    done
    CAP_INS_OK=1
    COUNT_CAP_INS=$(grep -c "^Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=" "$FAKE_UNIT_CAP_INSERT")
    if [ "$COUNT_CAP_INS" != "1" ]; then
      echo "  [FAIL] update-in-place + CAP（插入情境）：套用後該 key 出現 $COUNT_CAP_INS 次（應為 1，從無到有插入失敗或沒冪等）"
      CAP_INS_OK=0
    fi
    if ! grep -q "^Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=3.5$" "$FAKE_UNIT_CAP_INSERT"; then
      echo "  [FAIL] update-in-place + CAP（插入情境）：值不是 3.5（從無到有插入失敗）"
      CAP_INS_OK=0
    fi
    if [ "$CAP_INS_OK" = "1" ]; then
      echo "  [PASS] update-in-place + CAP（插入情境）：從無到有插入 Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=3.5，重跑冪等只出現 1 次"
      PASS=$((PASS + 1))
    else
      FAIL=$((FAIL + 1))
    fi
    rm -f "$FAKE_UNIT_CAP_INSERT"

    FAKE_UNIT_CAP_REPLACE=$(mktemp)
    cat > "$FAKE_UNIT_CAP_REPLACE" <<'UNITEOF_CAP_REPLACE'
[Unit]
Description=TrustForge web
[Service]
Environment=PYTHONPATH=/opt/trustforge
Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=99
ExecStart=/usr/bin/python3 -m trustforge.web
[Install]
WantedBy=multi-user.target
UNITEOF_CAP_REPLACE
    PATCHED_CAP_REP=$(sed "s#/etc/systemd/system/trustforge.service#$FAKE_UNIT_CAP_REPLACE#g" "$CAPTURE/remote_script_cap.sh")
    for lineno in $ENSURE_LINES_CAP; do
      LINE=$(printf '%s\n' "$PATCHED_CAP_REP" | sed -n "${lineno}p")
      bash -c "$LINE"
      bash -c "$LINE"
    done
    CAP_REP_OK=1
    COUNT_CAP_REP=$(grep -c "^Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=" "$FAKE_UNIT_CAP_REPLACE")
    if [ "$COUNT_CAP_REP" != "1" ]; then
      echo "  [FAIL] update-in-place + CAP（取代情境）：套用後該 key 出現 $COUNT_CAP_REP 次（應為 1，舊值沒被取代或重複插入）"
      CAP_REP_OK=0
    fi
    if ! grep -q "^Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=3.5$" "$FAKE_UNIT_CAP_REPLACE"; then
      echo "  [FAIL] update-in-place + CAP（取代情境）：舊值 99 沒被取代成 3.5"
      CAP_REP_OK=0
    fi
    if [ "$CAP_REP_OK" = "1" ]; then
      echo "  [PASS] update-in-place + CAP（取代情境）：從舊值 99 換成新值 3.5，重跑冪等只出現 1 次"
      PASS=$((PASS + 1))
    else
      FAIL=$((FAIL + 1))
    fi
    rm -f "$FAKE_UNIT_CAP_REPLACE"
  else
    echo "  [SKIP] 本機 sed 非 GNU sed（macOS 內建 BSD sed 對 'a' 指令語法不同），
          略過 CAP ensure 實跑驗證，已用 CI/EC2 實際跑的 GNU sed 4.10 (Homebrew gnu-sed) 驗過"
  fi
fi

echo
echo "== 場景 3：fetch-scheduler 同步驗證失敗（模擬 DynamoDB IAM 權限不足）=="
# 模擬「主設定 SSM 成功，但實際跑 fetch-scheduler 卻失敗」（HIGH 修的核心情境：
# 只 enable timer 不代表真的能寫進 DynamoDB）。mock 讓「含 --probe 標記」的那次
# send-command（無論編號幾）回 Failed，斷言整支 deploy_ec2.sh 必須非零結束、不能誤報成功。
if run_deploy "scheduler-fail"; then
  echo "  [FAIL] fetch-scheduler 驗證失敗時，deploy_ec2.sh 仍回報成功（exit 0）——不可接受"
  FAIL=$((FAIL + 1))
else
  echo "  [PASS] fetch-scheduler 驗證失敗時，deploy_ec2.sh 正確地非零結束"
  PASS=$((PASS + 1))
  if grep -qF "fetch-scheduler probe 同步驗證失敗" "$CAPTURE/stdout_scheduler-fail.log"; then
    echo "  [PASS] 失敗訊息明確（含 fetch-scheduler 同步驗證失敗 字樣）"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] deploy_ec2.sh 非零結束了，但訊息沒有明確指出是 fetch-scheduler 驗證失敗："
    cat "$CAPTURE/stdout_scheduler-fail.log"
    FAIL=$((FAIL + 1))
  fi
fi

# codex HIGH-3：確認會被判定失敗的那次 send-command，內容真的包含 --probe——
# 證明失敗判定是掛在「一般排程 + probe」的組合驗證上，不是舊版只驗
# systemctl start（無參數、可能因 cache 全新鮮而假成功）那條路。
SCHED_FAIL_CONTENT=$(cat "$(find_ssm_call_by_marker 'fetch_scheduler.py --probe')" 2>/dev/null || echo "")
assert_contains "$SCHED_FAIL_CONTENT" "fetch_scheduler.py --probe" "場景 3：判定失敗的那次驗證，內容包含 --probe（不是只靠 freshness-skip 的一般排程）"

echo
echo "== 場景 4：首次建置 web healthz 驗證失敗（codex HIGH，模擬 systemctl/curl 失敗）=="
# 模擬「user-data 建完 scheduler unit 之後，web 服務其實沒起來」（本次修的
# HIGH 核心情境：舊版首次建置只驗 DynamoDB probe，probe 過就報成功，公開
# 服務卻是壞的）。mock 讓 call1（verify_web_healthz）回 Failed，斷言
# 整支 deploy_ec2.sh 必須非零結束、且 --probe 那次 send-command 根本沒被呼叫——
# healthz gate 獨立擋在 probe 之前，不受 probe 會不會過影響。
if run_deploy "healthz-fail"; then
  echo "  [FAIL] web healthz 驗證失敗時，deploy_ec2.sh 仍回報成功（exit 0）——不可接受"
  FAIL=$((FAIL + 1))
else
  echo "  [PASS] web healthz 驗證失敗時，deploy_ec2.sh 正確地非零結束"
  PASS=$((PASS + 1))
  if grep -qF "web healthz 同步驗證失敗" "$CAPTURE/stdout_healthz-fail.log"; then
    echo "  [PASS] 失敗訊息明確（含 web healthz 同步驗證失敗 字樣）"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] deploy_ec2.sh 非零結束了，但訊息沒有明確指出是 web healthz 驗證失敗："
    cat "$CAPTURE/stdout_healthz-fail.log"
    FAIL=$((FAIL + 1))
  fi
fi

# issue #118：healthz gate 獨立擋在 --probe 之前，探針那次 send-command 根本
# 不該被送出。改為「跨所有已捕捉的 ssm call 搜尋 --probe 標記」，對 call 編號
# 漂移免疫（不再硬查 ssm_params_call2.txt）。
if find_ssm_call_by_marker 'fetch_scheduler.py --probe' >/dev/null 2>&1; then
  echo "  [FAIL] 場景 4：healthz gate 沒擋住，--probe 那次 send-command 仍被呼叫到了"
  FAIL=$((FAIL + 1))
else
  echo "  [PASS] 場景 4：healthz gate 正確擋在 --probe 之前，--probe 那次 send-command 根本沒被呼叫（獨立於 scheduler/probe 結果）"
  PASS=$((PASS + 1))
fi

echo "== 場景 5：IAM 角色不存在（首次真的建角色）→ Bedrock inline policy region 收斂（CISO hardening R2 #2b）=="
if run_deploy "iam-role-missing"; then
  echo "  [PASS] IAM 角色不存在時，deploy_ec2.sh 正常建完角色並成功結束"
  PASS=$((PASS + 1))
else
  echo "  [FAIL] IAM 角色不存在時，deploy_ec2.sh 未能成功結束"
  cat "$CAPTURE/stdout_iam-role-missing.log"
  FAIL=$((FAIL + 1))
fi

BEDROCK_POLICY=$(cat "$CAPTURE/iam_policy_iam-role-missing_trustforge-inline.txt" 2>/dev/null || echo "")
if [ -z "$BEDROCK_POLICY" ]; then
  echo "  [FAIL] 沒抓到 iam put-role-policy --policy-name trustforge-inline（IAM 角色建立分支沒被跑到）"
  FAIL=$((FAIL + 1))
else
  assert_contains "$BEDROCK_POLICY" "arn:aws:bedrock:ap-southeast-2::foundation-model/anthropic.*" "Bedrock policy 列舉 ap-southeast-2 foundation-model"
  assert_contains "$BEDROCK_POLICY" "arn:aws:bedrock:ap-southeast-4::foundation-model/anthropic.*" "Bedrock policy 列舉 ap-southeast-4 foundation-model"
  assert_contains "$BEDROCK_POLICY" "arn:aws:bedrock:ap-southeast-6::foundation-model/anthropic.*" "Bedrock policy 列舉 ap-southeast-6 foundation-model"
  assert_contains "$BEDROCK_POLICY" "arn:aws:bedrock:ap-southeast-2:123456789012:inference-profile/*anthropic*" "inference-profile ARN 收斂到實際部署的 REGION/ACCT（不留萬用字元）"
  if grep -qF "arn:aws:bedrock:*" "$CAPTURE/iam_policy_iam-role-missing_trustforge-inline.txt"; then
    echo "  [FAIL] Bedrock policy 仍留有 region 萬用字元 arn:aws:bedrock:*"
    FAIL=$((FAIL + 1))
  else
    echo "  [PASS] Bedrock policy 不留 region 萬用字元 arn:aws:bedrock:*"
    PASS=$((PASS + 1))
  fi
fi

echo
echo "== 場景 8：管理控制台 PR-5——token SSM 前綴含不安全字元 → 本機 fail-fast（注入防護）=="
# token SSM 前綴會被用來組 SSM 參數名路徑（再被嵌進 SSM commands JSON 與
# 遠端 shell）：含引號/管線/分號等字元必須在打任何 AWS API 之前就中止（不能等到遠端才炸）。
if run_deploy "bad-prefix" TRUSTFORGE_TOKEN_SSM_PREFIX='evil"pre;fix'; then
  echo "  [FAIL] token SSM 前綴含引號/分號仍回報成功（注入防護失效）——不可接受"
  FAIL=$((FAIL + 1))
else
  echo "  [PASS] token SSM 前綴含不安全字元時，deploy_ec2.sh 正確地非零結束"
  PASS=$((PASS + 1))
  assert_file_contains "$CAPTURE/stdout_bad-prefix.log" "不允許字元" "錯誤訊息明確指出字元集限制"
fi
# vp-eng M-2：字元集驗證是全腳本第一段檢查，在任何 aws 呼叫之前，中止時
# 應該連一次 aws 呼叫都還沒打過——零 aws 呼叫。
if [ -f "$CAPTURE/aws_calls_bad-prefix.log" ]; then
  echo "  [FAIL] token SSM 前綴字元集檢查中止前，不該有任何 aws 呼叫，但抓到：$(cat "$CAPTURE/aws_calls_bad-prefix.log")"
  FAIL=$((FAIL + 1))
else
  echo "  [PASS] token SSM 前綴字元集檢查中止在任何 aws 呼叫之前（零 aws 呼叫）"
  PASS=$((PASS + 1))
fi
if run_deploy "bad-cap" TRUSTFORGE_BEDROCK_DAILY_USD_CAP='1;rm -rf /'; then
  echo "  [FAIL] cap 含非數字字元仍回報成功（注入防護失效）——不可接受"
  FAIL=$((FAIL + 1))
else
  echo "  [PASS] cap 含非數字字元時，deploy_ec2.sh 正確地非零結束"
  PASS=$((PASS + 1))
  assert_file_contains "$CAPTURE/stdout_bad-cap.log" "必須是十進位數字" "錯誤訊息明確指出 cap 格式限制"
fi
if [ -f "$CAPTURE/aws_calls_bad-cap.log" ]; then
  echo "  [FAIL] cap 字元集檢查中止前，不該有任何 aws 呼叫，但抓到：$(cat "$CAPTURE/aws_calls_bad-cap.log")"
  FAIL=$((FAIL + 1))
else
  echo "  [PASS] cap 字元集檢查中止在任何 aws 呼叫之前（零 aws 呼叫）"
  PASS=$((PASS + 1))
fi

echo
echo "== 場景 12：#119/PR-A——trustforge-inline IAM 窄範圍（ssm:GetParameter deploy/* + runtime/* 前綴 ARN + kms:Decrypt ViaService）=="
# CEO gate（IAM 面）：兩條部署路徑 + 建角色路徑 reconcile 出來的
# trustforge-inline 都必須含窄範圍語句——不依賴 AmazonSSMManagedInstanceCore
# 恰好放行的巧合。結構化解析，Resource 錯一個字元都算 FAIL。PR-A 新增
# runtime/* 這條獨立語句後，policy 裡應恰好有兩條 ssm:GetParameter。
assert_inline_ssm_kms_stmts \
  "首次建置：trustforge-inline 含兩條 ssm:GetParameter（鎖 parameter/trustforge/deploy/* 與 runtime/*）+ kms:Decrypt（ViaService=ssm.<region>）" \
  "$CAPTURE/iam_policy_first-time_trustforge-inline.txt"
assert_inline_ssm_kms_stmts \
  "update-in-place：trustforge-inline 同樣被 reconcile 成含窄範圍 ssm/kms 語句（既有角色也吃得到）" \
  "$CAPTURE/iam_policy_update-in-place_trustforge-inline.txt"
assert_inline_ssm_kms_stmts \
  "IAM 角色不存在（首次建角色）：trustforge-inline 亦含窄範圍 ssm/kms 語句" \
  "$CAPTURE/iam_policy_iam-role-missing_trustforge-inline.txt"

echo
echo "== nginx /api/admin/ 硬化結構檢查（harper 條件 A + M1，管理控制台 PR-5）=="
# 結構化解析 deploy/nginx.conf（react TLS 版）的 /api/admin/ location 區塊：
# X-Real-IP/X-Forwarded-For 無條件 $remote_addr 覆寫（admin per-IP lockout
# 完整性）、no-store（設定快照不快取）、HSTS 重補（add_header 繼承全有全
# 無）、allowlist 範本預設註解（不硬編 IP）。
assert_nginx_admin_location \
  "nginx.conf（react TLS）/api/admin/ location：proxy + IP 覆寫 + no-store + HSTS 重補齊備，allowlist 預設註解" \
  "$REPO_ROOT/deploy/nginx.conf" \
  'proxy_pass http://trustforge_backend/api/admin/;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_no_cache 1;
proxy_cache_bypass 1;
add_header Cache-Control "no-store" always;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;'
# react-http（明碼）模式：管理面「技術封鎖」（harper M-3 = vp-eng M-1 複審
# 修正）——早期版本誤以為「省略 /api/admin/ location」就等於禁用，其實會
# 落入下面較短前綴的 /api/ 照樣 proxy 給 python；現在改成 nginx 主動
# `location ^~ /api/admin/ { return 404; }` 技術性封死，且仍要有勿設 token
# 的警語（雙重防護，非單一防線）。
assert_nginx_admin_blocked_with_404 \
  "nginx-react-http.conf（明碼）/api/admin/ 技術封鎖：location ^~ 優先於 /api/，唯一動作 return 404" \
  "$REPO_ROOT/deploy/nginx-react-http.conf"
assert_file_contains "$REPO_ROOT/deploy/nginx-react-http.conf" "勿設 TRUSTFORGE_ADMIN_TOKEN" \
  "nginx-react-http.conf 有「明碼模式勿設 TRUSTFORGE_ADMIN_TOKEN」警語"
assert_file_contains "$REPO_ROOT/deploy/README.md" "勿設" \
  "deploy/README.md 有 react-http 模式管理面技術封鎖 + 勿設 token 警語"
# 憑證邊界鐵則：deploy_ec2.sh 對這幾個 env（含 PR-B 新增的
# TRUSTFORGE_TOKEN_SSM_PREFIX opt-in 旗標）只能是 \${VAR-} 純 env 傳遞
# （無 :-default 寫死值）——腳本本體不得含任何 token 實際值。
for var in TRUSTFORGE_ADMIN_TOKEN TRUSTFORGE_LIVE_TOKEN TRUSTFORGE_BEDROCK_DAILY_USD_CAP TRUSTFORGE_TOKEN_SSM_PREFIX; do
  if grep -Eq "\\\$\{$var:?-[^}]" "$REPO_ROOT/deploy/deploy_ec2.sh"; then
    echo "  [FAIL] deploy_ec2.sh 對 $var 寫了預設值（違反憑證邊界：值只能由部署 env 傳入）"
    FAIL=$((FAIL + 1))
  else
    echo "  [PASS] deploy_ec2.sh 對 $var 無寫死預設值（\${VAR-} 純 env 傳遞）"
    PASS=$((PASS + 1))
  fi
done

echo
echo "== 結果：PASS=$PASS FAIL=$FAIL =="
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
