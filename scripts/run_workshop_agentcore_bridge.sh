#!/usr/bin/env bash
# TrustForge × AWS Bedrock AgentCore (Workshop Studio) — LLM 橋接啟動腳本
#
# 用途：在 workshop 提供的運算環境（已開好 Bedrock model access + IAM 執行角色）
# 啟動 TrustForge，並透過 TRUSTFORGE_AGENTCORE=1 把 LLM 呼叫切到 strands.BedrockModel，
# 吃 workshop 的 LLM 額度。
#
# 這是「先橋 LLM 就好」的最小路徑（B-1），不建 agentcore.json、不跑 agentcore deploy。
# TrustForge 本體架構不變，只是多設一個 env 開關 + runtime 裝 strands-agents。
#
# 前置（你/workshop 環境已具備）：
#   1. 這份 TrustForge repo 已 clone 到 workshop 環境
#   2. Python 3.14（workshop AgentCore runtime 鎖 PYTHON_3_14；本機 .venv 已是 3.14）
#   3. AWS 憑證 / IAM 角色已有 bedrock:InvokeModel（workshop 環境自帶）
#   4. 以下 env 視 workshop 公告調整（見「可調 env」）
#
# 用法：
#   bash scripts/run_workshop_agentcore_bridge.sh
#   或背景： nohup bash scripts/run_workshop_agentcore_bridge.sh > /tmp/trustforge_agentcore.log 2>&1 &

set -euo pipefail
cd "$(dirname "$0")/.."

# ---------- 可調 env（workshop 現場公告請改這裡） ----------
export TRUSTFORGE_AGENTCORE="${TRUSTFORGE_AGENTCORE:-1}"          # 開關：啟用 bridge
export AGENTCORE_MODEL_ID="${AGENTCORE_MODEL_ID:-global.anthropic.claude-sonnet-4-5-20250929-v1:0}"
export AWS_REGION="${AWS_REGION:-us-west-2}"                      # workshop runtime region
export PORT="${PORT:-8080}"

# 可選：若你的分析要真 Bedrock（live 模式），web.py 仍要求 TRUSTFORGE_LIVE_TOKEN
# + ?live=1。bridge 只接管 LLM 呼叫，live 閘邏輯不變。
# 設一個 token（任意值），並在請求帶 X-Live-Token header 才會真打 Bedrock。
export TRUSTFORGE_LIVE_TOKEN="${TRUSTFORGE_LIVE_TOKEN:-dev-token}"
# web.py 的 _bedrock_allowed 仍檢查 BEDROCK_MODEL_ID（bridge 模式實際不讀它，
# 但為了通過 live 閘必須有值）
export BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-$AGENTCORE_MODEL_ID}"

# ---------- 準備 Python 環境 ----------
echo "[bridge] 使用 Python: $(python3 --version 2>&1)"
if [ ! -d .venv ]; then
  echo "[bridge] 建立 .venv ..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[bridge] 確認 strands-agents 已安裝（bridge 依賴）..."
if ! python -c "import strands" 2>/dev/null; then
  echo "[bridge] 安裝 strands-agents ..."
  pip install --quiet --upgrade "strands-agents"
fi
# bridge 實際呼叫會走到 boto3 bedrock-runtime，workshop runtime 自帶；本機預檢需 botocore[crt]
python -c "import strands, sys; print('[bridge] strands', getattr(strands, '__version__', '?'), 'OK')"

# ---------- 安裝 TrustForge（editable，含現有 boto3/certifi） ----------
pip install --quiet -e . 2>/dev/null || pip install --quiet -e ".[dev]" 2>/dev/null || true

# ---------- 預檢：開關可讀、不會因缺 strands 靜默失敗 ----------
echo "[bridge] 預檢 bridge import ..."
python -c "from trustforge.agentcore_llm_bridge import build_bridge; print('[bridge] bridge 模組載入 OK')"

# ---------- 啟動 ----------
echo "[bridge] 啟動 TrustForge web (TRUSTFORGE_AGENTCORE=$TRUSTFORGE_AGENTCORE, PORT=$PORT) ..."
echo "[bridge] 訪問 /analyze?live=1 即可走 workshop LLM。Ctrl+C 停止。"
exec python -m trustforge.web
