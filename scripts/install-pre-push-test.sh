#!/usr/bin/env bash
# Install the repository-owned mandatory pre-push gate.
set -euo pipefail

if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  echo "❌ 請在 repo worktree 內執行此腳本" >&2
  exit 1
fi

ROOT="$(git rev-parse --show-toplevel)"
if [[ ! -x "$ROOT/.githooks/pre-push" ]]; then
  echo "❌ canonical hook 不存在或不可執行：$ROOT/.githooks/pre-push" >&2
  exit 1
fi

git -C "$ROOT" config core.hooksPath .githooks
echo "✅ mandatory pre-push gate 已啟用：.githooks/pre-push"
