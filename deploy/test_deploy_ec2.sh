#!/usr/bin/env bash
# deploy_ec2.sh 邏輯測試（禁真 AWS）：完全 mock `aws`，跑過「首次建置」與
# 「update-in-place」兩分支，斷言：
#   1. systemd trustforge.service 有 CACHE_BACKEND / TRUSTFORGE_CACHE_TABLE /
#      TRUSTFORGE_COST_LEDGER_TABLE / COST_LEDGER_BACKEND 四個 env。
#   2. fetch-scheduler.service + fetch-scheduler.timer 有裝、有 enable。
#   3. update-in-place 對「本來沒有這些 env」的舊實例也會補上（不是只在首次
#      建置才有）。
#   4. zip 封包含 scripts/（否則 timer 在 EC2 上會找不到 fetch_scheduler.py）。
#   5. 管理控制台 PR-5：TRUSTFORGE_ADMIN_TOKEN / TRUSTFORGE_LIVE_TOKEN /
#      TRUSTFORGE_BEDROCK_DAILY_USD_CAP 三個 env 有值時寫入 systemd、未設時
#      不寫（fail-closed）且 update-in-place 會把殘留的舊行整行刪除；不合法
#      token 字元（注入防護）fail-fast；nginx react conf 的 /api/admin/
#      location 結構（X-Real-IP/XFF 覆寫 + no-store + allowlist 預設註解）。
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
    if [ "$SCENARIO" = "update-in-place" ] || [ "$SCENARIO" = "scheduler-fail" ] || [ "$SCENARIO" = "admin-env-update" ] || [ "$SCENARIO" = "admin-env-mixed" ]; then
      printf 'i-0123456789abcdef0\trunning\n'
    else
      printf ''
    fi ;;
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
    if [ "$SCENARIO" = "scheduler-fail" ] && [ "$CMDID_ARG" = "cmd-call2" ]; then
      echo "Failed"
    elif [ "$SCENARIO" = "healthz-fail" ] && [ "$CMDID_ARG" = "cmd-call1" ]; then
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
  # 管理控制台 PR-5：預設用 `env -u` 隔離外層可能殘留的三個 admin env（避免
  # 開發機環境讓「未設＝不寫行」的 fail-closed 斷言假 PASS/假 FAIL）；token
  # 場景用額外參數顯式帶入（例：run_deploy admin-env-first TRUSTFORGE_ADMIN_TOKEN=x）。
  rm -f "$CAPTURE/ssm_call_count"
  TF_TEST_SCENARIO="$scenario" TF_TEST_CAPTURE_DIR="$CAPTURE" PATH="$MOCKDIR:$PATH" \
    env -u TRUSTFORGE_ADMIN_TOKEN -u TRUSTFORGE_LIVE_TOKEN -u TRUSTFORGE_BEDROCK_DAILY_USD_CAP "$@" \
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
VERIFY_FT_HEALTHZ=$(cat "$CAPTURE/ssm_params_call1.txt" 2>/dev/null || echo "")
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

VERIFY_FT=$(cat "$CAPTURE/ssm_params_call2.txt" 2>/dev/null || echo "")
if [ -z "$VERIFY_FT" ]; then
  echo "  [FAIL] 首次建置：沒捕捉到 fetch-scheduler 同步驗證的 ssm send-command"
  FAIL=$((FAIL + 1))
else
  assert_contains "$VERIFY_FT" "systemctl start fetch-scheduler.service" "首次建置：同步觸發 systemctl start fetch-scheduler.service"
  assert_contains "$VERIFY_FT" "fetch-scheduler.service ] && break" "首次建置：有等 unit 檔存在（容忍 user-data 還沒跑完）"
  assert_contains "$VERIFY_FT" "journalctl -u fetch-scheduler" "首次建置：失敗時有印 journal"
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
VERIFY_UP=$(cat "$CAPTURE/ssm_params_call2.txt" 2>/dev/null || echo "")
if [ -z "$VERIFY_UP" ]; then
  echo "  [FAIL] update-in-place：沒捕捉到 fetch-scheduler 同步驗證的第二次 ssm send-command"
  FAIL=$((FAIL + 1))
else
  assert_contains "$VERIFY_UP" "systemctl start fetch-scheduler.service" "update-in-place：主設定成功後同步觸發 systemctl start fetch-scheduler.service"
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
  assert_contains "$REMOTE" 'cat > /etc/systemd/system/fetch-scheduler.service' "update-in-place: 重寫 fetch-scheduler.service"
  assert_contains "$REMOTE" 'cat > /etc/systemd/system/fetch-scheduler.timer' "update-in-place: 重寫 fetch-scheduler.timer"
  assert_contains "$REMOTE" 'systemctl enable --now fetch-scheduler.timer' "update-in-place: 確認 timer enabled"
  assert_contains "$REMOTE" 'Environment=AWS_REGION=ap-southeast-2' "update-in-place: fetch-scheduler.service 帶 AWS_REGION（顯式，不吃 cache.py 預設 us-east-1）"
  # 管理控制台 PR-5 fail-closed：未設三個 admin env 時，update-in-place 要
  # 把（前次部署可能殘留的）該行**整行刪除**——不是留舊值、也不是寫空值。
  assert_contains "$REMOTE" 'sed -i "/^Environment=TRUSTFORGE_ADMIN_TOKEN=/d" /etc/systemd/system/trustforge.service' "update-in-place: 未設 ADMIN_TOKEN → 刪除殘留行（fail-closed）"
  assert_contains "$REMOTE" 'sed -i "/^Environment=TRUSTFORGE_LIVE_TOKEN=/d" /etc/systemd/system/trustforge.service' "update-in-place: 未設 LIVE_TOKEN → 刪除殘留行（fail-closed）"
  assert_contains "$REMOTE" 'sed -i "/^Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=/d" /etc/systemd/system/trustforge.service' "update-in-place: 未設 DAILY_USD_CAP → 刪除殘留行（回到 config/DEFAULT 層）"

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
    # 管理控制台 PR-5：預埋三行「上次部署殘留的」admin env（stale 值），驗證
    # 未設 env 的這次部署會把它們整行刪掉（fail-closed），不是留著繼續有效。
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
    for key in TRUSTFORGE_ADMIN_TOKEN TRUSTFORGE_LIVE_TOKEN TRUSTFORGE_BEDROCK_DAILY_USD_CAP; do
      if grep -q "^Environment=$key=" "$FAKE_UNIT"; then
        echo "  [FAIL] update-in-place 未設 $key，但殘留的舊行沒被刪掉（fail-closed 失效）"
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
echo "== 場景 3：fetch-scheduler 同步驗證失敗（模擬 DynamoDB IAM 權限不足）=="
# 模擬「主設定 SSM 成功，但實際跑 fetch-scheduler 卻失敗」（HIGH 修的核心情境：
# 只 enable timer 不代表真的能寫進 DynamoDB）。mock 讓 call2（驗證那次）回
# Failed，斷言整支 deploy_ec2.sh 必須非零結束、不能誤報成功。
if run_deploy "scheduler-fail"; then
  echo "  [FAIL] fetch-scheduler 驗證失敗時，deploy_ec2.sh 仍回報成功（exit 0）——不可接受"
  FAIL=$((FAIL + 1))
else
  echo "  [PASS] fetch-scheduler 驗證失敗時，deploy_ec2.sh 正確地非零結束"
  PASS=$((PASS + 1))
  if grep -qF "fetch-scheduler 同步驗證失敗" "$CAPTURE/stdout_scheduler-fail.log"; then
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
SCHED_FAIL_CONTENT=$(cat "$CAPTURE/ssm_params_call2.txt" 2>/dev/null || echo "")
assert_contains "$SCHED_FAIL_CONTENT" "fetch_scheduler.py --probe" "場景 3：判定失敗的那次驗證，內容包含 --probe（不是只靠 freshness-skip 的一般排程）"

echo
echo "== 場景 4：首次建置 web healthz 驗證失敗（codex HIGH，模擬 systemctl/curl 失敗）=="
# 模擬「user-data 建完 scheduler unit 之後，web 服務其實沒起來」（本次修的
# HIGH 核心情境：舊版首次建置只驗 DynamoDB probe，probe 過就報成功，公開
# 服務卻是壞的）。mock 讓 call1（新加的 verify_web_healthz）回 Failed，斷言
# 整支 deploy_ec2.sh 必須非零結束、且 call2（--probe）根本沒被呼叫到——
# healthz gate 獨立擋在 probe 之前，不受 probe 會不會過影響。
rm -f "$CAPTURE/ssm_params_call2.txt"
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

if [ -f "$CAPTURE/ssm_params_call2.txt" ]; then
  echo "  [FAIL] 場景 4：healthz gate 沒擋住，--probe 那次 send-command（call2）仍被呼叫到了"
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
echo "== 場景 6：管理控制台 PR-5——admin env 有值（首次建置 + 明確 override）→ 寫入 systemd unit =="
# harper CISO M-2：首建 + token 現在預設硬擋（見場景 9），這裡明確帶
# TRUSTFORGE_ALLOW_USERDATA_TOKEN=1（逃生口）才能繼續驗證「寫入 unit」這條
# 既有行為——不是繞過新擋，是驗證擋跟寫入邏輯彼此獨立、沒有互相干擾。
if run_deploy "admin-env-first" \
     TRUSTFORGE_ADMIN_TOKEN=test-admin-token.A1 \
     TRUSTFORGE_LIVE_TOKEN=test-live-token.B2 \
     TRUSTFORGE_BEDROCK_DAILY_USD_CAP=1 \
     TRUSTFORGE_ALLOW_USERDATA_TOKEN=1; then
  echo "  deploy_ec2.sh 執行成功（exit 0）"
else
  echo "  [FAIL] admin-env-first 場景非零結束"
  cat "$CAPTURE/stdout_admin-env-first.log"
  FAIL=$((FAIL + 1))
fi
assert_unit_env_lines \
  "user-data: 三個 admin env 以獨立行寫入 trustforge.service unit 區塊（結構化解析，非整檔 grep；ExecStart 行未被黏住）" \
  "$CAPTURE/user_data.sh" \
  "Environment=TRUSTFORGE_ADMIN_TOKEN=test-admin-token.A1;Environment=TRUSTFORGE_LIVE_TOKEN=test-live-token.B2;Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=1"
assert_file_contains "$CAPTURE/stdout_admin-env-first.log" "user-data 殘留" \
  "首次建置帶 token 時有印 user-data 殘留警語（建議改走 update-in-place）"

echo
echo "== 場景 7：管理控制台 PR-5——admin env 有值（update-in-place）→ SSM sed reconcile 寫入 =="
if run_deploy "admin-env-update" \
     TRUSTFORGE_ADMIN_TOKEN=test-admin-token.A1 \
     TRUSTFORGE_LIVE_TOKEN=test-live-token.B2 \
     TRUSTFORGE_BEDROCK_DAILY_USD_CAP=1; then
  echo "  deploy_ec2.sh 執行成功（exit 0）"
else
  echo "  [FAIL] admin-env-update 場景非零結束"
  cat "$CAPTURE/stdout_admin-env-update.log"
  FAIL=$((FAIL + 1))
fi
SSM_ADMIN_RAW=$(cat "$CAPTURE/ssm_params_call1.txt" 2>/dev/null || echo "")
if [ -z "$SSM_ADMIN_RAW" ]; then
  echo "  [FAIL] admin-env-update：沒捕捉到主設定 ssm send-command 的 --parameters"
  FAIL=$((FAIL + 1))
else
  SSM_ADMIN_JSON="${SSM_ADMIN_RAW#commands=}"
  echo "$SSM_ADMIN_JSON" > "$CAPTURE/ssm_admin.json"
  # ADMIN_ENV_CMDS 是動態拼進 commands JSON 的片段——最容易壞的就是 JSON
  # 本身（引號/逗號拼錯），所以跟場景 2 一樣先驗 JSON 合法 + 還原腳本
  # bash -n，再驗內容。
  if python3 -c "
import json
with open('$CAPTURE/ssm_admin.json') as f:
    cmds = json.load(f)
assert isinstance(cmds, list) and len(cmds) > 5, 'commands 陣列太短或格式不對'
script = chr(10).join(cmds)
with open('$CAPTURE/remote_script_admin.sh', 'w') as f:
    f.write(script)
" 2>"$CAPTURE/json_admin_err.txt"; then
    echo "  [PASS] admin-env-update：含 ADMIN_ENV_CMDS 片段的 SSM commands 仍是合法 JSON 陣列"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] admin-env-update：SSM commands JSON 解析失敗："
    cat "$CAPTURE/json_admin_err.txt"
    FAIL=$((FAIL + 1))
  fi
  if bash -n "$CAPTURE/remote_script_admin.sh" 2>"$CAPTURE/bashn_admin_err.txt"; then
    echo "  [PASS] admin-env-update：還原出的遠端腳本 bash -n 語法合法"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] admin-env-update：還原出的遠端腳本語法錯誤："
    cat "$CAPTURE/bashn_admin_err.txt"
    FAIL=$((FAIL + 1))
  fi

  REMOTE_ADMIN=$(cat "$CAPTURE/remote_script_admin.sh" 2>/dev/null || echo "")
  assert_contains "$REMOTE_ADMIN" 'Environment=TRUSTFORGE_ADMIN_TOKEN=test-admin-token.A1|" /etc/systemd/system/trustforge.service' "admin-env-update: ADMIN_TOKEN ensure（sed 取代帶正確值）"
  assert_contains "$REMOTE_ADMIN" 'Environment=TRUSTFORGE_LIVE_TOKEN=test-live-token.B2|" /etc/systemd/system/trustforge.service' "admin-env-update: LIVE_TOKEN ensure（sed 取代帶正確值）"
  assert_contains "$REMOTE_ADMIN" 'Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=1|" /etc/systemd/system/trustforge.service' "admin-env-update: DAILY_USD_CAP ensure（sed 取代帶正確值）"
  assert_not_contains "$REMOTE_ADMIN" 'sed -i "/^Environment=TRUSTFORGE_ADMIN_TOKEN=/d"' "admin-env-update: 有值時不該出現 ADMIN_TOKEN 刪除行"
  assert_not_contains "$REMOTE_ADMIN" 'sed -i "/^Environment=TRUSTFORGE_LIVE_TOKEN=/d"' "admin-env-update: 有值時不該出現 LIVE_TOKEN 刪除行"
  assert_not_contains "$REMOTE_ADMIN" 'sed -i "/^Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=/d"' "admin-env-update: 有值時不該出現 DAILY_USD_CAP 刪除行"

  # 功能性驗證（同場景 2 的 GNU sed 慣例）：對「完全沒有 admin env 行的
  # 舊實例 unit」實跑 ensure 邏輯，斷言插入正確、值正確、重跑冪等。
  if [ "${USE_GNU_SED:-0}" = "1" ]; then
    FAKE_UNIT_A=$(mktemp)
    cat > "$FAKE_UNIT_A" <<'UNITEOF'
[Unit]
Description=TrustForge web
[Service]
Environment=PORT=80
Environment=BEDROCK_MODEL_ID=
Environment=PYTHONPATH=/opt/trustforge
ExecStart=/usr/bin/python3 -m trustforge.web
[Install]
WantedBy=multi-user.target
UNITEOF
    ENSURE_LINES_A=$(grep -n '^if grep -q' "$CAPTURE/remote_script_admin.sh" | cut -d: -f1)
    PATCHED_A=$(sed "s#/etc/systemd/system/trustforge.service#$FAKE_UNIT_A#g" "$CAPTURE/remote_script_admin.sh")
    for lineno in $ENSURE_LINES_A; do
      LINE=$(printf '%s\n' "$PATCHED_A" | sed -n "${lineno}p")
      bash -c "$LINE"
      bash -c "$LINE"  # 跑兩次驗證冪等
    done
    ADMIN_ENV_OK=1
    for kv in "TRUSTFORGE_ADMIN_TOKEN=test-admin-token.A1" "TRUSTFORGE_LIVE_TOKEN=test-live-token.B2" "TRUSTFORGE_BEDROCK_DAILY_USD_CAP=1"; do
      COUNT=$(grep -cF "Environment=$kv" "$FAKE_UNIT_A" || true)
      if [ "$COUNT" != "1" ]; then
        echo "  [FAIL] admin-env-update 實跑後 Environment=$kv 出現 $COUNT 次（應為 1）"
        ADMIN_ENV_OK=0
      fi
    done
    if [ "$ADMIN_ENV_OK" = "1" ]; then
      echo "  [PASS] admin-env-update：三個 admin env 對舊 unit 實跑插入正確、值正確、重跑冪等不重複"
      PASS=$((PASS + 1))
    else
      FAIL=$((FAIL + 1))
    fi
    rm -f "$FAKE_UNIT_A"
  else
    echo "  [SKIP] 本機 sed 非 GNU sed，略過 admin env ensure 實跑驗證（同場景 2 的 SKIP 理由）"
  fi
fi

echo
echo "== 場景 8：管理控制台 PR-5——token 含不安全字元 → 本機 fail-fast（注入防護）=="
# token 值會被嵌進 SSM commands JSON 與遠端 root shell 的 sed 取代式：含引
# 號/管線/分號等字元必須在打任何 AWS API 之前就中止（不能等到遠端才炸）。
if run_deploy "bad-token" TRUSTFORGE_ADMIN_TOKEN='evil"tok;en'; then
  echo "  [FAIL] token 含引號/分號仍回報成功（注入防護失效）——不可接受"
  FAIL=$((FAIL + 1))
else
  echo "  [PASS] token 含不安全字元時，deploy_ec2.sh 正確地非零結束"
  PASS=$((PASS + 1))
  assert_file_contains "$CAPTURE/stdout_bad-token.log" "不允許字元" "錯誤訊息明確指出字元集限制"
fi
# vp-eng M-2：字元集驗證是全腳本第一段檢查（在新的「首建 token 硬擋」查
# 既有實例之前），中止時應該連那一次唯讀 describe-instances 查詢都還沒打過
# ——零 aws 呼叫，比首建硬擋（見場景 9）更早。
if [ -f "$CAPTURE/aws_calls_bad-token.log" ]; then
  echo "  [FAIL] token 字元集檢查中止前，不該有任何 aws 呼叫，但抓到：$(cat "$CAPTURE/aws_calls_bad-token.log")"
  FAIL=$((FAIL + 1))
else
  echo "  [PASS] token 字元集檢查中止在任何 aws 呼叫之前（零 aws 呼叫）"
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
echo "== 場景 9：管理控制台 PR-5——首建帶 token、無 override → 硬性中止（harper CISO M-2）=="
# 早期版本只印警告就放行，token 會經 user-data 永久殘留。這裡斷言：查無既有
# 實例（scenario 名不在 mock 的 update-in-place 名單內）+ 帶 ADMIN_TOKEN、
# 未設 TRUSTFORGE_ALLOW_USERDATA_TOKEN → 非零結束，且中止前只打過一次 aws
# （唯讀 describe-instances 既有實例查詢，用來判斷是否為首次建置）——沒碰過
# 任何會建立/修改資源的 API（iam/s3/security-group/run-instances 都沒被
# 呼叫到）。
if run_deploy "first-build-token-hardfail" TRUSTFORGE_ADMIN_TOKEN=would-leak-into-userdata; then
  echo "  [FAIL] 首建帶 token 無 override 仍回報成功（M-2 擋失效）——不可接受"
  FAIL=$((FAIL + 1))
else
  echo "  [PASS] 首建帶 token 無 override 時，deploy_ec2.sh 正確地非零結束"
  PASS=$((PASS + 1))
fi
assert_file_contains "$CAPTURE/stdout_first-build-token-hardfail.log" \
  "查無既有實例（將走首次建置）且帶了" \
  "錯誤訊息明確指出：查無既有實例（首次建置）且帶了 token"
assert_file_contains "$CAPTURE/stdout_first-build-token-hardfail.log" \
  "TRUSTFORGE_ALLOW_USERDATA_TOKEN=1" \
  "錯誤訊息有指出明確逃生口 TRUSTFORGE_ALLOW_USERDATA_TOKEN=1"
AWSCALLS_HF=$(cat "$CAPTURE/aws_calls_first-build-token-hardfail.log" 2>/dev/null || echo "")
AWSCALLS_HF_COUNT=$(printf '%s\n' "$AWSCALLS_HF" | grep -c . || true)
if [ "$AWSCALLS_HF_COUNT" != "1" ]; then
  echo "  [FAIL] 首建 token 硬擋應該只打過 1 次 aws 呼叫（唯讀既有實例查詢），實際打了 $AWSCALLS_HF_COUNT 次：$AWSCALLS_HF"
  FAIL=$((FAIL + 1))
else
  echo "  [PASS] 首建 token 硬擋中止前，全腳本只打過 1 次 aws 呼叫（唯讀既有實例查詢）"
  PASS=$((PASS + 1))
fi
assert_contains "$AWSCALLS_HF" "ec2 describe-instances" "首建 token 硬擋前唯一打過的 aws 呼叫是 ec2 describe-instances（既有實例查詢）"
assert_not_contains "$AWSCALLS_HF" "sts get-caller-identity" "首建 token 硬擋在 aws sts get-caller-identity 之前就中止（尚未取得帳號）"
assert_not_contains "$AWSCALLS_HF" "iam " "首建 token 硬擋在任何 IAM 呼叫之前就中止"
assert_not_contains "$AWSCALLS_HF" "s3 " "首建 token 硬擋在任何 S3 呼叫之前就中止"
assert_not_contains "$AWSCALLS_HF" "run-instances" "首建 token 硬擋在 run-instances 之前就中止（不會建出帶 token 的 user-data 實例）"

echo
echo "== 場景 10：管理控制台 PR-5——首建帶 token + 明確 override → 放行（逃生口）=="
if run_deploy "first-build-token-override" \
     TRUSTFORGE_ADMIN_TOKEN=override-admin-token.Z1 \
     TRUSTFORGE_ALLOW_USERDATA_TOKEN=1; then
  echo "  [PASS] 首建帶 token + TRUSTFORGE_ALLOW_USERDATA_TOKEN=1 時，deploy_ec2.sh 正常放行並成功結束"
  PASS=$((PASS + 1))
else
  echo "  [FAIL] 首建帶 token + override 應該放行，但非零結束了"
  cat "$CAPTURE/stdout_first-build-token-override.log"
  FAIL=$((FAIL + 1))
fi
assert_unit_env_lines \
  "場景 10：override 放行後，user-data 仍正確寫入 ADMIN_TOKEN（override 只解除硬擋，不影響既有 fail-closed 寫入邏輯）" \
  "$CAPTURE/user_data.sh" \
  "Environment=TRUSTFORGE_ADMIN_TOKEN=override-admin-token.Z1"

echo
echo "== 場景 11：管理控制台 PR-5——mixed 部分設（ADMIN 設+LIVE 未設+CAP 設）+ 取代 stale 值分支功能實跑（vp-eng M-2）=="
# 早期測試只驗證過「插入到全新 unit（原本沒有這幾行）」（場景 7）跟「刪除
# stale 值」（場景 2，未設情境）兩條路徑，沒驗證過「既有值 → sed 取代成新
# 值」這個 if 分支是否真的正確取代（而非重複插入或誤刪其他行）。這裡故意
# 只設 ADMIN_TOKEN + CAP（LIVE_TOKEN 刻意不設），先斷言 commands JSON 對三
# 個 env 分別產生正確的 ensure/delete 指令（交錯出現），再對一份「三個 key
# 都已有舊值」的假 unit 實跑 GNU sed，驗證 ADMIN_TOKEN/CAP 的舊值被正確取代
# 成新值（而不是插入變成第二行），LIVE_TOKEN 的舊值被整行刪除。
if run_deploy "admin-env-mixed" \
     TRUSTFORGE_ADMIN_TOKEN=mixed-new-admin.M1 \
     TRUSTFORGE_BEDROCK_DAILY_USD_CAP=2.5; then
  echo "  deploy_ec2.sh 執行成功（exit 0）"
else
  echo "  [FAIL] admin-env-mixed 場景非零結束"
  cat "$CAPTURE/stdout_admin-env-mixed.log"
  FAIL=$((FAIL + 1))
fi
SSM_MIXED_RAW=$(cat "$CAPTURE/ssm_params_call1.txt" 2>/dev/null || echo "")
if [ -z "$SSM_MIXED_RAW" ]; then
  echo "  [FAIL] admin-env-mixed：沒捕捉到主設定 ssm send-command 的 --parameters"
  FAIL=$((FAIL + 1))
else
  SSM_MIXED_JSON="${SSM_MIXED_RAW#commands=}"
  echo "$SSM_MIXED_JSON" > "$CAPTURE/ssm_mixed.json"
  if python3 -c "
import json
with open('$CAPTURE/ssm_mixed.json') as f:
    cmds = json.load(f)
assert isinstance(cmds, list) and len(cmds) > 5, 'commands 陣列太短或格式不對'
script = chr(10).join(cmds)
with open('$CAPTURE/remote_script_mixed.sh', 'w') as f:
    f.write(script)
" 2>"$CAPTURE/json_mixed_err.txt"; then
    echo "  [PASS] admin-env-mixed：含 ADMIN_ENV_CMDS 片段的 SSM commands 仍是合法 JSON 陣列"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] admin-env-mixed：SSM commands JSON 解析失敗："
    cat "$CAPTURE/json_mixed_err.txt"
    FAIL=$((FAIL + 1))
  fi
  if bash -n "$CAPTURE/remote_script_mixed.sh" 2>"$CAPTURE/bashn_mixed_err.txt"; then
    echo "  [PASS] admin-env-mixed：還原出的遠端腳本 bash -n 語法合法"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] admin-env-mixed：還原出的遠端腳本語法錯誤："
    cat "$CAPTURE/bashn_mixed_err.txt"
    FAIL=$((FAIL + 1))
  fi

  REMOTE_MIXED=$(cat "$CAPTURE/remote_script_mixed.sh" 2>/dev/null || echo "")
  assert_contains "$REMOTE_MIXED" 'Environment=TRUSTFORGE_ADMIN_TOKEN=mixed-new-admin.M1|" /etc/systemd/system/trustforge.service' "admin-env-mixed: ADMIN_TOKEN ensure（有設）"
  assert_contains "$REMOTE_MIXED" 'Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=2.5|" /etc/systemd/system/trustforge.service' "admin-env-mixed: CAP ensure（有設）"
  assert_contains "$REMOTE_MIXED" 'sed -i "/^Environment=TRUSTFORGE_LIVE_TOKEN=/d"' "admin-env-mixed: LIVE_TOKEN delete（未設，交錯出現在 ensure 之間）"
  assert_not_contains "$REMOTE_MIXED" 'sed -i "/^Environment=TRUSTFORGE_ADMIN_TOKEN=/d"' "admin-env-mixed: 有設時不該出現 ADMIN_TOKEN 刪除行"
  assert_not_contains "$REMOTE_MIXED" 'sed -i "/^Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=/d"' "admin-env-mixed: 有設時不該出現 CAP 刪除行"
  assert_not_contains "$REMOTE_MIXED" 'Environment=TRUSTFORGE_LIVE_TOKEN=.*|" /etc/systemd/system/trustforge.service' "admin-env-mixed: 未設時不該出現 LIVE_TOKEN ensure（取代/插入）行"

  if [ "${USE_GNU_SED:-0}" = "1" ]; then
    FAKE_UNIT_MIXED=$(mktemp)
    cat > "$FAKE_UNIT_MIXED" <<'UNITEOF'
[Unit]
Description=TrustForge web
[Service]
Environment=PORT=80
Environment=BEDROCK_MODEL_ID=
Environment=PYTHONPATH=/opt/trustforge
Environment=TRUSTFORGE_ADMIN_TOKEN=stale-mixed-admin-OLD
Environment=TRUSTFORGE_LIVE_TOKEN=stale-mixed-live-OLD
Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=99
ExecStart=/usr/bin/python3 -m trustforge.web
[Install]
WantedBy=multi-user.target
UNITEOF
    ENSURE_LINES_MIXED=$(grep -n '^if grep -q' "$CAPTURE/remote_script_mixed.sh" | cut -d: -f1)
    PATCHED_MIXED=$(sed "s#/etc/systemd/system/trustforge.service#$FAKE_UNIT_MIXED#g" "$CAPTURE/remote_script_mixed.sh")
    for lineno in $ENSURE_LINES_MIXED; do
      LINE=$(printf '%s\n' "$PATCHED_MIXED" | sed -n "${lineno}p")
      bash -c "$LINE"
      bash -c "$LINE"  # 跑兩次驗證冪等
    done
    DELETE_LINES_MIXED=$(grep -n '^sed -i "/^Environment=TRUSTFORGE_' "$CAPTURE/remote_script_mixed.sh" | cut -d: -f1)
    for lineno in $DELETE_LINES_MIXED; do
      LINE=$(printf '%s\n' "$PATCHED_MIXED" | sed -n "${lineno}p")
      bash -c "$LINE"
      bash -c "$LINE"  # 跑兩次驗證冪等
    done
    MIXED_OK=1
    ADMIN_COUNT=$(grep -cF "Environment=TRUSTFORGE_ADMIN_TOKEN=" "$FAKE_UNIT_MIXED" || true)
    if [ "$ADMIN_COUNT" != "1" ] || ! grep -qF "Environment=TRUSTFORGE_ADMIN_TOKEN=mixed-new-admin.M1" "$FAKE_UNIT_MIXED"; then
      echo "  [FAIL] admin-env-mixed 實跑後 ADMIN_TOKEN 取代 stale 值失敗（行數=$ADMIN_COUNT，或值不對）"
      MIXED_OK=0
    fi
    CAP_COUNT=$(grep -cF "Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=" "$FAKE_UNIT_MIXED" || true)
    if [ "$CAP_COUNT" != "1" ] || ! grep -qF "Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=2.5" "$FAKE_UNIT_MIXED"; then
      echo "  [FAIL] admin-env-mixed 實跑後 CAP 取代 stale 值失敗（行數=$CAP_COUNT，或值不對）"
      MIXED_OK=0
    fi
    if grep -qF "Environment=TRUSTFORGE_LIVE_TOKEN=" "$FAKE_UNIT_MIXED"; then
      echo "  [FAIL] admin-env-mixed 實跑後 LIVE_TOKEN stale 值沒被刪除"
      MIXED_OK=0
    fi
    if [ "$MIXED_OK" = "1" ]; then
      echo "  [PASS] admin-env-mixed：對「三個 key 都已有 stale 舊值」的既有 unit 實跑——ADMIN_TOKEN/CAP 正確取代成新值（非插入重複）、LIVE_TOKEN 正確整行刪除，且重跑冪等"
      PASS=$((PASS + 1))
    else
      FAIL=$((FAIL + 1))
    fi
    rm -f "$FAKE_UNIT_MIXED"
  else
    echo "  [SKIP] 本機 sed 非 GNU sed，略過 mixed admin env 實跑驗證（同場景 2 的 SKIP 理由）"
  fi
fi

echo
echo "== nginx /api/admin/ 硬化結構檢查（harper 條件 A + M1，管理控制台 PR-5）=="
# 結構化解析 deploy/nginx.conf（react TLS 版）的 /api/admin/ location 區塊：
# X-Real-IP/X-Forwarded-For 無條件 $remote_addr 覆寫（admin per-IP lockout
# 完整性）、no-store（設定快照不快取）、HSTS 重補（add_header 繼承全有全
# 無）、allowlist 範本預設註解（不硬編 IP）。
assert_nginx_admin_location \
  "nginx.conf（react TLS）/api/admin/ location：proxy + IP 覆寫 + no-store + HSTS 重補齊備，allowlist 預設註解" \
  "$REPO_ROOT/deploy/nginx.conf" \
  'proxy_pass http://127.0.0.1:8080/api/admin/;
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
# 憑證邊界鐵則：deploy_ec2.sh 對三個 admin env 只能是 \${VAR-} 純 env 傳遞
# （無 :-default 寫死值）——腳本本體不得含任何 token 實際值。
for var in TRUSTFORGE_ADMIN_TOKEN TRUSTFORGE_LIVE_TOKEN TRUSTFORGE_BEDROCK_DAILY_USD_CAP; do
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
