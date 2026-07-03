"""task #26（docs/PLAN-multicore-worldfirst.md，新核心#1 持久化寫入基礎）：
`--snapshot` 按日累積歷史快照 + `get_trust_history()` 讀取 helper 測試。

⛔ credit-safe 鐵律：全程 `pipeline.run()` 被 monkeypatch 成樁，不觸發任何
連接器真抓取、不打真 Bedrock、不打真 DynamoDB——比照 `test_home_overview.py`
既有 `--snapshot` 測試慣例，`CACHE_BACKEND=json` 隔離到 tmp_path。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from scripts import fetch_scheduler
from trustforge.ingestion.cache import (
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
