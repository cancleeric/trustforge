#!/usr/bin/env bash
# deploy_ec2.sh 邏輯測試（禁真 AWS）：完全 mock `aws`，跑過「首次建置」與
# 「update-in-place」兩分支，斷言：
#   1. systemd trustforge.service 有 CACHE_BACKEND / TRUSTFORGE_CACHE_TABLE /
#      TRUSTFORGE_COST_LEDGER_TABLE / COST_LEDGER_BACKEND 四個 env。
#   2. fetch-scheduler.service + fetch-scheduler.timer 有裝、有 enable。
#   3. update-in-place 對「本來沒有這些 env」的舊實例也會補上（不是只在首次
#      建置才有）。
#   4. zip 封包含 scripts/（否則 timer 在 EC2 上會找不到 fetch_scheduler.py）。
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
    if [ "$SCENARIO" = "update-in-place" ] || [ "$SCENARIO" = "scheduler-fail" ]; then
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
  # ssm_call_count 是「這次部署送了幾次 ssm send-command」的計數器，每個
  # scenario 各自從 1 開始編號（call1=主設定或首次驗證、call2=update-in-place
  # 額外的 fetch-scheduler 同步驗證），開跑前先清掉避免跨場景疊加。
  rm -f "$CAPTURE/ssm_call_count"
  TF_TEST_SCENARIO="$scenario" TF_TEST_CAPTURE_DIR="$CAPTURE" PATH="$MOCKDIR:$PATH" \
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

# 注意：trustforge-inline（Bedrock）policy 只有在「IAM 角色本來不存在」那個
# 分支才會建立（見 deploy_ec2.sh 214 行 `if ! aws iam get-role`），first-time/
# update-in-place 這兩個場景的 mock 都假設角色已存在（跟 dynamodb reconcile
# 不同，後者每次部署都無條件 reconcile）。專門測 Bedrock region 收斂的斷言
# 見下方獨立的「場景 5：IAM 角色不存在」。

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
echo "== 場景 2：既有實例 running → update-in-place =="
if run_deploy "update-in-place"; then
  echo "  deploy_ec2.sh 執行成功（exit 0）"
else
  echo "  [FAIL] deploy_ec2.sh update-in-place 場景非零結束"
  cat "$CAPTURE/stdout_update-in-place.log"
  FAIL=$((FAIL + 1))
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
    DUP_OK=1
    for key in CACHE_BACKEND TRUSTFORGE_CACHE_TABLE TRUSTFORGE_COST_LEDGER_TABLE COST_LEDGER_BACKEND; do
      COUNT=$(grep -c "^Environment=$key=" "$FAKE_UNIT")
      if [ "$COUNT" != "1" ]; then
        echo "  [FAIL] update-in-place ensure-env 對舊實例套用後 $key 出現 $COUNT 次（應為 1，不冪等或沒插入）"
        DUP_OK=0
      fi
    done
    if [ "$DUP_OK" = "1" ]; then
      echo "  [PASS] update-in-place ensure-env 對「完全沒有這些行的舊實例」插入正確、重跑冪等不重複"
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
echo "== 結果：PASS=$PASS FAIL=$FAIL =="
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
