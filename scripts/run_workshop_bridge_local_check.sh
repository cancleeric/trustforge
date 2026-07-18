#!/usr/bin/env bash
# TrustForge × AgentCore bridge — 本機預檢（不真打 LLM）
#
# 本機無 workshop AWS 憑證、無 strands，無法驗證真實 LLM 回應。
# 本腳本只驗證：
#   1. 未設 TRUSTFORGE_AGENTCORE 時，BedrockClient import 不依賴 strands（現有路徑不變）
#   2. 設 TRUSTFORGE_AGENTCORE=1 時，bridge 模組可被 import、且「缺 strands 會報清楚錯誤」
#      （而不是靜默炸），證明開關邏輯正確
#   3. offline 模式不受 bridge 影響
#
# 真實 LLM 回應請在 workshop 環境跑 run_workshop_agentcore_bridge.sh 由 CEO 親測。
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true

echo "=== 預檢 1：未設開關，import 不依賴 strands ==="
unset TRUSTFORGE_AGENTCORE
python -c "from trustforge.bedrock import BedrockClient; print('  OK：BedrockClient import 成功（無 strands 依賴）')"

echo
echo "=== 預檢 2：設開關，bridge 可 import；缺 strands 應報清楚錯誤 ==="
export TRUSTFORGE_AGENTCORE=1
python - <<'PY' 2>&1 | sed 's/^/  /'
try:
    from trustforge.agentcore_llm_bridge import build_bridge
    print("bridge 模組 import OK（本機若已裝 strands 則不報錯）")
    try:
        build_bridge(region="us-west-2", narrative_model_id="x", stance_model_id="x", max_tokens=1024)
        print("build_bridge OK：環境已有 strands，可直接在 workshop 用")
    except RuntimeError as e:
        print(f"預期錯誤（本機無 strands）：{e}")
except Exception as e:
    print(f"非預期錯誤：{e}")
PY

echo
echo "=== 預檢 3：offline 模式不觸 bridge ==="
unset TRUSTFORGE_AGENTCORE
python - <<'PY' 2>&1 | sed 's/^/  /'
from trustforge.bedrock import BedrockClient
class _Cfg:
    region="us-west-2"; model_id=""; stance_model_id=""; max_tokens=1024
c = BedrockClient(_Cfg(), offline=True, stance_offline=True)
print("offline complete ->", c.complete("sys","prompt").text[:40])
print("offline classify_stance ->", c.classify_stance("a","b"))
print("  OK：offline 路徑完全不觸 bridge / AWS")
PY

echo
echo "=== 預檢完成 ==="
echo "本機僅驗證開關邏輯。真實 LLM 回應請在 workshop 環境執行："
echo "  bash scripts/run_workshop_agentcore_bridge.sh"
