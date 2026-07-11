#!/usr/bin/env bash
# ============================================================================
# reconcile_trustforge_unit.sh  (#121.6)
#
# 用途：
#   冪等確保 /etc/systemd/system/trustforge.service 含有：
#   1. ExecStartPre=/opt/trustforge/scripts/sweep_deploy_parameters.sh
#      —— 部署期臨時 SSM 參數時間窗 sweep（#121.6，非致命）。
#
#   讓「既有實例 update-in-place」也能補上這一行（首次建置 user-data 亦會寫入
#   保持一致）。改完只動 unit 檔，不含任何 token 值。
#
# 安全模型（回退到 SSM 路徑）：
#   runtime token 由 app 啟動期經 get_runtime_token 直接從 SSM Parameter Store
#   （SecureString + KMS，WithDecryption）讀取——不經 argv / env / 持久碟 / 日誌，
#   fail-closed。本腳本不注入任何 systemd tmpfs 憑證層（該層經 codex-review 第三
#   輪實測確認在真實部署路徑（全新 user-data / update-in-place reconcile）完全失效
#   且有「假安全感 + 服務起不來」風險，已移除並回退 SSM 路徑）。
#
# 安全邊界：
#   - 完全冪等（先確認是否存在，不存在才插入），重跑不重複插入。
#   - 不寫任何 token / 機敏值；只操作 unit 檔的 [Service] 行。
#   - 用 python3 做編輯（路徑含 `/` 在 sed `i` 語法上跨平台不一致；python 最穩）。
# ============================================================================
set -euo pipefail

UNIT="${UNIT_PATH:-/etc/systemd/system/trustforge.service}"
[ -f "$UNIT" ] || { echo "[reconcile] unit 不存在，skip"; exit 0; }

SWEEP_SCRIPT=/opt/trustforge/scripts/sweep_deploy_parameters.sh

UNIT="$UNIT" SWEEP_SCRIPT="$SWEEP_SCRIPT" \
python3 - <<'PY'
import os

unit = os.environ["UNIT"]
sweep_script = os.environ["SWEEP_SCRIPT"]

EXEC_TARGET = "ExecStart=/usr/bin/python3 -m trustforge.web"
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


# 部署期臨時 SSM 參數 sweep（#121.6）。
lines = insert_before(lines, EXEC_TARGET, PRE_SWEEP)

with open(unit, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")

print("[reconcile] trustforge.service 已 reconcile（sweep ExecStartPre）。")
PY
