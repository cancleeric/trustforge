"""Issue #84：`scripts/measure_stance_cache_hit_rate.py` 單元測試。

⛔ 全程不打真 AWS：全部經由 monkeypatch 假 claims/candidate pairs，`measure()`
本身也只做唯讀的 `StanceCache.get()` 查詢，不呼叫任何 Bedrock client。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO / "scripts" / "measure_stance_cache_hit_rate.py"

_spec = importlib.util.spec_from_file_location(
    "measure_stance_cache_hit_rate", _SCRIPT_PATH
)
measure_mod = importlib.util.module_from_spec(_spec)
sys.modules["measure_stance_cache_hit_rate"] = measure_mod
_spec.loader.exec_module(measure_mod)

from trustforge.trust.stance_cache import STANCE_CACHE_VERSION, cache_key  # noqa: E402


def _write_cache(path: Path, entries: dict) -> None:
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def test_measure_all_hits_yields_100_percent(tmp_path, monkeypatch):
    cache_path = tmp_path / "stance_cache.json"
    _write_cache(cache_path, {
        cache_key("btc claim a", "btc claim b"): {
            "label": "entailment", "version": STANCE_CACHE_VERSION,
        },
    })

    def _fake_pairs_for_coin(coin):
        if coin == "BTC":
            return {cache_key("btc claim a", "btc claim b"): ("btc claim a", "btc claim b")}
        return {}

    monkeypatch.setattr(measure_mod._gen, "collect_claims_for_coin", lambda coin: coin)
    monkeypatch.setattr(
        measure_mod._gen, "enumerate_candidate_pairs_for_claims", _fake_pairs_for_coin
    )

    result = measure_mod.measure(cache_path, coins=["BTC", "ETH"])

    assert result["total"] == 1
    assert result["hits"] == 1
    assert result["hit_rate"] == 1.0
    assert result["per_coin"]["BTC"] == {"total": 1, "hits": 1}
    assert result["per_coin"]["ETH"] == {"total": 0, "hits": 0}


def test_measure_partial_hits_computes_correct_rate(tmp_path, monkeypatch):
    cache_path = tmp_path / "stance_cache.json"
    hit_key = cache_key("claim a", "claim b")
    _write_cache(cache_path, {hit_key: {"label": "neutral", "version": STANCE_CACHE_VERSION}})

    def _fake_pairs_for_coin(coin):
        return {
            hit_key: ("claim a", "claim b"),
            cache_key("claim c", "claim d"): ("claim c", "claim d"),  # miss
        }

    monkeypatch.setattr(measure_mod._gen, "collect_claims_for_coin", lambda coin: coin)
    monkeypatch.setattr(
        measure_mod._gen, "enumerate_candidate_pairs_for_claims", _fake_pairs_for_coin
    )

    result = measure_mod.measure(cache_path, coins=["BTC"])

    assert result["total"] == 2
    assert result["hits"] == 1
    assert result["hit_rate"] == 0.5


def test_measure_dedups_identical_pair_across_coins(tmp_path, monkeypatch):
    """同一對 (a,b) 若跨幣重複出現，總計只算一次（跟 `enumerate_candidate_pairs()`
    的合併去重規則一致）。"""
    cache_path = tmp_path / "stance_cache.json"
    shared_key = cache_key("shared a", "shared b")
    _write_cache(cache_path, {})  # 空快取：全部 miss

    def _fake_pairs_for_coin(coin):
        return {shared_key: ("shared a", "shared b")}

    monkeypatch.setattr(measure_mod._gen, "collect_claims_for_coin", lambda coin: coin)
    monkeypatch.setattr(
        measure_mod._gen, "enumerate_candidate_pairs_for_claims", _fake_pairs_for_coin
    )

    result = measure_mod.measure(cache_path, coins=["BTC", "ETH", "SOL"])

    assert result["total"] == 1  # 去重後只算一次，不是 3
    assert result["hits"] == 0
    assert result["per_coin"]["BTC"] == {"total": 1, "hits": 0}
    assert result["per_coin"]["ETH"] == {"total": 1, "hits": 0}
    assert result["per_coin"]["SOL"] == {"total": 1, "hits": 0}


def test_measure_no_candidate_pairs_is_100_percent(tmp_path, monkeypatch):
    """沒有任何候選對（如離線樣本資料剛好完全不重疊）：hit_rate 定義為 1.0，
    不該被誤判成「未達標」（天然零外呼需求）。"""
    cache_path = tmp_path / "stance_cache.json"
    _write_cache(cache_path, {})

    monkeypatch.setattr(measure_mod._gen, "collect_claims_for_coin", lambda coin: coin)
    monkeypatch.setattr(measure_mod._gen, "enumerate_candidate_pairs_for_claims", lambda claims: {})

    result = measure_mod.measure(cache_path, coins=["BTC"])

    assert result["total"] == 0
    assert result["hit_rate"] == 1.0


def test_main_exit_zero_when_hit_rate_meets_threshold(tmp_path, monkeypatch, capsys):
    cache_path = tmp_path / "stance_cache.json"
    hit_key = cache_key("claim a", "claim b")
    _write_cache(cache_path, {hit_key: {"label": "neutral", "version": STANCE_CACHE_VERSION}})

    monkeypatch.setattr(measure_mod._gen, "collect_claims_for_coin", lambda coin: coin)
    monkeypatch.setattr(
        measure_mod._gen, "enumerate_candidate_pairs_for_claims",
        lambda claims: {hit_key: ("claim a", "claim b")},
    )

    rc = measure_mod.main(["--cache", str(cache_path), "--coins", "BTC", "--threshold", "0.8"])

    assert rc == 0
    captured = capsys.readouterr()
    assert "達標" in captured.out


def test_main_exit_nonzero_when_hit_rate_below_threshold(tmp_path, monkeypatch, capsys):
    cache_path = tmp_path / "stance_cache.json"
    _write_cache(cache_path, {})  # 空快取：全部 miss

    monkeypatch.setattr(measure_mod._gen, "collect_claims_for_coin", lambda coin: coin)
    monkeypatch.setattr(
        measure_mod._gen, "enumerate_candidate_pairs_for_claims",
        lambda claims: {cache_key("a", "b"): ("a", "b")},
    )

    rc = measure_mod.main(["--cache", str(cache_path), "--coins", "BTC", "--threshold", "0.8"])

    assert rc != 0
    captured = capsys.readouterr()
    assert "未達標" in captured.err


def test_measure_real_demo_cache_hits_at_least_80_percent():
    """回歸：專案實際的 `demo/sample_data/stance_cache.json` 對 5 幣 demo 動線
    （真實離線候選對枚舉，不 monkeypatch）必須 ≥80%（issue #84 驗收標準本身）。
    """
    from trustforge.trust.stance_cache import DEFAULT_CACHE_PATH

    result = measure_mod.measure(DEFAULT_CACHE_PATH)

    assert result["hit_rate"] >= 0.8, (
        f"5 幣 demo 動線 stance cache hit rate 僅 {result['hit_rate']:.1%}"
        f"（{result['hits']}/{result['total']}），未達 issue #84 的 80% 門檻"
    )
