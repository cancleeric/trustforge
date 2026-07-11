#!/usr/bin/env bash
# ============================================================================
# reconcile_trustforge_unit.sh  (#121.6 / #121.7)
#
# 用途：
#   冪等確保 /etc/systemd/system/trustforge.service 含有：
#   1. ExecStartPre=/opt/trustforge/deploy/setup_runtime_credentials.sh
#      —— 開機期把 SSM SecureString token 寫進 tmpfs（CREDENTIALS_DIRECTORY），
#         供 app 經 $CREDENTIALS_DIRECTORY/<name> 讀取（#121.7 LoadCredential 真
#         實讀取層，見 src/trustforge/ssm_params.py::get_runtime_token）。
#   2. Environment=CREDENTIALS_DIRECTORY=/run/trustforge-credentials
#      —— 告訴 app 去哪個 tmpfs 目錄讀 token（未設前綴時 setup 腳本 no-op、
#         app 自然 fallback SSM/env，向後相容）。
#   3. ExecStartPre=/opt/trustforge/scripts/sweep_deploy_parameters.sh
#      —— 部署期臨時 SSM 參數時間窗 sweep（#121.6，非致命）。
#
#   讓「既有實例 update-in-place」也能補上這些行（首次建置 user-data 亦會呼叫
#   本腳本保持一致）。改完只動 unit 檔，不含任何 token 值。
#
# 安全邊界：
#   - 完全冪等（先確認是否存在，不存在才插入），重跑不重複插入。
#   - 不寫任何 token / 機敏值；只操作 unit 檔的 ExecStartPre / Environment 行。
#   - 用 python3 做編輯（路徑含 `/` 在 sed `i` 語法上跨平台不一致；python 最穩）。
# ============================================================================
set -euo pipefail

UNIT="${UNIT_PATH:-/etc/systemd/system/trustforge.service}"
[ -f "$UNIT" ] || { echo "[reconcile] unit 不存在，skip"; exit 0; }

CRED_SCRIPT=/opt/trustforge/deploy/setup_runtime_credentials.sh
SWEEP_SCRIPT=/opt/trustforge/scripts/sweep_deploy_parameters.sh

UNIT="$UNIT" CRED_SCRIPT="$CRED_SCRIPT" SWEEP_SCRIPT="$SWEEP_SCRIPT" \
python3 - <<'PY'
import os
import io

unit = os.environ["UNIT"]
cred_script = os.environ["CRED_SCRIPT"]
sweep_script = os.environ["SWEEP_SCRIPT"]

EXEC_TARGET = "ExecStart=/usr/bin/python3 -m trustforge.web"
CRED_ENV = "Environment=CREDENTIALS_DIRECTORY=/run/trustforge-credentials"
PRE_CRED = f"ExecStartPre={cred_script}"
PRE_SWEEP = f"ExecStartPre={sweep_script}"

with open(unit, "r", encoding="utf-8") as fh:
    lines = fh.read().splitlines()

present = set(lines)


def insert_before(lines, target_prefix, new_line):
    if new_line in present:
        return lines
    for i, ln in enumerate(lines):
        if ln == target_prefix:
            lines.insert(i, new_line)
            present.add(new_line)
            print(f"[reconcile] 已插入：{new_line}")
            return lines
    # 找不到 ExecStart 目標（極罕見）→ 直接 append
    lines.append(new_line)
    present.add(new_line)
    return lines


def insert_after_env(lines, anchor_prefix, new_line):
    if new_line in present:
        return lines
    for i, ln in enumerate(lines):
        if ln.startswith(anchor_prefix):
            lines.insert(i + 1, new_line)
            present.add(new_line)
            print(f"[reconcile] 已插入：{new_line}")
            return lines
    # 退路：加在 [Service] 之後
    for i, ln in enumerate(lines):
        if ln == "[Service]":
            lines.insert(i + 1, new_line)
            present.add(new_line)
            return lines
    lines.append(new_line)
    present.add(new_line)
    return lines


lines = insert_before(lines, EXEC_TARGET, PRE_CRED)
lines = insert_after_env(lines, "Environment=PYTHONPATH=", CRED_ENV)
lines = insert_before(lines, EXEC_TARGET, PRE_SWEEP)

with open(unit, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")

print("[reconcile] trustforge.service 已 reconcile（LoadCredential 讀取層 + sweep）。")
PY
