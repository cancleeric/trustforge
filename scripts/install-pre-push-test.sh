#!/usr/bin/env bash
# install-pre-push-test.sh — Issue #349
# 安裝 pre-push test gate 到本機 .git/hooks/pre-push
# 團隊成員 clone 後執行一次即可：bash scripts/install-pre-push-test.sh
set -euo pipefail

HOOK=".git/hooks/pre-push"
MARKER="# === TrustForge pre-push test gate ==="

if [[ ! -d ".git" ]]; then
  echo "❌ 請在 repo 根目錄執行此腳本" >&2
  exit 1
fi

# 確保 hook 檔案存在且可執行
if [[ ! -f "$HOOK" ]]; then
  printf '#!/usr/bin/env bash\nset -euo pipefail\n' > "$HOOK"
  chmod +x "$HOOK"
fi

# 已安裝則跳過
if grep -qF "$MARKER" "$HOOK"; then
  echo "✅ pre-push test gate 已安裝，跳過。"
  exit 0
fi

# 讀取現有內容
existing="$(cat "$HOOK")"

# 取出 shebang 和 set 行（前兩行），其餘為 body
head_lines="$(head -2 "$HOOK")"
body="$(tail -n +3 "$HOOK")"

# 寫回：shebang + set → test gate → 原有邏輯
cat > "$HOOK" <<'HOOK_CONTENT'
#!/usr/bin/env bash
set -euo pipefail

# === TrustForge pre-push test gate ===
if [[ -f ".venv/bin/pytest" ]]; then
  echo "[pre-push] Running tests..."
  PYTHONPATH=src CACHE_BACKEND=sqlite TRUSTFORGE_DISABLE_ADMIN_CONFIG=1 \
    .venv/bin/pytest tests/ -x -q --no-cov --timeout=120 2>&1 | tail -5
  if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
    echo "[pre-push] ❌ Tests failed. Push rejected."
    exit 1
  fi
  echo "[pre-push] ✅ Tests passed."
fi
# === End TrustForge pre-push test gate ===

HOOK_CONTENT

# 把原有 body（跳過 shebang + set）追加回去
printf '%s\n' "$body" >> "$HOOK"

chmod +x "$HOOK"
echo "✅ pre-push test gate 已安裝到 $HOOK"
