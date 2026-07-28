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
    tmp_path，而非真實 repo 的 `out/connector_cache/`，且預設強制走本地
    JSON backend，而非真實預設值 `DynamoDBCache`。

    部分既有測試以 `collect(coin, coin=coin, ...)`（`offline` 預設 False、
    `sources=None`）呼叫線上路徑，會建立 `CachedSource`。`get_cache_backend()`
    的生產預設是 `DynamoDBCache`；`DynamoDBCache.__init__` 本身不連真 AWS，
    但 lazy 建立的 boto3 client 一旦被 `cache_get`/`cache_set` 實際呼叫
    `.get()`/`.set()`，在開發者本機沒有有效 AWS SSO/憑證時，每個
    (source, coin) key 都要卡 0.6–1.2s 逾時才拋 `LoginRefreshRequired`——
    掃多源多幣的測試（`/status` 鮮度矩陣、`analyze` 線上路徑）單顆可以拖到
    數十秒到 150 秒以上，是全套件跑很慢的最大病灶。測試預設本來就不該打
    真 AWS，比照上面 `_isolate_cost_ledger`（隔離寫入路徑）的邏輯，這裡把
    「讀取用哪個 backend」也一併鎖死成本地 JSON。個別測試若要驗證「真實
    預設值就是 dynamodb」本身（如 `test_get_cache_backend_reads_env`），可
    自行 `monkeypatch.delenv("CACHE_BACKEND", raising=False)` 還原真預設再
    斷言；只要不呼叫 `.get()`/`.set()`，單純建構 `DynamoDBCache()` 本身不
    會連真網，不受這個 fixture 影響。
    """
    monkeypatch.setenv("CACHE_BACKEND", "json")
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path / "connector_cache"))
    monkeypatch.setenv(
        "TRUSTFORGE_SOURCE_ARCHIVE_PATH", str(tmp_path / "source_events.sqlite3")
    )


@pytest.fixture(autouse=True)
def _isolate_scheduler_run_log(tmp_path, monkeypatch):
    """排程 run log（Phase3 `scheduler_log.py`）測試隔離：預設寫入 tmp_path，
    而非真實 repo 的 `out/scheduler_runs.jsonl`。

    比照 `_isolate_cost_ledger`/`_isolate_connector_cache`：不隔離的話，跑
    `scripts/fetch_scheduler.py` 相關測試會把測試噪音寫進開發者本機的
    `out/scheduler_runs.jsonl`，汙染 `/status` 頁面「最近排程執行」顯示。
    """
    monkeypatch.setenv(
        "TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "test_scheduler_runs.jsonl")
    )


@pytest.fixture(autouse=True)
def _isolate_budget_reservation():
    """#9 codex HIGH（並行 TOCTOU）：`budget_guard.BudgetReservation` 是
    process-local 全域單例（`_RESERVATION`），跨測試共用同一份記憶體狀態。
    比照 `_isolate_cost_ledger` 等既有慣例，每個測試前後都歸零，避免某個
    測試忘記 release（或斷言中途失敗、跳過 release）殘留的「已預留」金額
    污染到下一個測試。"""
    from trustforge.budget_guard import _reset_reservation_for_tests

    _reset_reservation_for_tests()
    yield
    _reset_reservation_for_tests()


@pytest.fixture(autouse=True)
def _isolate_unledgered_spend():
    """#9 codex HIGH（記帳完整性）：`budget_guard._UNLEDGERED_SPEND` 是
    process-local 全域單例，跨測試共用同一份記憶體狀態。比照
    `_isolate_budget_reservation`，每個測試前後都歸零，避免某個測試模擬
    「帳本持久化失敗」殘留的未記帳花費污染到下一個測試。"""
    from trustforge.budget_guard import _reset_unledgered_spend_for_tests

    _reset_unledgered_spend_for_tests()
    yield
    _reset_unledgered_spend_for_tests()


@pytest.fixture(autouse=True)
def _isolate_admin_config_store(monkeypatch):
    """admin console PR-3：`budget_guard.daily_cap_usd()` 與 web.py live 閘
    （`_bedrock_allowed`/`_live_token_matches`）的熱路徑現在會走
    `admin_config.get_config_cached()`——其預設 store lazy 連真 DynamoDB。
    比照 `_isolate_connector_cache`（測試預設不打真 AWS）：把預設 store 換成
    「item 不存在」的 in-memory 假表（＝空 config，行為與 v0.7.0 逐字相同）。
    個別測試要模擬 config 層時，自行 `monkeypatch.setattr(admin_config,
    "get_config", ...)`（`get_config_cached` 會透過模組全域拿到 patch 後
    版本）或建自己的 store + mock `_table`（PR-1 測試既有慣例，傳參注入，
    不受本 fixture 影響）。"""
    from trustforge import admin_config

    class _EmptyAdminTable:
        def get_item(self, **kwargs):
            return {}  # 無 "Item" → 空 config（合法「尚未設定過」狀態）

        def put_item(self, **kwargs):
            return {}

        def query(self, **kwargs):
            return {"Items": []}

    store = admin_config.AdminConfigStore()
    store._table = _EmptyAdminTable()
    monkeypatch.setattr(admin_config, "_default_store_instance", store)


@pytest.fixture(autouse=True)
def _isolate_rate_limit_store(monkeypatch):
    """issue #1（CISO High）：`web._check_live_rate_limit` 現在優先呼叫
    `rate_limit_store`（DynamoDB 跨實例共享限流計數器），其預設 store lazy
    連真 DynamoDB。比照 `_isolate_admin_config_store`（測試預設不打真
    AWS）：把預設 store 換成「一律回報後端不可用」的假 store，讓
    `_check_live_rate_limit` 每次都吃到 `RateLimitBackendError` 並 fallback
    回 `_check_live_rate_limit_local`（DynamoDB 護欄加入前逐字相同的
    process-local 滑動視窗邏輯）——保證全套件既有的限流測試（直接操作/斷言
    `web._rate_buckets`）行為不變。個別測試要驗證 DynamoDB 共享計數器本身
    時，自建 `rate_limit_store.RateLimitStore` + 假 `_table`（見
    `test_rate_limit_store.py`），不受本 fixture 影響。"""
    from trustforge import rate_limit_store

    class _AlwaysUnavailableStore:
        def try_increment(self, bucket, key, window_seconds, max_requests, *, now=None):
            raise rate_limit_store.RateLimitBackendError("測試環境預設不打真 DynamoDB")

    monkeypatch.setattr(rate_limit_store, "_default_store_instance", _AlwaysUnavailableStore())


@pytest.fixture(autouse=True)
def _isolate_admin_config_cache():
    """admin console PR-1：`admin_config` 的 process 內 TTL 快取
    （`_cache`）是模組級全域狀態，跨測試共用同一份記憶體。比照
    `_isolate_budget_reservation` 慣例，每個測試前後都清空，避免某個測試
    write-through 進快取的設定（或殘留的過期戳）污染到下一個測試。"""
    from trustforge.admin_config import _reset_admin_config_cache_for_tests
    from trustforge.web import _reset_admin_cfg_fail_window_for_tests

    _reset_admin_config_cache_for_tests()
    _reset_admin_cfg_fail_window_for_tests()
    yield
    _reset_admin_config_cache_for_tests()
    _reset_admin_cfg_fail_window_for_tests()


@pytest.fixture(autouse=True)
def _isolate_calibration_model_cache():
    """issue #343：`scoring._CALIBRATION_MODEL_CACHE` 是模組級全域狀態，
    跨測試共用同一份記憶體。比照其他 isolate fixture，每個測試前後都清空，
    避免某個測試載入的校準模型（如 TestNoModelFallback.test_with_model_uses_isotonic）
    污染到下一個測試。"""
    from trustforge.trust import scoring
    scoring._CALIBRATION_MODEL_CACHE.clear()
    yield
    scoring._CALIBRATION_MODEL_CACHE.clear()


@pytest.fixture(autouse=True)
def _zero_regulatory_delay(monkeypatch):
    """測試環境中禮貌性延遲歸零——CI 不打真 SEC API，不需等。
    原值 0.1s × 12 查詢詞 = 1.1s/test，14 個 regulatory 測試共浪費 ~16s。"""
    try:
        from trustforge.ingestion import regulatory
        monkeypatch.setattr(regulatory, "_REQUEST_DELAY_SECONDS", 0)
    except (ImportError, AttributeError):
        pass
