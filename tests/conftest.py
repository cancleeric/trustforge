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


@pytest.fixture(autouse=True)
def _isolate_connector_cache(tmp_path, monkeypatch):
    """連接器快取（階段2 CachedSource/fetch_scheduler）測試隔離：預設寫入
    tmp_path，而非真實 repo 的 `out/connector_cache/`。

    部分既有測試以 `collect(coin, coin=coin, ...)`（`offline` 預設 False、
    `sources=None`）呼叫線上路徑，會建立 `CachedSource`。預設 backend 是
    `DynamoDBCache`（不吃這個 env、也不連真 AWS），但 `cache_get`/`cache_set`
    fallback 路徑、以及測試裡直接指定 `CACHE_BACKEND=json` 的案例都會落到
    `JsonCacheBackend`，其路徑吃這個 env。不隔離的話，這些情境會在開發者
    本機的 `out/connector_cache/` 寫入 JSON 檔案，汙染真實快取目錄。個別
    測試若要驗證「真實預設路徑」邏輯本身，可自行
    `monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", ...)` 再覆寫一次。
    """
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path / "connector_cache"))
