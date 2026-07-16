"""`/status`（系統可觀測性頁，Phase1~3）測試。

⛔ credit-safe 鐵律：`/status` 只讀既有 cache/ledger/scheduler run log，
不呼叫 Bedrock、不觸發任何連接器真抓取。本檔用 monkeypatch 把
`pipeline.run`/`BedrockClient`/真 `Source.fetch()` 全部替換成「一旦被呼叫就
斷言失敗」的樁，證明 `/status` 完全不碰這些路徑。
"""
from __future__ import annotations

import time

import pytest

from trustforge import web
from trustforge.ingestion.cache import JsonCacheBackend, cache_key
from trustforge.ledger import JsonlLedger
from trustforge.scheduler_log import JsonlSchedulerRunLog, append_scheduler_run


@pytest.fixture(autouse=True)
def _reset_status_module_state():
    """`_status_rate_buckets`/`_status_cache` 是 module 級共用狀態，比照既有
    `web._rate_buckets.clear()` 慣例，每個測試前重置，避免跨測試互相汙染。"""
    web._status_rate_buckets.clear()
    web._status_cache["expires_at"] = 0.0
    web._status_cache["html"] = ""
    yield
    web._status_rate_buckets.clear()
    web._status_cache["expires_at"] = 0.0
    web._status_cache["html"] = ""


@pytest.fixture
def json_cache_backend(tmp_path, monkeypatch):
    """`/status` 用 `CACHE_BACKEND=json` 且指向隔離的 tmp_path，避免碰到真
    DynamoDB / 汙染開發者本機的 out/connector_cache/。"""
    monkeypatch.setenv("CACHE_BACKEND", "json")
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path))
    return JsonCacheBackend()


# ---------------------------------------------------------------------------
# 基本 200 + 內容
# ---------------------------------------------------------------------------

def test_status_route_returns_200(json_cache_backend):
    code, body = web._handle_status(client_ip="1.2.3.4")
    assert code == 200
    assert "系統狀態" in body


def test_status_page_shows_version_and_mode_info(json_cache_backend, monkeypatch):
    monkeypatch.setattr(web, "VERSION", "9.9.9-test")
    code, body = web._handle_status(client_ip="1.2.3.5")
    assert code == 200
    assert "9.9.9-test" in body
    assert "HAS_BEDROCK" in body or "Bedrock" in body


def test_status_page_shows_uptime(json_cache_backend):
    code, body = web._handle_status(client_ip="1.2.3.6")
    assert code == 200
    assert "運行時間" in body


def test_status_page_links_to_costs(json_cache_backend):
    _, body = web._handle_status(client_ip="1.2.3.7")
    assert 'href="/costs"' in body


def test_homepage_has_status_link(json_cache_backend):
    """首頁 header 應有連到 /status 的連結（比照既有 /costs 連結慣例）。"""
    assert 'href="/status"' in web.render_page("")


# ---------------------------------------------------------------------------
# DynamoDB／cache backend 連線探測：失敗要優雅降級，不炸頁面
# ---------------------------------------------------------------------------

def test_status_page_shows_connected_when_cache_backend_healthy(json_cache_backend):
    _, body = web._handle_status(client_ip="2.2.2.2")
    assert "connected" in body
    assert "disconnected" not in body


def test_status_page_degrades_gracefully_when_cache_backend_broken(monkeypatch, tmp_path):
    """cache backend `get()` 丟例外（如 DynamoDB 缺憑證/表未建）→ 頁面顯示
    disconnected，不 raise、不回 500。"""
    monkeypatch.setenv("TRUSTFORGE_COST_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))

    class BrokenBackend:
        def get(self, key, *, consistent_read=False):
            raise RuntimeError("AccessDeniedException：dynamodb:GetItem 被拒")

        def set(self, key, docs, fetched_at, ttl_seconds=None):
            raise RuntimeError("不該被呼叫")

    # web.py 內部用延遲匯入 `from .ingestion.cache import get_cache_backend`，
    # 需在 ingestion.cache 模組層級 patch 才會被拿到。
    import trustforge.ingestion.cache as cache_mod
    monkeypatch.setattr(cache_mod, "get_cache_backend", lambda: BrokenBackend())

    code, body = web._handle_status(client_ip="2.2.2.3")
    assert code == 200  # 不因為 backend 掛掉就整頁 500
    assert "disconnected" in body


def test_status_page_shows_connected_when_probe_key_is_cache_miss(monkeypatch, tmp_path):
    """探測 key 在 backend 上查無資料（`get()` 回 `None`，非例外）就是正常
    cache-miss，代表 backend 讀寫路徑本身是通的 → 應顯示 connected，不是
    disconnected。"""
    monkeypatch.setenv("TRUSTFORGE_COST_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))

    class MissBackend:
        def get(self, key, *, consistent_read=False):
            return None  # 查無此 key，正常 cache-miss，不是錯誤

        def set(self, key, docs, fetched_at, ttl_seconds=None):
            raise RuntimeError("不該被呼叫")

    import trustforge.ingestion.cache as cache_mod
    monkeypatch.setattr(cache_mod, "get_cache_backend", lambda: MissBackend())

    code, body = web._handle_status(client_ip="2.2.2.4")
    assert code == 200
    assert "connected" in body
    assert "disconnected" not in body


def test_status_page_shows_disconnected_on_real_client_error(monkeypatch, tmp_path):
    """`get()` 丟真的 `botocore.exceptions.ClientError`（如連線/權限錯誤）→
    仍要顯示 disconnected，不能因為修探測 key 就連真的斷線都吞掉不報。"""
    from botocore.exceptions import ClientError

    monkeypatch.setenv("TRUSTFORGE_COST_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))

    class ClientErrorBackend:
        def get(self, key, *, consistent_read=False):
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}},
                "GetItem",
            )

        def set(self, key, docs, fetched_at, ttl_seconds=None):
            raise RuntimeError("不該被呼叫")

    import trustforge.ingestion.cache as cache_mod
    monkeypatch.setattr(cache_mod, "get_cache_backend", lambda: ClientErrorBackend())

    code, body = web._handle_status(client_ip="2.2.2.5")
    assert code == 200
    assert "disconnected" in body


def test_status_page_probe_never_passes_empty_string_key_parts(monkeypatch, tmp_path):
    """回歸鎖（生產 bug）：探測 key 的 `source_id`／`coin` 兩段都必須非空字串。

    `DynamoDBCache` 表結構 PK=`source_id`、SK=`coin`，DynamoDB 對空字串 key
    屬性一律回 `ValidationException`（`GetItem`），跟「backend 真的連不上」
    是兩回事，卻曾經被 `/status` 誤判成 disconnected（CEO 生產親測抓到：
    探測傳了空字串 coin，實際 cache 完全正常）。這裡用一個模擬 DynamoDB
    真實行為的假 backend（空字串 key 段落 → 丟 `ValidationException`
    `ClientError`，否則回 `None`）直接驗證：修好之後探測必須顯示
    connected，不能再誤報 disconnected。"""
    from botocore.exceptions import ClientError

    monkeypatch.setenv("TRUSTFORGE_COST_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))

    class DynamoLikeBackend:
        """比照 `DynamoDBCache._split_key` + 真 DynamoDB 對空字串 key 屬性
        的驗證行為：`source_id`／`coin` 任一段是空字串就丟
        `ValidationException`，兩段都非空則視為正常 cache-miss（回 `None`）。
        """

        def get(self, key, *, consistent_read=False):
            source_id, _, coin = key.partition(":")
            if source_id == "" or coin == "":
                raise ClientError(
                    {
                        "Error": {
                            "Code": "ValidationException",
                            "Message": (
                                "The AttributeValue for a key attribute cannot "
                                "contain an empty string value. Key: coin"
                            ),
                        }
                    },
                    "GetItem",
                )
            return None

        def set(self, key, docs, fetched_at, ttl_seconds=None):
            raise RuntimeError("不該被呼叫")

    import trustforge.ingestion.cache as cache_mod
    monkeypatch.setattr(cache_mod, "get_cache_backend", lambda: DynamoLikeBackend())

    code, body = web._handle_status(client_ip="2.2.2.6")
    assert code == 200
    assert "connected" in body
    assert "disconnected" not in body


# ---------------------------------------------------------------------------
# 成本摘要：重用既有 get_ledger().summary()，零新查詢
# ---------------------------------------------------------------------------

def test_status_page_shows_cost_summary(json_cache_backend, monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_COST_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    JsonlLedger().append({
        "run_id": "r1", "ts": "2026-01-01T00:00:00+00:00",
        "total_cost_usd": 0.0042, "calls": [{"model": "m", "cost_usd": 0.0042}],
    })
    _, body = web._handle_status(client_ip="3.3.3.3")
    assert "成本摘要" in body
    assert "0.0042" in body


def test_status_page_shows_model_token_detail_table(json_cache_backend, monkeypatch, tmp_path):
    """成本會計階段1：`/status`「成本摘要」也要含 tokens 明細表（與 `/costs`
    共用同一份 `_render_model_token_table`）。"""
    monkeypatch.setenv("TRUSTFORGE_COST_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    JsonlLedger().append({
        "ts": "2026-01-01T00:00:00+00:00", "total_cost_usd": 0.005,
        "calls": [{"model": "haiku", "tokens_in": 300, "tokens_out": 60, "cost_usd": 0.005}],
    })
    _, body = web._handle_status(client_ip="7.7.7.1")
    assert "300" in body
    assert "60" in body


# ---------------------------------------------------------------------------
# 資料鮮度矩陣（Phase2）
# ---------------------------------------------------------------------------

def test_status_page_shows_freshness_matrix(json_cache_backend):
    json_cache_backend.set(cache_key("coindesk", "BTC"), [], fetched_at=time.time())
    _, body = web._handle_status(client_ip="4.4.4.4")
    assert "資料鮮度矩陣" in body
    assert "coindesk" in body
    assert "BTC" in body


# ---------------------------------------------------------------------------
# 排程持久化（Phase3）：讀「最近排程執行」
# ---------------------------------------------------------------------------

def test_status_page_shows_recent_scheduler_run(json_cache_backend, monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "runs.jsonl"))
    append_scheduler_run({
        "ts": "2026-06-01T00:00:00+00:00", "success_count": 7,
        "failure_count": 1, "failures": ["reddit-bitcoin:ETH"], "total_docs": 42,
    })
    _, body = web._handle_status(client_ip="5.5.5.5")
    assert "最近排程執行" in body
    assert "reddit-bitcoin:ETH" in body


def test_status_page_shows_placeholder_when_no_scheduler_run(json_cache_backend, monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "empty.jsonl"))
    _, body = web._handle_status(client_ip="5.5.5.6")
    assert "尚無排程執行紀錄" in body


# ---------------------------------------------------------------------------
# 成本會計階段2：連接器用量表
# ---------------------------------------------------------------------------

def test_status_page_shows_connector_usage_table(json_cache_backend, monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "runs.jsonl"))
    append_scheduler_run({
        "ts": "2026-06-01T00:00:00+00:00", "success_count": 4, "failure_count": 0,
        "failures": [], "total_docs": 10,
        "source_calls": {"coingecko-price": 3, "coindesk": 1},
    })
    _, body = web._handle_status(client_ip="6.6.6.1")
    assert "連接器用量" in body
    # codex HIGH（#24、PR #41）修正後：3 個 coingecko-* source 合併成一行
    # 「共用 demo key」，不逐 source 顯示，也不顯示配額使用%（rolling window
    # 無法代表月曆月配額）。
    assert "coingecko-price" in body  # 出現在合併列的成員清單裡
    assert "共用 demo key" in body
    assert "coindesk" in body
    assert "配額使用%" not in body
    assert "%" not in body.split("連接器用量")[1].split("資料鮮度矩陣")[0]
    # coindesk 無官方配額 → 誠實標示，非假造百分比
    assert "無官方公開量化硬配額" in body
    # free tier 恆 $0，但仍顯示真實用量次數（誠實原則，非隱藏用量）
    assert "$0.00" in body


def test_status_page_connector_usage_aggregates_coingecko_shared_pool(
    json_cache_backend, monkeypatch, tmp_path
):
    """codex HIGH（#24、PR #41）回歸鎖：3 個 coingecko-* source 各自用量都要
    合併加總顯示在同一列（呼叫數加總），不能逐 source 分開顯示成各自獨立
    的配額使用率（那會嚴重低估共用同一組 key 的真實用量）。"""
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "runs.jsonl"))
    append_scheduler_run({
        "ts": "2026-06-01T00:00:00+00:00", "success_count": 3, "failure_count": 0,
        "failures": [], "total_docs": 3,
        "source_calls": {
            "coingecko-price": 4000, "coingecko-sentiment": 4000, "coingecko-dev": 4000,
        },
    })
    _, body = web._handle_status(client_ip="6.6.6.6")
    # 3 源合計 12000（超過官方 10,000/月配額）——不能被拆成三個各 <100% 的
    # 「安全」數字掩蓋掉真實已超額的事實；因為完全不顯示%，天然不會誤導。
    assert "<td>CoinGecko（price + sentiment + dev，共用 demo key）</td><td>12000</td>" in body
    # 回歸鎖：不會出現「各自 40%」這種逐 source 拆開的假象
    assert "40.00%" not in body
    assert "120.00%" not in body


def test_status_page_connector_usage_shows_registered_sources_with_zero_when_no_scheduler_run(
    json_cache_backend, monkeypatch, tmp_path
):
    """尚無排程執行紀錄時，仍列出成本模型登記過的全部連接器（用量 0），
    而不是整段隱藏——讓維運者一眼看到「哪些來源還沒被排程跑過」。"""
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "empty.jsonl"))
    _, body = web._handle_status(client_ip="6.6.6.2")
    assert "連接器用量" in body
    assert "<td>coindesk</td><td>0</td>" in body


def test_render_connector_usage_table_placeholder_when_registry_and_usage_both_empty(monkeypatch):
    """防禦性分支（目前 CONNECTOR_COST_MODEL 恆非空，正常不會走到）：登記表
    與用量都空時要顯示明確的「無資料」訊息，不是空表格。"""
    monkeypatch.setattr(web, "CONNECTOR_COST_MODEL", {})
    monkeypatch.setattr(web, "_get_connector_usage_summary", lambda records=None: {})
    body = web._render_connector_usage_table()
    assert "尚無排程執行紀錄，無連接器用量可顯示" in body


def test_status_page_connector_usage_aggregates_across_multiple_runs(
    json_cache_backend, monkeypatch, tmp_path
):
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "runs.jsonl"))
    append_scheduler_run({
        "ts": "2026-06-01T00:00:00+00:00", "success_count": 1, "failure_count": 0,
        "failures": [], "total_docs": 1, "source_calls": {"coindesk": 2},
    })
    append_scheduler_run({
        "ts": "2026-06-01T01:00:00+00:00", "success_count": 1, "failure_count": 0,
        "failures": [], "total_docs": 1, "source_calls": {"coindesk": 3},
    })
    _, body = web._handle_status(client_ip="6.6.6.3")
    # 兩輪加總 = 5 次
    assert "<td>coindesk</td><td>5</td>" in body


def test_status_page_connector_usage_never_scans_full_history(
    json_cache_backend, monkeypatch, tmp_path
):
    """回歸測試：`/status` 讀連接器用量走 `recent()`（O(1)-相容），不掃描
    完整排程歷史（呼應 iron rule「O(1) 規則不能破壞」）。"""
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "runs.jsonl"))
    for i in range(50):
        append_scheduler_run({
            "ts": f"2026-06-{(i % 28) + 1:02d}T00:00:00+00:00",
            "success_count": 1, "failure_count": 0, "failures": [], "total_docs": 1,
            "source_calls": {"coindesk": 1},
        })

    original_read_all = JsonlSchedulerRunLog.read_all
    calls = {"n": 0}

    def spy(self):
        calls["n"] += 1
        return original_read_all(self)

    monkeypatch.setattr(JsonlSchedulerRunLog, "read_all", spy)
    web._handle_status(client_ip="6.6.6.4")
    assert calls["n"] == 0


def test_status_page_recent_called_once_per_render_shared_across_sections(
    json_cache_backend, monkeypatch, tmp_path
):
    """codex MEDIUM（PR #41）回歸鎖：「連接器用量」表 + 「快取節省」卡都要
    彙總 `SchedulerRunLog.recent()`，一次 `/status` render 只該呼叫一次、
    結果共用給兩處——不是各自獨立呼叫兩次（原本的 bug：DynamoDB backend
    會因此同一次 render 多打一次 GetItem，JSONL backend 多讀一次 window
    檔，沒必要）。"""
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "runs.jsonl"))
    append_scheduler_run({
        "ts": "2026-06-01T00:00:00+00:00", "success_count": 1, "failure_count": 0,
        "failures": [], "total_docs": 1, "source_calls": {"coindesk": 1},
    })

    original_recent = JsonlSchedulerRunLog.recent
    calls = {"n": 0}

    def spy(self, n=30):
        calls["n"] += 1
        return original_recent(self, n)

    monkeypatch.setattr(JsonlSchedulerRunLog, "recent", spy)
    _, body = web._handle_status(client_ip="6.6.6.7")

    assert calls["n"] == 1, f"recent() 該只被呼叫一次（共用），實際 {calls['n']} 次"
    assert "連接器用量" in body
    assert "快取節省" in body


# ---------------------------------------------------------------------------
# 成本會計階段3：快取節省估算卡
# ---------------------------------------------------------------------------

def test_status_page_shows_cache_savings_card(json_cache_backend, monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "runs.jsonl"))
    _, body = web._handle_status(client_ip="6.6.6.5")
    assert "快取節省" in body
    assert "估算" in body
    assert "算式" in body


def test_cache_savings_formula_matches_analyze_and_scheduler_counts(
    json_cache_backend, monkeypatch, tmp_path
):
    """`_render_cache_savings_card()` 算式：若無快取 ≈ analyze 次數 × 已知
    連接器來源數；實際 = scheduler 呼叫加總；省下 = max(0, 若無快取 − 實際)。"""
    monkeypatch.setenv("TRUSTFORGE_SCHEDULER_RUN_LOG_PATH", str(tmp_path / "runs.jsonl"))
    from trustforge.cost_model import CONNECTOR_COST_MODEL

    web._analyze_service_count = 0
    web._record_analyze_service_calls(2)
    append_scheduler_run({
        "ts": "2026-06-01T00:00:00+00:00", "success_count": 1, "failure_count": 0,
        "failures": [], "total_docs": 1, "source_calls": {"coindesk": 3},
    })

    n_sources = len(CONNECTOR_COST_MODEL)
    expected_would_be = 2 * n_sources
    expected_saved = max(0, expected_would_be - 3)

    body = web._render_cache_savings_card()
    assert f"省下約 {expected_saved} 次" in body
    assert f"＝ {expected_would_be} 次" in body
    web._analyze_service_count = 0  # 測試後歸零，避免汙染其他測試（module 級狀態）


def test_analyze_service_counter_increments_only_on_real_or_live(monkeypatch):
    """離線示範（樣本資料）不計數，只有 `real`/`live` 真連接器路徑才計數，
    否則快取節省估算會被離線 demo 流量汙染。"""
    web._analyze_service_count = 0
    assert web._get_analyze_service_count() == 0
    web._record_analyze_service_calls(1)
    assert web._get_analyze_service_count() == 1
    web._record_analyze_service_calls(2)
    assert web._get_analyze_service_count() == 3
    web._analyze_service_count = 0


# ---------------------------------------------------------------------------
# 頁面級 ~30s TTL 快取
# ---------------------------------------------------------------------------

def test_status_page_cached_within_ttl(json_cache_backend, monkeypatch):
    calls = {"n": 0}
    real_render = web._render_status_page

    def counting_render():
        calls["n"] += 1
        return real_render()

    monkeypatch.setattr(web, "_render_status_page", counting_render)

    web._handle_status(client_ip="6.6.6.6")
    web._handle_status(client_ip="6.6.6.7")  # 不同 IP，仍應共用同一份快取
    assert calls["n"] == 1


def test_status_page_recomputed_after_ttl_expires(json_cache_backend, monkeypatch):
    calls = {"n": 0}
    real_render = web._render_status_page

    def counting_render():
        calls["n"] += 1
        return real_render()

    monkeypatch.setattr(web, "_render_status_page", counting_render)

    web._handle_status(client_ip="6.6.6.8")
    web._status_cache["expires_at"] = time.time() - 1  # 模擬 TTL 已過期
    web._handle_status(client_ip="6.6.6.8")
    assert calls["n"] == 2


def test_render_status_page_cached_single_flight_on_concurrent_expiry(
    json_cache_backend, monkeypatch
):
    """cache-stampede 回歸（MEDIUM）：cache 過期瞬間多執行緒併發打
    `_render_status_page_cached()`（模擬 `ThreadingHTTPServer` 底下、分散
    IP 打進來、per-IP 限流擋不住的 thundering herd 情境）——必須只有一個
    請求真的跑 `_render_status_page()`（後端讀取 freshness matrix + ledger
    summary + dual scheduler run log），其餘請求直接吃新快取，不各自重算。

    用 `time.sleep()` 刻意拉寬 `_render_status_page()` 的執行視窗，確保
    其他執行緒真的會在它執行期間排隊等鎖，而不是僥倖沒撞在一起。
    """
    import threading

    real_render = web._render_status_page
    calls = {"n": 0}
    calls_lock = threading.Lock()

    def slow_counting_render():
        with calls_lock:
            calls["n"] += 1
        time.sleep(0.05)  # 拉寬執行視窗，逼其他執行緒排隊等鎖
        return real_render()

    monkeypatch.setattr(web, "_render_status_page", slow_counting_render)

    # 先讓 cache 有效過一次（確保 _render_status_page 之後真的可以正常跑），
    # 再手動讓它過期，模擬「TTL 剛過期的那一瞬間」。
    web._render_status_page_cached()
    calls["n"] = 0
    web._status_cache["expires_at"] = time.time() - 1

    barrier = threading.Barrier(20)
    results: list[str] = []
    results_lock = threading.Lock()

    def worker():
        barrier.wait()  # 盡量讓所有執行緒同時衝進 cache-miss 分支
        html_out = web._render_status_page_cached()
        with results_lock:
            results.append(html_out)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert calls["n"] == 1  # single-flight：只有一個請求真的重算
    assert len(results) == 20
    assert all(r == results[0] for r in results)  # 其餘全部拿到同一份新快取


# ---------------------------------------------------------------------------
# per-IP 限流：獨立於 live/real 限流的 bucket
# ---------------------------------------------------------------------------

def test_status_rate_limit_triggers_429_after_max(json_cache_backend):
    ip = "7.7.7.7"
    for _ in range(web._STATUS_RATE_MAX):
        code, _ = web._handle_status(client_ip=ip)
        assert code == 200
    code, body = web._handle_status(client_ip=ip)
    assert code == 429
    assert "請求過於頻繁" in body


def test_status_rate_limit_independent_from_live_rate_limit(json_cache_backend):
    """/status 限流用獨立 bucket，不消耗/受 live 限流 bucket 影響。"""
    ip = "7.7.7.8"
    web._rate_buckets.clear()
    web._real_rate_buckets.clear()


def test_read_only_endpoint_rate_limits_are_independent():
    """Normal navigation must not make overview/status/history rate-limit each other."""
    ip = "7.7.7.80"
    for _ in range(web._STATUS_RATE_MAX):
        web._check_status_rate_limit(ip, "overview")
    with pytest.raises(web.TooManyRequests):
        web._check_status_rate_limit(ip, "overview")

    # The same browser can still load every other read-only screen.
    web._check_status_rate_limit(ip, "status-api")
    web._check_status_rate_limit(ip, "history")
    web._check_status_rate_limit(ip, "costs")
    # 灌爆 live 限流 bucket
    for _ in range(web._RATE_MAX):
        web._check_live_rate_limit(ip)
    with pytest.raises(web.TooManyRequests):
        web._check_live_rate_limit(ip)

    # /status 仍應正常放行（獨立 bucket，不受上面影響）
    code, _ = web._handle_status(client_ip=ip)
    assert code == 200
    web._rate_buckets.clear()
    web._real_rate_buckets.clear()


def test_status_rate_limit_bucket_count_bounded_under_many_distinct_ips(monkeypatch):
    """Scalability 回歸：大量不同 IP 打 /status 限流不該讓 `_status_rate_buckets`
    這個 dict 本身無限增長（記憶體耗盡向量，防 DoS 反成 DoS）——bucket 數
    必須有上限。"""
    monkeypatch.setattr(web, "_RATE_LIMIT_MAX_TRACKED_IPS", 50)
    web._status_rate_buckets.clear()

    for i in range(500):
        web._check_status_rate_limit(f"10.0.{i // 256}.{i % 256}")

    assert len(web._status_rate_buckets) <= 50
    web._status_rate_buckets.clear()


def test_live_rate_limit_bucket_count_bounded_under_many_distinct_ips(monkeypatch):
    """同一套上限保護也套用在既有 `_rate_buckets`（live 緊限流），不是只
    修新加的 /status bucket。"""
    monkeypatch.setattr(web, "_RATE_LIMIT_MAX_TRACKED_IPS", 50)
    web._rate_buckets.clear()
    web._real_rate_buckets.clear()

    for i in range(500):
        web._check_live_rate_limit(f"172.16.{i // 256}.{i % 256}")

    assert len(web._rate_buckets) <= 50
    web._rate_buckets.clear()
    web._real_rate_buckets.clear()


def test_real_rate_limit_bucket_count_bounded_under_many_distinct_ips(monkeypatch):
    """同一套上限保護也套用在 codex HIGH 修復新加的 `_real_rate_buckets`
    （real-off 寬鬆限流），避免這個新 bucket 自己變成記憶體耗盡向量。"""
    monkeypatch.setattr(web, "_RATE_LIMIT_MAX_TRACKED_IPS", 50)
    web._rate_buckets.clear()
    web._real_rate_buckets.clear()

    for i in range(500):
        web._check_real_rate_limit(f"172.20.{i // 256}.{i % 256}")

    assert len(web._real_rate_buckets) <= 50
    web._rate_buckets.clear()
    web._real_rate_buckets.clear()


def test_status_rate_limit_state_preserved_when_under_bucket_cap(monkeypatch):
    """未達 bucket 數上限時，`_evict_stale_rate_buckets` 直接短路返回，不影響
    任何既有 IP 的限流狀態——驗證新加的上限保護邏輯在正常（未觸頂）情境下
    零行為改變，不會誤傷。"""
    monkeypatch.setattr(web, "_RATE_LIMIT_MAX_TRACKED_IPS", 1000)
    web._status_rate_buckets.clear()

    active_ip = "9.9.9.9"
    for _ in range(web._STATUS_RATE_MAX):
        web._check_status_rate_limit(active_ip)
    with pytest.raises(web.TooManyRequests):
        web._check_status_rate_limit(active_ip)

    for i in range(50):
        web._check_status_rate_limit(f"8.8.{i // 256}.{i % 256}")

    with pytest.raises(web.TooManyRequests):
        web._check_status_rate_limit(active_ip)
    web._status_rate_buckets.clear()


def test_evict_stale_rate_buckets_prefers_fully_expired_ips_over_active_ones():
    """達上限時，優先清「視窗內完全無動靜」的 IP，不動仍活躍的 IP。"""
    now = time.time()
    buckets = {"expired-ip": [now - 1000.0], "active-ip": [now]}
    web._evict_stale_rate_buckets(buckets, window=30, now=now, max_tracked_ips=2)
    assert "expired-ip" not in buckets
    assert "active-ip" in buckets


def test_evict_stale_rate_buckets_falls_back_to_lru_when_all_active():
    """掃完仍超過上限（沒有完全無動靜的 IP 可清）時，退化成逐出最舊活動
    時間的 IP，直到降回上限之下。"""
    now = time.time()
    buckets = {"older-active": [now - 5.0], "newer-active": [now]}
    web._evict_stale_rate_buckets(buckets, window=30, now=now, max_tracked_ips=2)
    assert "older-active" not in buckets
    assert "newer-active" in buckets


def test_evict_stale_rate_buckets_noop_when_under_cap():
    """未達上限時完全不動 dict（零額外成本，正常情境行為不變）。"""
    now = time.time()
    buckets = {"a": [now - 1000.0], "b": [now]}
    web._evict_stale_rate_buckets(buckets, window=30, now=now, max_tracked_ips=10)
    assert set(buckets) == {"a", "b"}


def test_status_rate_limit_scoped_per_ip(json_cache_backend):
    ip_a, ip_b = "7.7.7.9", "7.7.7.10"
    for _ in range(web._STATUS_RATE_MAX):
        code, _ = web._handle_status(client_ip=ip_a)
        assert code == 200
    code, _ = web._handle_status(client_ip=ip_a)
    assert code == 429

    # 另一個 IP 不受影響
    code, _ = web._handle_status(client_ip=ip_b)
    assert code == 200


# ---------------------------------------------------------------------------
# credit-safe：/status 完全不觸發 Bedrock / 真連接器 API 呼叫
# ---------------------------------------------------------------------------

def test_status_route_never_calls_pipeline_run(json_cache_backend, monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("/status 不該呼叫 pipeline.run()")

    monkeypatch.setattr("trustforge.pipeline.run", _boom)
    monkeypatch.setattr(web, "run", _boom)

    code, _ = web._handle_status(client_ip="8.8.8.8")
    assert code == 200


def test_status_route_never_calls_real_source_fetch(json_cache_backend, monkeypatch):
    """`get_freshness_snapshot()` 只讀 cache，`/status` 全鏈路都不該呼叫任何
    真 `Source.fetch()`（credit-safe：/status 探測不可觸發真抓取）。"""
    from trustforge.ingestion.base import Source

    original_fetch = Source.fetch if hasattr(Source, "fetch") else None

    def _boom(self, query, coin=""):
        raise AssertionError(f"/status 不該呼叫真 Source.fetch()（{self})")

    monkeypatch.setattr(Source, "fetch", _boom, raising=False)
    try:
        code, _ = web._handle_status(client_ip="8.8.8.9")
        assert code == 200
    finally:
        if original_fetch is not None:
            monkeypatch.setattr(Source, "fetch", original_fetch, raising=False)


# ---------------------------------------------------------------------------
# CEO 決策（PR #39，收斂）：theme toggle 切換機制整個拆除（rtok render
# cache 是 process-local，重啟/部署/多 worker/TTL 過期即 cache miss，會把
# 使用者已產出的報告永久弄丟——「切主題不重跑 pipeline」與「不遺失報告」
# 在無狀態 SSR 本質難兩全）。`/status` 不再接受 theme 參數，固定渲染
# dark；`var(--tf-*)` token 架構仍保留，等 #20（結果持久化）做對後再重新
# 開放。
# ---------------------------------------------------------------------------

def test_handle_status_always_renders_dark_no_theme_param():
    """`_handle_status` 不再接受 theme 參數，只回傳固定 dark 的頁面。"""
    _, body = web._handle_status(client_ip="9.9.9.1")
    assert 'data-theme="dark"' in body


def test_status_route_via_do_get_ignores_any_cookie_and_stays_dark():
    """端到端：即使帶 `Cookie: tf_theme=light`（theme toggle 已拆除，理論上
    不會再有呼叫端設這個 cookie），`/status` 頁面仍固定渲染 dark，不因
    cookie 值而改變（no-op，非白名單/驗證問題）。"""
    from io import BytesIO
    from email.message import Message

    h = web.Handler.__new__(web.Handler)
    h.client_address = ("127.0.0.1", 12345)
    h.path = "/status"
    h.wfile = BytesIO()
    headers = Message()
    headers["Cookie"] = "tf_theme=light"
    h.headers = headers

    captured = []
    h.send_response = lambda code: captured.append(("status", code))
    h.send_header = lambda name, val: captured.append(("header", name, val))
    h.end_headers = lambda: None

    h.do_GET()

    body = h.wfile.getvalue().decode("utf-8")
    assert 'data-theme="dark"' in body


def test_theme_route_no_longer_exists():
    """`/theme` 路由已整個拆除——命中 `/theme` 應落回一般路由分派（不存在
    專屬處理，回 404），不再有任何 rtok/next/cookie 相關邏輯可觸發。"""
    from io import BytesIO

    h = web.Handler.__new__(web.Handler)
    h.client_address = ("127.0.0.1", 12345)
    h.path = "/theme?to=light&rtok=whatever"
    h.wfile = BytesIO()

    captured = []
    h.send_response = lambda code: captured.append(("status", code))
    h.send_header = lambda name, val: captured.append(("header", name, val))
    h.end_headers = lambda: None

    h.do_GET()

    statuses = [c[1] for c in captured if c[0] == "status"]
    assert statuses == [404]
