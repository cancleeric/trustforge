#!/usr/bin/env bash
# TrustForge local live 啟動腳本（持久化用）
# 從 Hurricane Vault 取 MODELHUB_API_KEY，從 ~/.trustforge-live.env 取 Bedrock/AWS 憑證
# 用法：直接跑，或由 launchd plist 呼叫
set -euo pipefail

TRUSTFORGE_HOME="${TRUSTFORGE_HOME:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
cd "$TRUSTFORGE_HOME"

PYTHON="${TRUSTFORGE_PYTHON:-$TRUSTFORGE_HOME/.venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"

# === 非秘密配置 ===
export PYTHONPATH="$TRUSTFORGE_HOME/src"
export PORT="${PORT:-8799}"
export CACHE_BACKEND="${CACHE_BACKEND:-sqlite}"
export COST_LEDGER_BACKEND="${COST_LEDGER_BACKEND:-sqlite}"
export TRUSTFORGE_SQLITE_PATH="${TRUSTFORGE_SQLITE_PATH:-$TRUSTFORGE_HOME/out/trustforge.sqlite3}"
export TRUSTFORGE_BIND_HOST="${TRUSTFORGE_BIND_HOST:-127.0.0.1}"
export TRUSTFORGE_ALLOW_INSECURE_LIVE_TOKEN="${TRUSTFORGE_ALLOW_INSECURE_LIVE_TOKEN:-1}"
export TRUSTFORGE_CORS_ALLOW_ORIGINS="${TRUSTFORGE_CORS_ALLOW_ORIGINS:-http://127.0.0.1:4174,http://localhost:4174}"
export MODELHUB_BASE_URL="${MODELHUB_BASE_URL:-http://localhost:8950}"
export TRUSTFORGE_BEDROCK_DAILY_USD_CAP="${TRUSTFORGE_BEDROCK_DAILY_USD_CAP:-10}"

# === 從 Hurricane Vault 取 secret（不硬編碼）===
VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8930}"
_vault_get() {
  curl -sf "$VAULT_ADDR/api/secrets/$1" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('value',''))" 2>/dev/null || true
}
export MODELHUB_API_KEY="${MODELHUB_API_KEY:-$(_vault_get trustforge/dev/MODELHUB_API_KEY)}"

# === Bedrock live 憑證（從 ~/.trustforge-live.env 載入，不進 git）===
LIVE_ENV="$HOME/.trustforge-live.env"
if [[ -f "$LIVE_ENV" ]]; then
  # shellcheck source=/dev/null
  source "$LIVE_ENV"
else
  echo "[start-local-live] ~/.trustforge-live.env 不存在 — Bedrock live 模式未配置" >&2
  echo "[start-local-live] 建立範本：cat > ~/.trustforge-live.env <<'EOF'" >&2
  echo "  export BEDROCK_MODEL_ID=anthropic.claude-..." >&2
  echo "  export AWS_ACCESS_KEY_ID=..." >&2
  echo "  export AWS_SECRET_ACCESS_KEY=..." >&2
  echo "  export AWS_REGION=us-west-2" >&2
  echo "  export TRUSTFORGE_LIVE_TOKEN=$(openssl rand -hex 24)" >&2
  echo "  export TRUSTFORGE_ADMIN_TOKEN=$(openssl rand -hex 24)" >&2
fi

echo "[start-local-live] PORT=$PORT bedrock=${BEDROCK_MODEL_ID:+live} modelhub=${MODELHUB_API_KEY:+ok} python=$PYTHON"

exec "$PYTHON" -m trustforge.web
