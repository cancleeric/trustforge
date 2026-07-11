#!/usr/bin/env bash
# ============================================================================
# sweep_deploy_parameters.sh  (#121.6)
#
# 用途：
#   由 systemd unit 的 ExecStartPre 呼叫，清理「部署期臨時 SSM 參數」
#   （/trustforge/deploy/*）中超過時間窗、早該被 trap 清掉卻因異常中斷而殘留
#   的項目（見 src/trustforge/ssm_params.py::sweep_deploy_parameters）。
#
# 設計：非致命——失敗只記 log、不影響服務啟動（sweep 是維運收尾動作，不該
# 因為 SSM 暫時不可用而擋住 TrustForge 啟動）。呼叫端（unit ExecStartPre）應
# 用 `|| true` 包住本腳本。
# ============================================================================
set -uo pipefail

cd /opt/trustforge 2>/dev/null || exit 0

PYTHONPATH=/opt/trustforge python3 - <<'PY' || true
from trustforge import ssm_params

try:
    deleted = ssm_params.sweep_deploy_parameters()
    if deleted:
        print(f"[sweep] 已清理 {len(deleted)} 個殘留部署期 SSM 參數：{deleted}")
    else:
        print("[sweep] 無殘留部署期 SSM 參數需清理")
except Exception as exc:  # 非致命：sweep 失敗不擋啟動
    print(f"[sweep] 清理失敗（非致命，略過）：{exc}")
PY
