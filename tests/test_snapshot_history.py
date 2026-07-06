"""task #26（docs/archive/plans/PLAN-multicore-worldfirst.md，新核心#1 持久化寫入基礎）：
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
    CacheBackend,
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
# codex HIGH（PR #59 review 第四／五輪）：per-coin all-or-nothing 收窄——
# 第五輪起改成先寫 history、再寫 latest/overview，gating 也改以 history
# 這次的 CAS 結果為準（history 才是不可復原、需要優先保住的那個）。
# ---------------------------------------------------------------------------

def test_history_skipped_for_one_coin_excludes_it_from_latest_and_overview(
    monkeypatch, json_cache_backend,
):
    """混合批次（BTC + ETH）：BTC 當天的 history 因為已有更新的並行排程
    結果而單調跳過（模擬另一輪較新的排程已經處理過 BTC 這一天），ETH 正常
    成功。

    per-coin gating 斷言（第五輪：以 history 結果為準）：BTC 這一幣的
    latest 完全不寫、總覽 blob 也不含 BTC 這輪（本該被跳過）的候選值；
    ETH 三表示都正常反映這輪新值。整體回傳碼仍是 0——這是健康的正常
    情況，不是失敗（見 `run_snapshot()` `skipped_coins` 的說明）。
    """
    day = "2026-07-01"
    stale_ts = _utc_ts(f"{day}T08:00:00")
    fresh_ts = _utc_ts(f"{day}T20:00:00")

    # 預先幫 BTC 當天的 history 種一筆已經比這輪要寫的還新的既有值——
    # 模擬「另一輪更新的排程已經處理過 BTC 這一天」。
    btc_history_key = trust_snapshot_history_key("BTC", day)
    pre_existing_btc_history = {
        "coin": "BTC", "trust_score": 0.99, "direction": "既有勝出值",
        "calibrated_confidence": 0.9, "decision_state": "normal",
        "generated_at": "2026-07-01T19:00:00Z",
    }
    json_cache_backend.set(btc_history_key, [pre_existing_btc_history], fetched_at=fresh_ts)

    monkeypatch.setattr(time, "time", lambda: stale_ts)
    _patch_pipeline_run(monkeypatch, confidence=0.3)
    assert fetch_scheduler.main(["--snapshot", "--coin", "BTC", "--coin", "ETH"]) == 0

    # BTC：history 維持既有（較新）值不被覆寫，latest 完全沒被建立
    # （這一幣本輪被跳過，不留一筆跟 history 矛盾的 latest）。
    btc_history = cache_get(json_cache_backend, btc_history_key)
    assert btc_history is not None
    assert btc_history["docs"][0] == pre_existing_btc_history
    btc_latest = cache_get(json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, "BTC"))
    assert btc_latest is None

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
    assert overview_html.count("tf-overview-card") == 1


def test_history_error_for_one_coin_excludes_it_from_latest_and_overview_and_counts_failure(
    monkeypatch, json_cache_backend,
):
    """混合批次（BTC + ETH）：BTC 的 history 寫入模擬 backend 端真的失敗
    （`result.ok=False`，不是單調跳過）。

    per-coin gating 斷言：BTC 不寫 latest、不進總覽候選、且真的計入
    `failures`（整體回傳碼非 0，這才是真失敗，需要被監控看到）——不能讓
    history 失敗但 latest/overview 還是幫這一幣寫了；ETH 不受影響，正常
    寫三表示。
    """
    day = "2026-07-01"
    ts = _utc_ts(f"{day}T12:00:00")
    monkeypatch.setattr(time, "time", lambda: ts)
    _patch_pipeline_run(monkeypatch, confidence=0.5)

    real_cache_set_if_newer = fetch_scheduler.cache_set_if_newer
    btc_history_key = trust_snapshot_history_key("BTC", day)

    def flaky_cache_set_if_newer(backend, key, docs, fetched_at, ttl_seconds=None, allow_json_fallback=None):
        if key == btc_history_key:
            return CacheWriteResult(
                ok=False, used_fallback=False, backend="JsonCacheBackend",
                error="模擬 backend 寫入失敗（測試注入，非單調跳過）",
            )
        return real_cache_set_if_newer(
            backend, key, docs, fetched_at, ttl_seconds=ttl_seconds,
            allow_json_fallback=allow_json_fallback,
        )

    monkeypatch.setattr(fetch_scheduler, "cache_set_if_newer", flaky_cache_set_if_newer)

    # BTC 真失敗，本輪不是全乾淨，回傳碼必須是 1 才能被監控看到。
    assert fetch_scheduler.main(["--snapshot", "--coin", "BTC", "--coin", "ETH"]) == 1

    btc_history = cache_get(json_cache_backend, btc_history_key)
    assert btc_history is None
    btc_latest = cache_get(json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, "BTC"))
    assert btc_latest is None

    eth_latest = cache_get(json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, "ETH"))
    eth_history = cache_get(json_cache_backend, trust_snapshot_history_key("ETH", day))
    assert eth_latest is not None and eth_history is not None

    overview = cache_get(
        json_cache_backend,
        cache_key(fetch_scheduler.TRUST_OVERVIEW_SOURCE, fetch_scheduler.TRUST_OVERVIEW_COIN),
    )
    overview_html = overview["docs"][0]["html"]
    assert "ETH" in overview_html


def test_crash_after_history_before_latest_keeps_history_durable(
    monkeypatch, json_cache_backend,
):
    """codex HIGH（PR #59 review 第五輪，#1 durability 最終閉合）核心情境：
    模擬程序剛好在「history 寫完」跟「latest 寫入」這兩步之間被砍斷
    （crash／OOM kill／部署中途被殺）——用 monkeypatch 讓 latest 這次
    `cache_set_if_newer()` 呼叫直接丟例外，且刻意不去接它（模擬程序真的
    終止，不是被 `run_snapshot()` 自己的 try/except 接住優雅降級）。

    斷言：即使 latest 完全沒機會寫、程序整個中斷退出，這一天這一幣的
    history 依然已經 durable 寫入正確值——這正是「重排成 history 先寫」
    要保住的東西（如果還是舊的「latest 先寫」順序，crash 會發生在 latest
    寫完、history 寫前，日後也還是自癒得了；但真正的風險場景反過來——
    history 沒寫到——用這個測試直接驗證新順序下 history 已經先保住）。
    """
    day = "2026-07-01"
    ts = _utc_ts(f"{day}T12:00:00")
    monkeypatch.setattr(time, "time", lambda: ts)
    _patch_pipeline_run(monkeypatch, confidence=0.42)

    real_cache_set_if_newer = fetch_scheduler.cache_set_if_newer
    btc_latest_key = cache_key(TRUST_SNAPSHOT_SOURCE, "BTC")

    class _SimulatedCrash(Exception):
        """模擬程序被砍斷（crash/OOM kill）——不是正常的錯誤處理路徑，
        刻意不被 `run_snapshot()` 內任何 try/except 接住。"""

    def crash_on_latest_write(backend, key, docs, fetched_at, ttl_seconds=None, allow_json_fallback=None):
        if key == btc_latest_key:
            raise _SimulatedCrash("模擬程序在 history 寫完、latest 寫入前被砍斷")
        return real_cache_set_if_newer(
            backend, key, docs, fetched_at, ttl_seconds=ttl_seconds,
            allow_json_fallback=allow_json_fallback,
        )

    monkeypatch.setattr(fetch_scheduler, "cache_set_if_newer", crash_on_latest_write)

    # 整個排程呼叫因為模擬 crash 而中斷，不是優雅降級——這才是真實
    # 「程序被砍斷」的行為，不應該被 `run_snapshot()` 吞掉繼續跑下去。
    with pytest.raises(_SimulatedCrash):
        fetch_scheduler.main(["--snapshot", "--coin", "BTC"])

    # 核心斷言：即使程序在 latest 寫入前整個中斷，這一天這一幣的 history
    # 已經 durable 保住，不會因為程序被砍斷而永久遺失。
    btc_history = cache_get(json_cache_backend, trust_snapshot_history_key("BTC", day))
    assert btc_history is not None
    assert btc_history["fetched_at"] == ts
    assert btc_history["docs"][0]["trust_score"] == pytest.approx(0.42, abs=1e-6)

    # latest 這一輪確實沒機會寫（程序中斷在它之前）——這是預期中「暫時
    # 落後」的自癒暫態，不是本測試要驗證的重點，但列出來讓行為顯式化。
    btc_latest = cache_get(json_cache_backend, btc_latest_key)
    assert btc_latest is None


def test_history_survives_across_utc_day_boundary_after_crash(
    monkeypatch, json_cache_backend,
):
    """codex HIGH（PR #59 review 第五輪）跨 UTC 日界情境：D 日最後一次
    排程在 history 寫完、latest 寫入前被砍斷（同上一個測試的 crash 模擬），
    接著 D+1 日重新正常跑一輪。

    斷言：D 日的 history 不會因為排程已經跨到 D+1 日而遺失或被覆寫
    （history key 本身就是按日分開的，D+1 的寫入完全不會碰到 D 的 key）；
    D+1 日的 history 也正確累積成新的一筆；先前因為 crash 而暫時落後的
    latest，在 D+1 這輪正常執行後自癒成 D+1 的最新值。
    """
    day_d = "2026-07-01"
    day_d1 = "2026-07-02"
    ts_d = _utc_ts(f"{day_d}T23:30:00")
    ts_d1 = _utc_ts(f"{day_d1}T00:05:00")

    real_cache_set_if_newer = fetch_scheduler.cache_set_if_newer
    btc_latest_key = cache_key(TRUST_SNAPSHOT_SOURCE, "BTC")

    class _SimulatedCrash(Exception):
        pass

    def crash_on_latest_write(backend, key, docs, fetched_at, ttl_seconds=None, allow_json_fallback=None):
        if key == btc_latest_key:
            raise _SimulatedCrash("模擬 D 日最後一次排程在跨日前被砍斷")
        return real_cache_set_if_newer(
            backend, key, docs, fetched_at, ttl_seconds=ttl_seconds,
            allow_json_fallback=allow_json_fallback,
        )

    # D 日：history 寫完、latest 寫入前程序中斷（crash）。
    monkeypatch.setattr(time, "time", lambda: ts_d)
    monkeypatch.setattr(fetch_scheduler, "cache_set_if_newer", crash_on_latest_write)
    _patch_pipeline_run(monkeypatch, confidence=0.71)
    with pytest.raises(_SimulatedCrash):
        fetch_scheduler.main(["--snapshot", "--coin", "BTC"])

    # crash 當下確認：D 日 history 已經 durable 保住，latest 還沒寫到。
    day_d_history = cache_get(json_cache_backend, trust_snapshot_history_key("BTC", day_d))
    assert day_d_history is not None
    assert day_d_history["docs"][0]["trust_score"] == pytest.approx(0.71, abs=1e-6)
    assert cache_get(json_cache_backend, btc_latest_key) is None

    # D+1 日：排程正常重跑（不再模擬 crash），latest 應該自癒成 D+1 新值。
    monkeypatch.setattr(fetch_scheduler, "cache_set_if_newer", real_cache_set_if_newer)
    monkeypatch.setattr(time, "time", lambda: ts_d1)
    _patch_pipeline_run(monkeypatch, confidence=0.88)
    assert fetch_scheduler.main(["--snapshot", "--coin", "BTC"]) == 0

    # 核心斷言：D 日的 history 完全沒有因為跨日、或因為 D 日曾經 crash
    # 過，而被遺失或被 D+1 的寫入動到——這正是重排寫入序要保住的東西。
    day_d_history_after = cache_get(json_cache_backend, trust_snapshot_history_key("BTC", day_d))
    assert day_d_history_after is not None
    assert day_d_history_after["docs"][0]["trust_score"] == pytest.approx(0.71, abs=1e-6)

    # D+1 日的 history 正確新增成另一筆，latest 也自癒成 D+1 的最新值。
    day_d1_history = cache_get(json_cache_backend, trust_snapshot_history_key("BTC", day_d1))
    assert day_d1_history is not None
    assert day_d1_history["docs"][0]["trust_score"] == pytest.approx(0.88, abs=1e-6)
    btc_latest_after = cache_get(json_cache_backend, btc_latest_key)
    assert btc_latest_after is not None
    assert btc_latest_after["fetched_at"] == ts_d1
    assert btc_latest_after["docs"][0]["trust_score"] == pytest.approx(0.88, abs=1e-6)


class _FlakyPrimaryBackend(CacheBackend):
    """模擬 primary backend（如 DynamoDB）對特定 key 暫時失敗、其餘 key
    正常運作（等同「兩次呼叫之間 primary 部分恢復」）——只用來驗證 codex
    第六輪 backend-affinity gating，不是真的 DynamoDB，純記憶體字典。"""

    def __init__(self, fail_once_for_keys: set[str]):
        self._store: dict[str, dict] = {}
        self._fail_once_for_keys = set(fail_once_for_keys)
        self._already_failed: set[str] = set()

    def get(self, key: str, *, consistent_read: bool = False):
        entry = self._store.get(key)
        return dict(entry) if entry is not None else None

    def set(self, key: str, docs, fetched_at: float, ttl_seconds=None) -> None:
        if key in self._fail_once_for_keys and key not in self._already_failed:
            self._already_failed.add(key)
            raise RuntimeError("模擬 primary backend 暫時失敗（測試注入，非真 DynamoDB）")
        self._store[key] = {"docs": docs, "fetched_at": fetched_at}


# ---------------------------------------------------------------------------
# codex HIGH（PR #59 review 第六輪 backend-affinity 一致性 → 第七輪
# 跨 backend 分裂 class 徹底閉合）：history/latest/overview 任一走 JSON
# fallback（primary 失敗）都不能讓其餘表示卻正常寫進 primary（跨 backend
# 分裂）。第七輪起 `run_snapshot()` 對這三個 `cache_set_if_newer()` 呼叫
# 都明確傳 `allow_json_fallback=False`——不管全域環境變數
# `TRUSTFORGE_CACHE_JSON_FALLBACK` 開或關，snapshot 這條路徑一律不嘗試
# fallback，primary 失敗就是直接 `ok=False`，不會有「fallback 成功但沒
# 進 primary」這種曖昧地帶。
# ---------------------------------------------------------------------------

def test_history_primary_failure_ignores_global_json_fallback_env_and_fails_closed(
    monkeypatch, json_cache_backend,
):
    """history 這次的 CAS 呼叫在 primary 端失敗；即使全域環境變數
    `TRUSTFORGE_CACHE_JSON_FALLBACK=1`（一般 cache 呼叫端會允許 fallback），
    `run_snapshot()` 對 history 明確傳的 `allow_json_fallback=False` 必須
    覆蓋掉環境變數——不嘗試 fallback，直接視為失敗，本輪這一幣的
    latest／總覽候選整個跳過、計入 failures，任何 backend（primary 或本地
    JSON fallback 檔）都不該出現這筆資料，不可能有跨 backend 分裂。

    這是第六輪同名測試的第七輪更新版：第六輪驗證的是「history 走 fallback
    時 gating 要擋 latest/overview」，第七輪把 fallback 本身直接關閉，
    所以第六輪那個「history 真的走 fallback 成功」的前提不再成立——
    這裡改成驗證「fallback 從頭到尾都沒被嘗試」這個更強的保證。
    """
    day = "2026-07-01"
    ts = _utc_ts(f"{day}T12:00:00")
    monkeypatch.setattr(time, "time", lambda: ts)
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_FALLBACK", "1")
    _patch_pipeline_run(monkeypatch, confidence=0.6)

    history_key = trust_snapshot_history_key("BTC", day)
    primary = _FlakyPrimaryBackend(fail_once_for_keys={history_key})

    # 直接呼叫 `run_snapshot()`（不透過 `main()` 的 env-var backend 選擇），
    # 才能注入這個非 `JsonCacheBackend` 的假 primary，觸發
    # `cache_set_if_newer()` 真正的 fallback 判斷邏輯（見
    # `src/trustforge/ingestion/cache.py` 的 `_json_fallback_enabled()`）
    # ——不是 monkeypatch `fetch_scheduler.cache_set_if_newer` 繞過真正的
    # 判斷。
    exit_code = fetch_scheduler.run_snapshot(["BTC"], primary, dry_run=False)

    # 真失敗（history 沒真正 durable 進 primary，也沒有走 fallback），
    # 回傳碼必須非 0。
    assert exit_code == 1

    # primary 端完全沒有這一輪的任何資料。
    assert history_key not in primary._store
    assert cache_key(TRUST_SNAPSHOT_SOURCE, "BTC") not in primary._store
    assert cache_key(fetch_scheduler.TRUST_OVERVIEW_SOURCE, fetch_scheduler.TRUST_OVERVIEW_COIN) not in primary._store

    # 本地 JSON fallback 這端：即使環境變數允許 fallback，`run_snapshot()`
    # 明確傳的 `allow_json_fallback=False` 必須讓 fallback 完全不被嘗試
    # ——history/latest/overview 三把 key 在本地 JSON 都不該出現任何資料。
    assert cache_get(json_cache_backend, history_key) is None
    assert cache_get(json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, "BTC")) is None
    assert cache_get(
        json_cache_backend,
        cache_key(fetch_scheduler.TRUST_OVERVIEW_SOURCE, fetch_scheduler.TRUST_OVERVIEW_COIN),
    ) is None


def test_latest_only_primary_failure_excludes_coin_and_does_not_split_backends(
    monkeypatch, json_cache_backend,
):
    """codex HIGH（PR #59 review 第七輪）：history 對 primary 寫入成功，
    緊接著同一幣的 latest 這次呼叫在 primary 端失敗（其餘 key 正常）。

    gating 斷言：即使全域環境變數 `TRUSTFORGE_CACHE_JSON_FALLBACK=1`，
    latest 也絕對不能走本地 JSON fallback 當成功繼續——必須直接視為
    失敗，這一幣排除在總覽候選外（若只有這一幣，overview_html 會是空字串
    根本不寫），計入 failures，回傳碼非 0。primary 端會有 history（真的
    成功了），但不該有 latest／overview；本地 JSON fallback 端則三者
    都不該有——不是「history 在 primary、latest 在 fallback」這種
    跨 backend 分裂，而是乾脆地整幣跳過。
    """
    day = "2026-07-01"
    ts = _utc_ts(f"{day}T12:00:00")
    monkeypatch.setattr(time, "time", lambda: ts)
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_FALLBACK", "1")
    _patch_pipeline_run(monkeypatch, confidence=0.6)

    latest_key = cache_key(TRUST_SNAPSHOT_SOURCE, "BTC")
    primary = _FlakyPrimaryBackend(fail_once_for_keys={latest_key})

    exit_code = fetch_scheduler.run_snapshot(["BTC"], primary, dry_run=False)

    assert exit_code == 1

    # history 對 primary 寫入沒有被 latest 的失敗影響（在它之前就已經
    # 成功了）——primary 端該有 history，不該有 latest／overview。
    history_key = trust_snapshot_history_key("BTC", day)
    assert history_key in primary._store
    assert latest_key not in primary._store
    overview_key = cache_key(fetch_scheduler.TRUST_OVERVIEW_SOURCE, fetch_scheduler.TRUST_OVERVIEW_COIN)
    assert overview_key not in primary._store

    # 本地 JSON fallback 端：即使環境變數允許 fallback，`allow_json_fallback
    # =False` 必須讓 latest 完全不嘗試 fallback——不該在本地看到 latest
    # 資料（也不該有 overview，畢竟只有這一幣、被排除後總覽候選是空的）。
    assert cache_get(json_cache_backend, latest_key) is None
    assert cache_get(json_cache_backend, overview_key) is None


def test_overview_only_primary_failure_counts_failure_and_does_not_split_backends(
    monkeypatch, json_cache_backend,
):
    """codex HIGH（PR #59 review 第七輪）：所有幣的 history／latest 都對
    primary 寫入成功，只有總覽 blob 這次呼叫在 primary 端失敗。

    gating 斷言：即使全域環境變數 `TRUSTFORGE_CACHE_JSON_FALLBACK=1`，
    總覽 blob 也不能走本地 JSON fallback 當成功繼續——直接視為失敗、
    計入 failures、回傳碼非 0。primary 端會有 BTC 的 history／latest
    （真的成功了），但不該有總覽 blob；本地 JSON fallback 端則完全不該
    出現總覽 blob 資料——不是「history/latest 在 primary、overview 在
    fallback」這種跨 backend 分裂，而是總覽這塊直接失敗、下一輪再重試。
    """
    day = "2026-07-01"
    ts = _utc_ts(f"{day}T12:00:00")
    monkeypatch.setattr(time, "time", lambda: ts)
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_FALLBACK", "1")
    _patch_pipeline_run(monkeypatch, confidence=0.6)

    overview_key = cache_key(fetch_scheduler.TRUST_OVERVIEW_SOURCE, fetch_scheduler.TRUST_OVERVIEW_COIN)
    primary = _FlakyPrimaryBackend(fail_once_for_keys={overview_key})

    exit_code = fetch_scheduler.run_snapshot(["BTC"], primary, dry_run=False)

    assert exit_code == 1

    history_key = trust_snapshot_history_key("BTC", day)
    latest_key = cache_key(TRUST_SNAPSHOT_SOURCE, "BTC")
    assert history_key in primary._store
    assert latest_key in primary._store
    assert overview_key not in primary._store

    # 本地 JSON fallback 端：總覽 blob 不該出現在任何 backend。
    assert cache_get(json_cache_backend, overview_key) is None


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


def test_get_trust_history_strict_true_pure_miss_still_returns_empty(json_cache_backend):
    """codex 複審 HIGH（根因修復）：`strict=True` 只影響「讀取真的失敗」
    分支——backend 正常運作、只是這幾天沒寫過快照（合法 miss），`strict`
    傳不傳結果必須一樣，一律回空序列，不能因為多了這個參數就連帶回歸。"""
    assert get_trust_history("ETH", days=30, backend=json_cache_backend, strict=True) == []


def test_get_trust_history_strict_true_raises_on_real_backend_outage(monkeypatch, tmp_path):
    """真 backend 降級（不是 replace `cache_get`/`get_trust_history` 這些
    helper 本身）：primary（模擬 DynamoDB 憑證/連線壞掉）+ fallback（本地
    `JsonCacheBackend`，模擬磁碟也讀不了）都真的拋例外 → `strict=True`
    必須讓例外傳出去（`CacheReadFailure`），不能悄悄回「這幾天都沒資料」
    的空序列，讓呼叫端（`/api/history`）誤判成正常回應。"""
    from trustforge.ingestion.cache import CacheReadFailure, DynamoDBCache
    from unittest.mock import MagicMock

    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(tmp_path / "fallback_cache.json"))

    broken = DynamoDBCache()
    monkeypatch.setattr(
        broken, "_get_table",
        MagicMock(side_effect=RuntimeError("no aws credentials / table not found")),
    )
    monkeypatch.setattr(
        JsonCacheBackend, "get",
        lambda self, key: (_ for _ in ()).throw(OSError("磁碟也壞了")),
    )

    with pytest.raises(CacheReadFailure):
        get_trust_history("BTC", days=3, backend=broken, strict=True)
