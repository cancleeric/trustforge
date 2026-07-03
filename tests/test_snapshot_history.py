"""task #26（docs/PLAN-multicore-worldfirst.md，新核心#1 持久化寫入基礎）：
`--snapshot` 按日累積歷史快照 + `get_trust_history()` 讀取 helper 測試。

⛔ credit-safe 鐵律：全程 `pipeline.run()` 被 monkeypatch 成樁，不觸發任何
連接器真抓取、不打真 Bedrock、不打真 DynamoDB——比照 `test_home_overview.py`
既有 `--snapshot` 測試慣例，`CACHE_BACKEND=json` 隔離到 tmp_path。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pytest

from scripts import fetch_scheduler
from trustforge.ingestion.cache import (
    CacheWriteResult,
    JsonCacheBackend,
    TRUST_SNAPSHOT_HISTORY_TTL_SECONDS,
    TRUST_SNAPSHOT_SOURCE,
    cache_get,
    cache_key,
    cache_set_if_newer,
    get_trust_history,
    snapshot_history_date,
    trust_snapshot_history_key,
)
from trustforge.schema import Evidence


@pytest.fixture
def json_cache_backend(tmp_path, monkeypatch):
    """`CACHE_BACKEND=json` 且指向隔離的 tmp_path，避免碰到真 DynamoDB。"""
    monkeypatch.setenv("CACHE_BACKEND", "json")
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path))
    return JsonCacheBackend()


def _fake_report(coin: str, confidence: float = 0.62, calibrated: float = 0.55,
                  direction: str = "偏多", decision_state: str = "normal",
                  generated_at: str = "2026-07-01T00:00:00Z"):
    class _FakeReport:
        pass

    r = _FakeReport()
    r.coin = coin
    r.confidence = confidence
    r.calibrated_confidence = calibrated
    r.direction = direction
    r.decision_state = decision_state
    r.generated_at = generated_at
    return r


def _utc_ts(iso: str) -> float:
    """`"YYYY-MM-DDTHH:MM:SS"` → epoch 秒，固定當 UTC 解讀（不透過
    `time.mktime()` 本機時區換算，避免測試結果隨跑測機器時區漂移）。"""
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def _fake_evidence_with_reputation(
    source: str, prior: float, final: float, agree_n: int = 0, contradict_n: int = 0,
) -> Evidence:
    """建一筆真 `Evidence`（比照 `agent.orchestrator._scored_to_evidence()`
    把 W2 reputation trace 併入 `trust_components` 的實際欄位命名），供
    `_reputation_summary()` 測試用，不是虛構的資料形狀。"""
    return Evidence(
        source=source,
        fetched_at="2026-07-01T00:00:00Z",
        content_reference="ref",
        related_claim="claim",
        trust_components={
            "reputation": 0.6,
            "reputation_prior": prior,
            "reputation_final": final,
            "reputation_agree_n": agree_n,
            "reputation_contradict_n": contradict_n,
            "reputation_iterations_run": 1,
        },
    )


# ---------------------------------------------------------------------------
# `_reputation_summary()` / `_snapshot_dict()` W2 trace 擷取
# ---------------------------------------------------------------------------

def test_reputation_summary_extracts_per_source_trace_and_dedups():
    evidence = [
        _fake_evidence_with_reputation("coingecko", prior=0.7, final=0.75, agree_n=2),
        # 同來源第二筆 Evidence（同一份報告內，來源信譽 trace 廣播到每筆
        # claim，值理論上相同）——只該留一份代表值。
        _fake_evidence_with_reputation("coingecko", prior=0.7, final=0.75, agree_n=2),
        _fake_evidence_with_reputation("some_news", prior=0.5, final=0.4, contradict_n=1),
    ]
    summary = fetch_scheduler._reputation_summary(evidence)
    assert set(summary.keys()) == {"coingecko", "some_news"}
    assert summary["coingecko"] == {
        "prior": 0.7, "final": 0.75, "delta": 0.05, "agree_n": 2, "contradict_n": 0,
    }
    assert summary["some_news"] == {
        "prior": 0.5, "final": 0.4, "delta": -0.1, "agree_n": 0, "contradict_n": 1,
    }


def test_reputation_summary_skips_evidence_without_trace():
    """沒開 W2（`dynamic_reputation=False`）或舊呼叫端的 `Evidence` 沒有
    `reputation_prior`/`reputation_final` 鍵時，該筆直接跳過，不補假值。"""
    evidence = [Evidence(source="x", fetched_at="", content_reference="", related_claim="")]
    assert fetch_scheduler._reputation_summary(evidence) == {}


def test_snapshot_dict_includes_reputation_trace_when_evidence_has_it():
    report = _fake_report("BTC")
    evidence = [_fake_evidence_with_reputation("coingecko", prior=0.7, final=0.75, agree_n=2)]
    snap = fetch_scheduler._snapshot_dict("BTC", report, evidence)
    assert snap["reputation_trace"] == {
        "coingecko": {"prior": 0.7, "final": 0.75, "delta": 0.05,
                       "agree_n": 2, "contradict_n": 0},
    }


def test_snapshot_dict_omits_reputation_trace_key_when_no_evidence():
    """向後相容：`evidence=None`/空清單時完全不新增 `reputation_trace` 鍵。"""
    report = _fake_report("BTC")
    assert "reputation_trace" not in fetch_scheduler._snapshot_dict("BTC", report, None)
    assert "reputation_trace" not in fetch_scheduler._snapshot_dict("BTC", report, [])


# ---------------------------------------------------------------------------
# 按日累積歷史（`run_snapshot()` 的歷史 key 寫入）
# ---------------------------------------------------------------------------

def _patch_pipeline_run(monkeypatch, confidence=0.71):
    def fake_pipeline_run(coin, query, qtype, **kwargs):
        report = _fake_report(coin, confidence=confidence, calibrated=0.6,
                               direction="偏多", decision_state="normal")
        evidence = [_fake_evidence_with_reputation("coingecko", prior=0.6, final=0.6)]
        return report, evidence, object()

    import trustforge.pipeline as pipeline_mod
    monkeypatch.setattr(pipeline_mod, "run", fake_pipeline_run)


def test_snapshot_writes_daily_history_key_alongside_latest(monkeypatch, json_cache_backend):
    """成功路徑除了既有 `__trust_snapshot__:{coin}`，還要多寫一筆按日歷史
    key，且內容跟「最新一筆」一致（含 reputation_trace）。"""
    day1 = _utc_ts("2026-07-01T12:00:00")
    monkeypatch.setattr(time, "time", lambda: day1)
    _patch_pipeline_run(monkeypatch)

    rc = fetch_scheduler.main(["--snapshot", "--coin", "BTC"])
    assert rc == 0

    latest = cache_get(json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, "BTC"))
    history = cache_get(
        json_cache_backend, trust_snapshot_history_key("BTC", "2026-07-01"),
    )
    assert latest is not None
    assert history is not None
    assert history["docs"][0] == latest["docs"][0]
    assert history["docs"][0]["reputation_trace"] == {
        "coingecko": {"prior": 0.6, "final": 0.6, "delta": 0.0, "agree_n": 0, "contradict_n": 0},
    }


def test_snapshot_same_day_reruns_upsert_not_duplicate(monkeypatch, json_cache_backend):
    """同一天多次跑 `--snapshot`：按日 key 是同一把，第二次覆寫（uPsert），
    不會出現「同一天兩筆」的情況——用 trust_score 變化驗證確實被覆寫。"""
    day1_morning = _utc_ts("2026-07-01T08:00:00")
    day1_evening = _utc_ts("2026-07-01T20:00:00")

    monkeypatch.setattr(time, "time", lambda: day1_morning)
    _patch_pipeline_run(monkeypatch, confidence=0.5)
    assert fetch_scheduler.main(["--snapshot", "--coin", "BTC"]) == 0

    monkeypatch.setattr(time, "time", lambda: day1_evening)
    _patch_pipeline_run(monkeypatch, confidence=0.9)
    assert fetch_scheduler.main(["--snapshot", "--coin", "BTC"]) == 0

    history = get_trust_history("BTC", days=7, backend=json_cache_backend, end_date="2026-07-01")
    assert len(history) == 1
    assert history[0]["date"] == "2026-07-01"
    assert history[0]["trust_score"] == pytest.approx(0.9, abs=1e-6)


def test_snapshot_across_two_days_accumulates_two_history_entries(monkeypatch, json_cache_backend):
    """兩天各跑一次 `--snapshot`（模擬兩天）→ `get_trust_history()` 讀回
    按日期由舊到新排序的兩筆序列，驗證跨日真的累積成歷史，不是覆寫。"""
    day1 = _utc_ts("2026-07-01T12:00:00")
    day2 = _utc_ts("2026-07-02T12:00:00")

    monkeypatch.setattr(time, "time", lambda: day1)
    _patch_pipeline_run(monkeypatch, confidence=0.4)
    assert fetch_scheduler.main(["--snapshot", "--coin", "BTC"]) == 0

    monkeypatch.setattr(time, "time", lambda: day2)
    _patch_pipeline_run(monkeypatch, confidence=0.6)
    assert fetch_scheduler.main(["--snapshot", "--coin", "BTC"]) == 0

    history = get_trust_history("BTC", days=7, backend=json_cache_backend, end_date="2026-07-02")
    assert [h["date"] for h in history] == ["2026-07-01", "2026-07-02"]
    assert history[0]["trust_score"] == pytest.approx(0.4, abs=1e-6)
    assert history[1]["trust_score"] == pytest.approx(0.6, abs=1e-6)


def test_snapshot_history_key_uses_utc_date_not_local_time():
    """日期別嵌用穩定的 UTC 換算，不受本機時區影響——`snapshot_history_date()`
    固定用 `datetime.fromtimestamp(ts, tz=timezone.utc)`。"""
    import datetime as _dt
    ts = _dt.datetime(2026, 7, 1, 23, 30, 0, tzinfo=_dt.timezone.utc).timestamp()
    assert snapshot_history_date(ts) == "2026-07-01"


# ---------------------------------------------------------------------------
# codex HIGH（PR #59 review）：按日歷史單調條件寫入（`cache_set_if_newer()`）
# ---------------------------------------------------------------------------

def test_history_write_skipped_when_incoming_older_than_existing(json_cache_backend):
    """既有值的 `fetched_at` 較新時，incoming（較舊）應被跳過——不寫入、
    當日 key 內容維持既有值不變，且 `result.ok=True`、`result.skipped=True`
    （這不是失敗）。"""
    key = trust_snapshot_history_key("BTC", "2026-07-01")
    newer_snap = {"coin": "BTC", "trust_score": 0.9}
    older_snap = {"coin": "BTC", "trust_score": 0.1}

    result_first = cache_set_if_newer(
        json_cache_backend, key, [newer_snap], fetched_at=2000.0,
        ttl_seconds=TRUST_SNAPSHOT_HISTORY_TTL_SECONDS,
    )
    assert result_first.ok is True
    assert result_first.skipped is False

    result_second = cache_set_if_newer(
        json_cache_backend, key, [older_snap], fetched_at=1000.0,
        ttl_seconds=TRUST_SNAPSHOT_HISTORY_TTL_SECONDS,
    )
    assert result_second.ok is True
    assert result_second.skipped is True

    entry = cache_get(json_cache_backend, key)
    assert entry["docs"][0] == newer_snap
    assert entry["fetched_at"] == 2000.0


def test_history_write_upserts_when_incoming_newer_than_existing(json_cache_backend):
    """正常情況：incoming 比既有值新 → 正常 upsert（覆寫），`skipped=False`。"""
    key = trust_snapshot_history_key("BTC", "2026-07-01")
    older_snap = {"coin": "BTC", "trust_score": 0.1}
    newer_snap = {"coin": "BTC", "trust_score": 0.9}

    result_first = cache_set_if_newer(
        json_cache_backend, key, [older_snap], fetched_at=1000.0,
        ttl_seconds=TRUST_SNAPSHOT_HISTORY_TTL_SECONDS,
    )
    assert result_first.ok is True
    assert result_first.skipped is False

    result_second = cache_set_if_newer(
        json_cache_backend, key, [newer_snap], fetched_at=2000.0,
        ttl_seconds=TRUST_SNAPSHOT_HISTORY_TTL_SECONDS,
    )
    assert result_second.ok is True
    assert result_second.skipped is False

    entry = cache_get(json_cache_backend, key)
    assert entry["docs"][0] == newer_snap
    assert entry["fetched_at"] == 2000.0


def test_history_interleaved_out_of_order_completion_does_not_corrupt_with_stale_value(
    json_cache_backend,
):
    """回歸測試（codex HIGH 根因場景）：模擬 run A（較舊 `fetched_at`）跟
    run B（較新）兩輪排程重疊，而 **A 的歷史寫入在 B 之後才完成**（排程
    重疊/重試/DynamoDB 延遲）。修正前：兩者都是無條件 `cache_set()`，A 最後
    完成就會把當日 key 蓋回 A 的舊快照，即使 B 的值更新、latest 也已經是
    B——歷史序列被舊值靜默污染。修正後：即使 A 的寫入在時間序上「最後才
    執行」，`cache_set_if_newer()` 仍會判斷 A 的 `fetched_at` 比當日既有
    （B 寫入的）值舊而跳過，當日 key 最終必須是 B（較新 `fetched_at`）的
    快照，不能被 A 覆蓋回去。"""
    key = trust_snapshot_history_key("BTC", "2026-07-01")
    run_a_snap = {"coin": "BTC", "trust_score": 0.2, "run": "A-stale"}
    run_b_snap = {"coin": "BTC", "trust_score": 0.8, "run": "B-fresh"}
    run_a_fetched_at = 1_000.0  # 較舊
    run_b_fetched_at = 5_000.0  # 較新

    # B（較新 fetched_at）先完成寫入。
    result_b = cache_set_if_newer(
        json_cache_backend, key, [run_b_snap], fetched_at=run_b_fetched_at,
        ttl_seconds=TRUST_SNAPSHOT_HISTORY_TTL_SECONDS,
    )
    assert result_b.ok is True
    assert result_b.skipped is False

    # A（較舊 fetched_at）的寫入「最後才完成」——這就是 codex 抓到的
    # out-of-order 場景：時間序上 A 在 B 之後執行，但其資料時間戳其實較舊。
    result_a = cache_set_if_newer(
        json_cache_backend, key, [run_a_snap], fetched_at=run_a_fetched_at,
        ttl_seconds=TRUST_SNAPSHOT_HISTORY_TTL_SECONDS,
    )
    assert result_a.ok is True
    assert result_a.skipped is True  # 主動跳過，不是失敗

    # 斷言：當日 key 最終必須是 B（新）的快照，不被 A（舊）覆蓋。
    entry = cache_get(json_cache_backend, key)
    assert entry["docs"][0] == run_b_snap
    assert entry["fetched_at"] == run_b_fetched_at


def test_concurrent_ordinary_set_and_set_if_newer_both_keys_survive(
    json_cache_backend, monkeypatch,
):
    """codex HIGH（PR #59 review 第二輪）回歸測試：`JsonCacheBackend` 所有
    會整檔 read-modify-write 的 mutation（普通 `set()` 跟 `set_if_newer()`）
    必須共用同一把鎖，不能只鎖 `set_if_newer()`——否則普通 `set(key_x)` 可能
    在 `set_if_newer(key_y)` 條件寫入前就 load 到舊檔（不含 `key_y`），
    `set_if_newer` 寫完之後，`set(key_x)` 才拿著手上那份舊 copy 整檔覆寫
    回去，等於把剛寫進去的 `key_y` 直接刪掉。

    用 `_load()` 插一段 `time.sleep()` 製造夠寬的臨界區窗口：讓先開始的
    `set(key_x)` 在（修好後）已經拿到鎖、`_load()` 讀完的當下睡一下，隨後
    才啟動的 `set_if_newer(key_y)` 嘗試搶同一把鎖。修好前：`set()` 沒拿鎖，
    `set_if_newer()` 會在這段睡眠期間整套（load→比較→寫）完整跑完並釋放
    自己的鎖，`set(key_x)` 醒來後用舊 copy 整檔覆寫，`key_y` 就會消失。
    修好後：兩者共用同一把鎖，`set_if_newer(key_y)` 必須排隊等 `set(key_x)`
    整段臨界區（含睡眠）結束才能進入，不會插隊——`key_x`、`key_y` 兩把都要
    活著。"""
    original_load = json_cache_backend._load
    first_call_seen = threading.Event()

    def slow_load():
        data = original_load()
        if not first_call_seen.is_set():
            first_call_seen.set()
            time.sleep(0.2)  # 撐開「已讀到資料、還沒寫回」的臨界區窗口
        return data

    monkeypatch.setattr(json_cache_backend, "_load", slow_load)

    key_x = cache_key("some_ordinary_source", "ETH")
    key_y = trust_snapshot_history_key("BTC", "2026-07-01")
    errors: list[BaseException] = []

    def writer_x():
        try:
            json_cache_backend.set(key_x, [{"v": "x"}], fetched_at=1.0)
        except BaseException as exc:  # noqa: BLE001 — 測試執行緒需要把例外帶回主執行緒斷言
            errors.append(exc)

    def writer_y():
        first_call_seen.wait(timeout=5)  # 確保 writer_x 已經先進入臨界區
        try:
            json_cache_backend.set_if_newer(key_y, [{"v": "y"}], fetched_at=2.0)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t_x = threading.Thread(target=writer_x)
    t_y = threading.Thread(target=writer_y)
    t_x.start()
    t_y.start()
    t_x.join(timeout=5)
    t_y.join(timeout=5)

    assert not errors, f"背景執行緒發生例外：{errors}"
    entry_x = cache_get(json_cache_backend, key_x)
    entry_y = cache_get(json_cache_backend, key_y)
    assert entry_x is not None and entry_x["docs"][0] == {"v": "x"}
    assert entry_y is not None and entry_y["docs"][0] == {"v": "y"}


# ---------------------------------------------------------------------------
# codex HIGH（PR #59 review 第三輪，#1 三表示一致性最終閉合）：完整
# `run_snapshot()` 交錯——latest／history／overview 三個表示要嘛一起贏、
# 要嘛一起因為比既有值舊而跳過，不能出現「某表示被較舊一輪蓋掉、另一個
# 表示卻正確跳過」的表示間矛盾。
# ---------------------------------------------------------------------------

def test_full_run_snapshot_interleaved_out_of_order_run_keeps_all_three_representations_consistent(
    monkeypatch, json_cache_backend,
):
    """模擬 run B（較新 `now`）完整跑完、三個持久化表示（latest／history／
    overview）都寫成功，接著 run A（較舊 `now`）的三個寫入才完成（真實情境
    是排程重疊/重試/DynamoDB 延遲；這裡用「先跑新的、再跑舊的」模擬 A 的
    寫入在 B 之後才完成）。斷言 latest、history、overview 三者最終都必須
    是 B（新）的內容，沒有任何一個被 A（舊）的值覆寫——修正前只有 history
    是 monotonic，latest／overview 還是無條件覆寫，會被 A 蓋回舊值，跟
    history 顯示的新值互相矛盾（#24：使用者看到的「當下」跟「歷史」打架）。
    """
    day = "2026-07-01"
    ts_a = _utc_ts(f"{day}T08:00:00")  # run A：較舊
    ts_b = _utc_ts(f"{day}T20:00:00")  # run B：較新

    # run B（較新）先完整跑完，三個表示都寫成功。
    monkeypatch.setattr(time, "time", lambda: ts_b)
    _patch_pipeline_run(monkeypatch, confidence=0.9)
    assert fetch_scheduler.main(["--snapshot", "--coin", "BTC"]) == 0

    # run A（較舊）的三個寫入「最後才完成」——時間序上在 B 之後才執行，
    # 但資料時間戳其實較舊，理當被全部擋下，不能覆寫任何一個表示。
    monkeypatch.setattr(time, "time", lambda: ts_a)
    _patch_pipeline_run(monkeypatch, confidence=0.1)
    assert fetch_scheduler.main(["--snapshot", "--coin", "BTC"]) == 0

    latest = cache_get(json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, "BTC"))
    history = cache_get(json_cache_backend, trust_snapshot_history_key("BTC", day))
    overview = cache_get(
        json_cache_backend,
        cache_key(fetch_scheduler.TRUST_OVERVIEW_SOURCE, fetch_scheduler.TRUST_OVERVIEW_COIN),
    )

    assert latest is not None and history is not None and overview is not None

    # 三者的 fetched_at 都必須是 B（新）的時間戳，不是 A（舊）的。
    assert latest["fetched_at"] == ts_b
    assert history["fetched_at"] == ts_b
    assert overview["fetched_at"] == ts_b

    # latest／history 的實際內容也必須是 B（confidence=0.9），不是 A（0.1）。
    assert latest["docs"][0]["trust_score"] == pytest.approx(0.9, abs=1e-6)
    assert history["docs"][0]["trust_score"] == pytest.approx(0.9, abs=1e-6)

    # overview blob 的 HTML 也必須顯示 B 的信任分（0.90），不是 A 的（0.10）。
    overview_html = overview["docs"][0]["html"]
    assert "0.90" in overview_html
    assert "0.10" not in overview_html


# ---------------------------------------------------------------------------
# codex HIGH（PR #59 review 第四輪）：per-coin all-or-nothing 收窄——latest
# 這一幣的寫入結果（skipped/error/成功）決定 history／overview 是否處理
# 這一幣，避免單幣內部矛盾。
# ---------------------------------------------------------------------------

def test_latest_skipped_for_one_coin_excludes_it_from_history_and_overview(
    monkeypatch, json_cache_backend,
):
    """混合批次（BTC + ETH）：BTC 的 latest 因為已有更新的並行排程結果而
    單調跳過（模擬另一輪較新的排程已經處理過 BTC），ETH 正常成功。

    per-coin gating 斷言：BTC 這一幣的 history 完全不寫（不留一筆比 latest
    目前實際內容更新、卻沒被 latest 接受的矛盾資料）、總覽 blob 也不含
    BTC 這輪（本該被跳過）的候選值；ETH 三表示都正常反映這輪新值。整體
    回傳碼仍是 0——這是健康的正常情況，不是失敗（見 `run_snapshot()`
    `skipped_coins` 的說明）。
    """
    day = "2026-07-01"
    stale_ts = _utc_ts(f"{day}T08:00:00")
    fresh_ts = _utc_ts(f"{day}T20:00:00")

    # 預先幫 BTC 的「最新一筆」種一筆已經比這輪要寫的還新的既有值——
    # 模擬「另一輪更新的排程已經處理過 BTC」。
    btc_latest_key = cache_key(TRUST_SNAPSHOT_SOURCE, "BTC")
    pre_existing_btc_snap = {
        "coin": "BTC", "trust_score": 0.99, "direction": "既有勝出值",
        "calibrated_confidence": 0.9, "decision_state": "normal",
        "generated_at": "2026-07-01T19:00:00Z",
    }
    json_cache_backend.set(btc_latest_key, [pre_existing_btc_snap], fetched_at=fresh_ts)

    monkeypatch.setattr(time, "time", lambda: stale_ts)
    _patch_pipeline_run(monkeypatch, confidence=0.3)
    assert fetch_scheduler.main(["--snapshot", "--coin", "BTC", "--coin", "ETH"]) == 0

    # BTC：latest 維持既有（較新）值不被覆寫，history 完全沒被建立。
    btc_latest = cache_get(json_cache_backend, btc_latest_key)
    assert btc_latest is not None
    assert btc_latest["docs"][0] == pre_existing_btc_snap
    btc_history = cache_get(json_cache_backend, trust_snapshot_history_key("BTC", day))
    assert btc_history is None

    # ETH：不受 BTC 影響，三表示正常寫入這輪新值。
    eth_latest = cache_get(json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, "ETH"))
    eth_history = cache_get(json_cache_backend, trust_snapshot_history_key("ETH", day))
    assert eth_latest is not None and eth_history is not None
    assert eth_latest["docs"][0]["trust_score"] == pytest.approx(0.3, abs=1e-6)
    assert eth_history["docs"][0]["trust_score"] == pytest.approx(0.3, abs=1e-6)

    # 總覽只含 ETH：不含 BTC 既有勝出值、更不含 BTC 這輪本該被跳過的新值。
    overview = cache_get(
        json_cache_backend,
        cache_key(fetch_scheduler.TRUST_OVERVIEW_SOURCE, fetch_scheduler.TRUST_OVERVIEW_COIN),
    )
    overview_html = overview["docs"][0]["html"]
    assert "ETH" in overview_html
    assert "既有勝出值" not in overview_html
    # 只有 ETH 一張卡——BTC 被跳過的這輪候選值完全沒被納入（用卡片數量
    # 斷言而非比對信任分數字，因為測試用同一組假 confidence，兩幣本輪
    # 的分數本來就一樣，不能拿數字有沒有出現當判準）。
    assert overview_html.count("tf-overview-card") == 1


def test_latest_error_for_one_coin_excludes_it_from_history_and_overview_and_counts_failure(
    monkeypatch, json_cache_backend,
):
    """混合批次（BTC + ETH）：BTC 的 latest 寫入模擬 backend 端真的失敗
    （`result.ok=False`，不是單調跳過）。

    per-coin gating 斷言：BTC 不寫 history、不進總覽候選、且真的計入
    `failures`（整體回傳碼非 0，這才是真失敗，需要被監控看到）；ETH 不受
    影響，正常寫三表示。
    """
    day = "2026-07-01"
    ts = _utc_ts(f"{day}T12:00:00")
    monkeypatch.setattr(time, "time", lambda: ts)
    _patch_pipeline_run(monkeypatch, confidence=0.5)

    real_cache_set_if_newer = fetch_scheduler.cache_set_if_newer
    btc_latest_key = cache_key(TRUST_SNAPSHOT_SOURCE, "BTC")

    def flaky_cache_set_if_newer(backend, key, docs, fetched_at, ttl_seconds=None):
        if key == btc_latest_key:
            return CacheWriteResult(
                ok=False, used_fallback=False, backend="JsonCacheBackend",
                error="模擬 backend 寫入失敗（測試注入，非單調跳過）",
            )
        return real_cache_set_if_newer(
            backend, key, docs, fetched_at, ttl_seconds=ttl_seconds,
        )

    monkeypatch.setattr(fetch_scheduler, "cache_set_if_newer", flaky_cache_set_if_newer)

    # BTC 真失敗，本輪不是全乾淨，回傳碼必須是 1 才能被監控看到。
    assert fetch_scheduler.main(["--snapshot", "--coin", "BTC", "--coin", "ETH"]) == 1

    btc_latest = cache_get(json_cache_backend, btc_latest_key)
    assert btc_latest is None
    btc_history = cache_get(json_cache_backend, trust_snapshot_history_key("BTC", day))
    assert btc_history is None

    eth_latest = cache_get(json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, "ETH"))
    eth_history = cache_get(json_cache_backend, trust_snapshot_history_key("ETH", day))
    assert eth_latest is not None and eth_history is not None

    overview = cache_get(
        json_cache_backend,
        cache_key(fetch_scheduler.TRUST_OVERVIEW_SOURCE, fetch_scheduler.TRUST_OVERVIEW_COIN),
    )
    overview_html = overview["docs"][0]["html"]
    assert "ETH" in overview_html


# ---------------------------------------------------------------------------
# `get_trust_history()` 讀取 helper
# ---------------------------------------------------------------------------

def test_get_trust_history_skips_missing_days_without_faking_values(json_cache_backend):
    """缺漏的日期（沒跑過 `--snapshot`）直接跳過，不補假值（#24 鐵律）。"""
    from trustforge.ingestion.cache import cache_set

    cache_set(
        json_cache_backend, trust_snapshot_history_key("BTC", "2026-07-01"),
        [{"coin": "BTC", "trust_score": 0.5}], fetched_at=1.0,
        ttl_seconds=TRUST_SNAPSHOT_HISTORY_TTL_SECONDS,
    )
    history = get_trust_history("BTC", days=5, backend=json_cache_backend, end_date="2026-07-03")
    assert len(history) == 1
    assert history[0]["date"] == "2026-07-01"


def test_get_trust_history_returns_empty_when_nothing_written(json_cache_backend):
    assert get_trust_history("ETH", days=30, backend=json_cache_backend) == []
