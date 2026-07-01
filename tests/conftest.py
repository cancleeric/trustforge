"""測試套件全域 fixture。"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_cost_ledger(tmp_path, monkeypatch):
    """成本帳本測試隔離：預設寫入 tmp_path，而非真實 repo 的 `out/cost_ledger.jsonl`。

    `run_agent_pipeline` 收尾一律呼叫 `ledger.append_run()`，若不隔離，整個測試套件
    （數百個呼叫 pipeline 的測試）每次跑都會把測試噪音資料寫進開發者本機的
    `out/cost_ledger.jsonl`，汙染真實帳本、讓 `/costs` 頁面顯示假資料。
    個別測試若要驗證「真實預設路徑」邏輯本身（如 `_default_ledger_path()`），可自行
    用 `monkeypatch.setenv("TRUSTFORGE_COST_LEDGER_PATH", ...)` 再覆寫一次。
    """
    monkeypatch.setenv(
        "TRUSTFORGE_COST_LEDGER_PATH", str(tmp_path / "test_cost_ledger.jsonl")
    )
