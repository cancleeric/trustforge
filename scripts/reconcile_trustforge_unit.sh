#!/usr/bin/env bash
# ============================================================================
# reconcile_trustforge_unit.sh  (#121.6 / #121.7)
#
# 用途：
#   冪等確保 /etc/systemd/system/trustforge.service 含有：
#   1. LoadCredential=trustforge-admin-token:... / LoadCredential=trustforge-live-token:...
#      —— 由 src/trustforge/ssm_params.py::runtime_token_load_credential_line 產生
#         （嚴格對齊 app 讀取層 $CREDENTIALS_DIRECTORY/trustforge-<name> 與
#         setup_runtime_credentials.sh 寫入的 tmpfs 檔名 #121.7 LoadCredential 真
#         實讀取層）。憑證來源檔由獨立 oneshot unit trustforge-credentials.service
#         （Before=trustforge.service）在啟動「前」寫好，故 LoadCredential 載入時
#         檔案已存在（修正「ExecStartPre 產憑證 → 時序倒置」的 bug）。
#   2. Wants=trustforge-credentials.service / After=trustforge-credentials.service
#      —— 確保 trustforge.service 啟動時拉起並先於本 unit 完成憑證 oneshot。
#   3. Environment=CREDENTIALS_DIRECTORY=/run/trustforge-credentials
#      —— 告訴 app 去哪個 tmpfs 目錄讀 token（未設前綴時 setup 腳本 no-op、
#         app 自然 fallback SSM/env，向後相容）。
#   4. ExecStartPre=/opt/trustforge/scripts/sweep_deploy_parameters.sh
#      —— 部署期臨時 SSM 參數時間窗 sweep（#121.6，非致命）。
#
#   讓「既有實例 update-in-place」也能補上這些行（首次建置 user-data 亦會寫入
#   保持一致）。改完只動 unit 檔，不含任何 token 值。
#
# 安全邊界：
#   - 完全冪等（先確認是否存在，不存在才插入），重跑不重複插入。
#   - 不寫任何 token / 機敏值；只操作 unit 檔的 [Unit] / [Service] 行。
#   - 用 python3 做編輯（路徑含 `/` 在 sed `i` 語法上跨平台不一致；python 最穩）。
# ============================================================================
set -euo pipefail

UNIT="${UNIT_PATH:-/etc/systemd/system/trustforge.service}"
[ -f "$UNIT" ] || { echo "[reconcile] unit 不存在，skip"; exit 0; }

SWEEP_SCRIPT=/opt/trustforge/scripts/sweep_deploy_parameters.sh

UNIT="$UNIT" SWEEP_SCRIPT="$SWEEP_SCRIPT" \
python3 - <<'PY'
import os
import sys

unit = os.environ["UNIT"]
sweep_script = os.environ["SWEEP_SCRIPT"]

EXEC_TARGET = "ExecStart=/usr/bin/python3 -m trustforge.web"
CRED_ENV = "Environment=CREDENTIALS_DIRECTORY=/run/trustforge-credentials"
WANTS = "Wants=trustforge-credentials.service"
AFTER = "After=trustforge-credentials.service"
PRE_SWEEP = f"ExecStartPre={sweep_script}"

# #121.7：LoadCredential= 行——優先呼叫 ssm_params 的 runtime_token_load_credential_line
# （與 app 讀取層 / setup 腳本寫入檔名嚴格一致）；import 失敗（極端情況，例如
# trustforge 套件暫時不在 sys.path）時退回與該函式等價的確定性兜底，避免 reconcile
# 因 import 問題而整段失敗。
def _load_credential_lines():
    try:
        sys.path.insert(0, "/opt/trustforge")
        from trustforge.ssm_params import runtime_token_load_credential_line as _f
        return [_f("admin-token"), _f("live-token")]
    except Exception:
        return [
            "LoadCredential=trustforge-admin-token:/run/trustforge-credentials/trustforge-admin-token",
            "LoadCredential=trustforge-live-token:/run/trustforge-credentials/trustforge-live-token",
        ]


LOAD_CRED_LINES = _load_credential_lines()

with open(unit, "r", encoding="utf-8") as fh:
    lines = fh.read().splitlines()

present = set(lines)


def insert_after(lines, anchor, new_line):
    """在 anchor 行之後插入 new_line（冪等）。找不到 anchor 則 append。"""
    if new_line in present:
        return lines
    for i, ln in enumerate(lines):
        if ln == anchor:
            lines.insert(i + 1, new_line)
            present.add(new_line)
            print(f"[reconcile] 已插入：{new_line}")
            return lines
    lines.append(new_line)
    present.add(new_line)
    return lines


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


# [Unit] 段：Wants / After 憑證 oneshot（確保先於本 unit 完成）。
lines = insert_after(lines, "[Unit]", WANTS)
lines = insert_after(lines, "[Unit]", AFTER)
# [Service] 段：LoadCredential= 行插在 ExecStart 之前；CREDENTIALS_DIRECTORY。
for lc in LOAD_CRED_LINES:
    lines = insert_before(lines, EXEC_TARGET, lc)
lines = insert_after(lines, "Environment=PYTHONPATH=", CRED_ENV)
# 部署期臨時 SSM 參數 sweep（#121.6）。
lines = insert_before(lines, EXEC_TARGET, PRE_SWEEP)

with open(unit, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")

print("[reconcile] trustforge.service 已 reconcile（LoadCredential 讀取層 + 憑證 oneshot + sweep）。")
PY
