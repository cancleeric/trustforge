#!/usr/bin/env python3
"""Bedrock smoke test — 驗證 Bedrock 連線可用，產出 artifact 證明非離線。

用法：
  python scripts/bedrock_smoke_test.py
  python -m trustforge.cli smoke

此腳本委託給 src/trustforge/smoke.py 執行，保持單一實作來源。
安全：不 hardcode 任何 credential，全走 env / boto3 default chain。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 確保可以從 repo 根直接執行（不一定已 pip install -e）
_repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo / "src"))

from trustforge.smoke import run_smoke  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_smoke())

