"""Issue #12：`scripts/gen_stance_cache.py` 核心函式單元測試。

⛔ 全程不打真 AWS：`--dry-run` 路徑完全不建立 client；merge/classify 相關測試
一律用回固定 label 的假 client，不匯入/呼叫 boto3。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO / "scripts" / "gen_stance_cache.py"

# scripts/ 沒有 __init__.py，用 importlib 依路徑載入，避免污染 sys.path 套件命名空間。
_spec = importlib.util.spec_from_file_location("gen_stance_cache", _SCRIPT_PATH)
gen_stance_cache = importlib.util.module_from_spec(_spec)
sys.modules["gen_stance_cache"] = gen_stance_cache
_spec.loader.exec_module(gen_stance_cache)

from trustforge.ingestion.base import Document  # noqa: E402
from trustforge.trust.scoring import Claim  # noqa: E402
from trustforge.trust.stance_cache import STANCE_CACHE_VERSION, cache_key  # noqa: E402


def _claim(cid: str, source: str, text: str, direction: str = "neutral") -> Claim:
    doc = Document(id=f"{cid}-doc", kind="news", source=source, text=text)
    return Claim(id=cid, text=text, doc=doc, direction=direction)


class _FakeClient:
    """固定回傳同一個 label 的假 client：不匯入 boto3，不打真 AWS。

    只實作 `classify_stance_strict`（gen 腳本現在只呼叫這個嚴格版，不呼叫
    降級版 `classify_stance`——見 HIGH 修正：呼叫失敗必須 raise，不可吞 neutral）。
    """

    def __init__(self, label: str = "entailment"):
        self.label = label
        self.calls: list[tuple[str, str]] = []

    def classify_stance_strict(self, a: str, b: str) -> str:
        self.calls.append((a, b))
        return self.label


class _FlakyFakeClient:
    """指定 pair 呼叫失敗（raise）的假 client，其餘正常回傳固定 label。

    用來驗證 HIGH 修正：任一對失敗 → `classify_pairs`/`main` 必須中止、
    不吞成 neutral、不寫檔。

    #84 review 必修 1（harper/codex HIGH）追加：比照 `_CostAccruingFakeClient`，
    每一次「成功」的真呼叫（失敗那次不算，跟真 `BedrockClient` 一致——沒判斷出
    結果就沒有對應的成功呼叫成本事件）都在 `cost_events` 累積一筆固定成本，
    讓測試能驗證「失敗中止前已經真的花掉的前 k 筆成本，`main()` 仍必須記回
    帳本」。
    """

    def __init__(self, fail_on: str, label: str = "entailment", cost_per_call: float = 0.01):
        self.fail_on = fail_on
        self.label = label
        self.cost_per_call = cost_per_call
        self.calls: list[tuple[str, str]] = []
        self.cost_events: list[dict] = []

    def classify_stance_strict(self, a: str, b: str) -> str:
        self.calls.append((a, b))
        if a == self.fail_on or b == self.fail_on:
            raise TimeoutError(f"simulated Bedrock failure on {self.fail_on!r}")
        self.cost_events.append({
            "model": "au.anthropic.claude-haiku-4-5-20251001-v1:0",
            "tokens_in": 10,
            "tokens_out": 5,
            "cost_usd": self.cost_per_call,
        })
        return self.label


class _CostAccruingFakeClient:
    """比照真 `BedrockClient`，每次真呼叫後在 `cost_events` 累積一筆固定成本，
    供 issue #84 budget guard 相關測試驗證 `_make_budget_check()` /
    `_record_batch_cost_to_ledger()` 的行為，仍不匯入/呼叫 boto3。
    """

    def __init__(self, label: str = "entailment", cost_per_call: float = 0.01):
        self.label = label
        self.cost_per_call = cost_per_call
        self.calls: list[tuple[str, str]] = []
        self.cost_events: list[dict] = []

    def classify_stance_strict(self, a: str, b: str) -> str:
        self.calls.append((a, b))
        self.cost_events.append({
            "model": "au.anthropic.claude-haiku-4-5-20251001-v1:0",
            "tokens_in": 10,
            "tokens_out": 5,
            "cost_usd": self.cost_per_call,
        })
        return self.label


# ── 候選對枚舉：overlap 前置閘 + 去重 ────────────────────────────────────────


def test_enumerate_candidate_pairs_dedups_reversed_pair():
    """高重疊、不同來源的一對 claims 應被列為候選對；(a,b) 與 (b,a) 只算一筆
    （用 cache_key 去重）。低重疊的第三條 claim 不應入選。
    """
    c1 = _claim("c1", "source-a", "alpha beta gamma delta")
    c2 = _claim("c2", "source-b", "alpha beta gamma epsilon")
    c3 = _claim("c3", "source-c", "totally unrelated content here")

    found = gen_stance_cache.enumerate_candidate_pairs_for_claims([c1, c2, c3])

    assert len(found) == 1
    (key, (a, b)) = next(iter(found.items()))
    assert key == cache_key(c1.text, c2.text)
    assert {a, b} == {c1.text, c2.text}
    # c3 與任何人都低重疊，不該出現在任何候選對中
    for a, b in found.values():
        assert c3.text not in (a, b)


def test_enumerate_candidate_pairs_excludes_same_source():
    """同來源的 claims 即使文字重疊也不該被視為候選對（`_corroboration_detail`
    排除自家來源）。"""
    c1 = _claim("c1", "same-source", "alpha beta gamma delta")
    c2 = _claim("c2", "same-source", "alpha beta gamma epsilon")

    found = gen_stance_cache.enumerate_candidate_pairs_for_claims([c1, c2])

    assert found == {}


def test_enumerate_candidate_pairs_excludes_direction_incompatible():
    """方向閘：兩者皆有明確方向且相反時，即使高重疊也不應入選。"""
    c1 = _claim("c1", "source-a", "alpha beta gamma delta", direction="bullish")
    c2 = _claim("c2", "source-b", "alpha beta gamma epsilon", direction="bearish")

    found = gen_stance_cache.enumerate_candidate_pairs_for_claims([c1, c2])

    assert found == {}


# ── merge：既有 key 不丟，新 key 覆蓋/新增 ──────────────────────────────────


def test_merge_cache_preserves_existing_and_applies_new():
    existing = {
        "keep-me": {"label": "contradiction", "version": STANCE_CACHE_VERSION},
        "overwrite-me": {"label": "neutral", "version": "v0"},
    }
    new_entries = {
        "overwrite-me": {"label": "entailment", "version": STANCE_CACHE_VERSION},
        "brand-new": {"label": "neutral", "version": STANCE_CACHE_VERSION},
    }

    merged = gen_stance_cache.merge_cache(existing, new_entries)

    assert merged["keep-me"] == {"label": "contradiction", "version": STANCE_CACHE_VERSION}
    assert merged["overwrite-me"] == {"label": "entailment", "version": STANCE_CACHE_VERSION}
    assert merged["brand-new"] == {"label": "neutral", "version": STANCE_CACHE_VERSION}
    assert len(merged) == 3


def test_load_existing_cache_missing_file_returns_empty(tmp_path):
    assert gen_stance_cache.load_existing_cache(tmp_path / "does-not-exist.json") == {}


def test_load_existing_cache_normal_file_merges_fine(tmp_path):
    """既有正常流程回歸：檔案存在且是合法 dict → 照常回傳內容（不 raise）。"""
    p = tmp_path / "stance_cache.json"
    p.write_text(
        json.dumps({"k": {"label": "neutral", "version": STANCE_CACHE_VERSION}}),
        encoding="utf-8",
    )
    assert gen_stance_cache.load_existing_cache(p) == {
        "k": {"label": "neutral", "version": STANCE_CACHE_VERSION}
    }


# ── load_existing_cache fail-closed（第二個 HIGH：讀失敗絕不能當空 dict）───────


def test_load_existing_cache_malformed_json_raises(tmp_path):
    p = tmp_path / "stance_cache.json"
    p.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(Exception):
        gen_stance_cache.load_existing_cache(p)


def test_load_existing_cache_non_dict_top_level_raises(tmp_path):
    p = tmp_path / "stance_cache.json"
    p.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")

    with pytest.raises(Exception):
        gen_stance_cache.load_existing_cache(p)


def test_load_existing_cache_os_error_raises(tmp_path, monkeypatch):
    """模擬讀取權限錯誤：`Path.read_text` 拋 `OSError` → 必須 raise，不可回空 dict。"""
    p = tmp_path / "stance_cache.json"
    p.write_text(json.dumps({"k": {"label": "neutral", "version": STANCE_CACHE_VERSION}}))

    def _boom(self, encoding=None):
        raise OSError("simulated permission denied")

    monkeypatch.setattr(Path, "read_text", _boom)

    with pytest.raises(Exception):
        gen_stance_cache.load_existing_cache(p)


# ── classify_pairs：假 client，不打真 AWS ───────────────────────────────────


def test_classify_pairs_uses_client_and_tags_version():
    fake_client = _FakeClient(label="contradiction")
    pairs = {"k1": ("claim text a", "claim text b")}

    entries = gen_stance_cache.classify_pairs(fake_client, pairs)

    assert entries == {"k1": {"label": "contradiction", "version": STANCE_CACHE_VERSION}}
    assert fake_client.calls == [("claim text a", "claim text b")]


def test_classify_pairs_raises_on_first_failure_no_partial_entries():
    """HIGH 修正核心：某一對失敗（strict raise）→ `classify_pairs` 必須把例外
    往上丟，不可吞成 neutral、也不可回傳部分結果讓呼叫端誤以為全部成功。
    """
    fake_client = _FlakyFakeClient(fail_on="bad-claim")
    pairs = {
        "k1": ("good-claim-a", "good-claim-b"),
        "k2": ("good-claim-a", "bad-claim"),
    }

    with pytest.raises(TimeoutError):
        gen_stance_cache.classify_pairs(fake_client, pairs)


# ── 原子寫入 ─────────────────────────────────────────────────────────────────


def test_atomic_write_json_writes_final_content_no_leftover_tmp(tmp_path):
    out_path = tmp_path / "stance_cache.json"
    data = {"k": {"label": "entailment", "version": STANCE_CACHE_VERSION}}

    gen_stance_cache.atomic_write_json(out_path, data)

    assert json.loads(out_path.read_text(encoding="utf-8")) == data
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


# ── --dry-run：不呼叫 client、不寫檔 ─────────────────────────────────────────


def test_main_dry_run_does_not_write_file(tmp_path, capsys):
    out_path = tmp_path / "stance_cache.json"

    rc = gen_stance_cache.main(["--dry-run", "--out", str(out_path)])

    assert rc == 0
    assert not out_path.exists()
    captured = capsys.readouterr()
    assert "候選對數" in captured.out
    assert "--dry-run" in captured.out


# ── HIGH 修正整合測試：main() 全成功才寫 / 一對失敗則 cache 完全不變 ──────────


_FAKE_PAIRS = {
    "k1": ("claim alpha text", "claim beta text"),
    "k2": ("claim gamma text", "claim delta text"),
}


def test_main_failure_mid_batch_leaves_existing_cache_untouched(tmp_path, monkeypatch, capsys):
    """某一對 strict 呼叫失敗 → `main()` 非零退出、完全不寫檔，既有快取檔
    （含內容）必須逐位元組保持原樣不變。
    """
    out_path = tmp_path / "stance_cache.json"
    original_content = json.dumps(
        {"keep-me": {"label": "contradiction", "version": STANCE_CACHE_VERSION}},
        ensure_ascii=False,
    )
    out_path.write_text(original_content, encoding="utf-8")

    monkeypatch.setattr(gen_stance_cache, "enumerate_candidate_pairs", lambda: _FAKE_PAIRS)
    fake_client = _FlakyFakeClient(fail_on="claim delta text")
    monkeypatch.setattr(gen_stance_cache, "_build_live_client", lambda: fake_client)

    rc = gen_stance_cache.main(["--out", str(out_path)])

    assert rc != 0
    # 既有快取檔完全不變（byte-identical），不含任何新 key、不含假 neutral。
    assert out_path.read_text(encoding="utf-8") == original_content
    # 沒有殘留的 temp 檔
    assert list(tmp_path.glob(".*.tmp")) == []
    captured = capsys.readouterr()
    assert "中止" in captured.err or "中止" in captured.out


def test_main_general_failure_still_records_partial_cost_to_ledger(tmp_path, monkeypatch):
    """harper/codex HIGH（#84 review 必修 1）：一般失敗（非 budget 中止，
    `except Exception` 分支）中止前，前 k 對已經真的花掉的成本
    （`client.cost_events`）也必須先記回帳本——不能因為隨後 `return 1`、
    不寫 cache 檔，就連帶讓這筆真實花費從帳本消失（否則下次查
    `daily_cost_usd()` 會少算這筆，等於繞過 #9 cap）。改成 `finally` 涵蓋
    全成功／`BudgetExhausted`／一般失敗所有出場路徑才記帳。
    """
    out_path = tmp_path / "stance_cache.json"
    monkeypatch.setattr(gen_stance_cache, "enumerate_candidate_pairs", lambda: _FAKE_PAIRS)
    # k1 = ("claim alpha text", "claim beta text") 先成功，k2 因含
    # "claim delta text" 而失敗——k1 這一筆真實花費必須被記回帳本。
    fake_client = _FlakyFakeClient(fail_on="claim delta text", cost_per_call=0.01)
    monkeypatch.setattr(gen_stance_cache, "_build_live_client", lambda: fake_client)
    captured_ledger: list[dict] = []
    monkeypatch.setattr(
        gen_stance_cache, "append_run", lambda record: captured_ledger.append(record) or True
    )

    rc = gen_stance_cache.main(["--out", str(out_path)])

    assert rc == 1
    assert not out_path.exists()  # 一般失敗仍然完全不寫 cache 檔，既有語意不變
    # 但 k1 已經真的花掉的成本必須被記到帳本，不能因失敗中止而消失
    assert len(captured_ledger) == 1
    assert captured_ledger[0]["total_cost_usd"] == 0.01
    assert len(captured_ledger[0]["calls"]) == 1


def test_main_success_writes_merged_result_preserving_existing_keys(tmp_path, monkeypatch):
    """全部候選對都成功分類 → 正常 merge 寫入，既有 key 保留，新 key 新增。"""
    out_path = tmp_path / "stance_cache.json"
    out_path.write_text(
        json.dumps({"keep-me": {"label": "contradiction", "version": STANCE_CACHE_VERSION}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(gen_stance_cache, "enumerate_candidate_pairs", lambda: _FAKE_PAIRS)
    fake_client = _FakeClient(label="entailment")
    monkeypatch.setattr(gen_stance_cache, "_build_live_client", lambda: fake_client)

    rc = gen_stance_cache.main(["--out", str(out_path)])

    assert rc == 0
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["keep-me"] == {"label": "contradiction", "version": STANCE_CACHE_VERSION}
    assert written["k1"] == {"label": "entailment", "version": STANCE_CACHE_VERSION}
    assert written["k2"] == {"label": "entailment", "version": STANCE_CACHE_VERSION}
    assert len(fake_client.calls) == 2


# ── 第二個 HIGH 整合測試：既有快取「讀不到」時 main() 必須 fail-closed 中止，
#    不可把讀失敗當空 dict 而覆寫掉舊資料。三種壞檔情境皆須「原檔不變」。──────


def test_main_malformed_json_cache_aborts_without_touching_file(tmp_path, monkeypatch, capsys):
    out_path = tmp_path / "stance_cache.json"
    original_content = "{not valid json"
    out_path.write_text(original_content, encoding="utf-8")

    monkeypatch.setattr(gen_stance_cache, "enumerate_candidate_pairs", lambda: _FAKE_PAIRS)
    fake_client = _FakeClient(label="entailment")
    monkeypatch.setattr(gen_stance_cache, "_build_live_client", lambda: fake_client)

    rc = gen_stance_cache.main(["--out", str(out_path)])

    assert rc != 0
    assert out_path.read_text(encoding="utf-8") == original_content
    # 壞檔在真呼叫之前就該被擋下，不該浪費任何一次真 Bedrock 呼叫
    assert fake_client.calls == []
    captured = capsys.readouterr()
    assert "中止" in captured.err or "中止" in captured.out


def test_main_non_dict_top_level_cache_aborts_without_touching_file(tmp_path, monkeypatch):
    out_path = tmp_path / "stance_cache.json"
    original_content = json.dumps(["not", "a", "dict"])
    out_path.write_text(original_content, encoding="utf-8")

    monkeypatch.setattr(gen_stance_cache, "enumerate_candidate_pairs", lambda: _FAKE_PAIRS)
    fake_client = _FakeClient(label="entailment")
    monkeypatch.setattr(gen_stance_cache, "_build_live_client", lambda: fake_client)

    rc = gen_stance_cache.main(["--out", str(out_path)])

    assert rc != 0
    assert out_path.read_text(encoding="utf-8") == original_content
    assert fake_client.calls == []


def test_main_cache_os_error_aborts_without_touching_file(tmp_path, monkeypatch):
    out_path = tmp_path / "stance_cache.json"
    original_content = json.dumps({"keep-me": {"label": "neutral", "version": STANCE_CACHE_VERSION}})
    out_path.write_text(original_content, encoding="utf-8")

    monkeypatch.setattr(gen_stance_cache, "enumerate_candidate_pairs", lambda: _FAKE_PAIRS)
    fake_client = _FakeClient(label="entailment")
    monkeypatch.setattr(gen_stance_cache, "_build_live_client", lambda: fake_client)

    def _boom(self, encoding=None):
        raise OSError("simulated permission denied")

    monkeypatch.setattr(Path, "read_text", _boom)

    rc = gen_stance_cache.main(["--out", str(out_path)])

    assert rc != 0
    # 繞過被 monkeypatch 的 read_text，用原生 open 驗證檔案內容真的沒被動過
    with open(out_path, encoding="utf-8") as f:
        assert f.read() == original_content
    assert fake_client.calls == []


# ── issue #84：select_missing_pairs 冪等過濾 ─────────────────────────────────


def test_select_missing_pairs_skips_valid_cached_entries():
    existing = {
        "k1": {"label": "entailment", "version": STANCE_CACHE_VERSION},
    }
    pairs = {
        "k1": ("claim a", "claim b"),
        "k2": ("claim c", "claim d"),
    }

    missing = gen_stance_cache.select_missing_pairs(existing, pairs)

    assert missing == {"k2": ("claim c", "claim d")}


def test_select_missing_pairs_treats_version_mismatch_as_missing():
    existing = {"k1": {"label": "entailment", "version": "v0"}}
    pairs = {"k1": ("claim a", "claim b")}

    missing = gen_stance_cache.select_missing_pairs(existing, pairs)

    assert missing == {"k1": ("claim a", "claim b")}


def test_select_missing_pairs_treats_malformed_entry_as_missing():
    existing = {"k1": "not-a-dict"}
    pairs = {"k1": ("claim a", "claim b")}

    missing = gen_stance_cache.select_missing_pairs(existing, pairs)

    assert missing == {"k1": ("claim a", "claim b")}


def test_select_missing_pairs_treats_missing_label_as_missing():
    """codex/harper HIGH（#84 review 必修 4）：version 相符但缺 `label` 的壞
    entry，不可被當成「已快取命中」永久跳過——`StanceCache.get()` 對同一筆
    entry 會判定為 miss，兩套判準不能 drift。"""
    existing = {"k1": {"version": STANCE_CACHE_VERSION}}  # 缺 label
    pairs = {"k1": ("claim a", "claim b")}

    missing = gen_stance_cache.select_missing_pairs(existing, pairs)

    assert missing == {"k1": ("claim a", "claim b")}


def test_select_missing_pairs_treats_invalid_label_as_missing():
    """codex/harper HIGH（#84 review 必修 4）：version 相符但 `label` 不是
    合法值（如手動編輯打錯字/舊格式殘留）的壞 entry，同樣必須視為 missing，
    跟 `StanceCache.get()` 的驗證共用同一份 `_VALID_LABELS`。"""
    existing = {"k1": {"label": "maybe", "version": STANCE_CACHE_VERSION}}
    pairs = {"k1": ("claim a", "claim b")}

    missing = gen_stance_cache.select_missing_pairs(existing, pairs)

    assert missing == {"k1": ("claim a", "claim b")}


def test_select_missing_pairs_empty_when_all_cached():
    existing = {
        "k1": {"label": "entailment", "version": STANCE_CACHE_VERSION},
        "k2": {"label": "neutral", "version": STANCE_CACHE_VERSION},
    }
    pairs = {"k1": ("a", "b"), "k2": ("c", "d")}

    assert gen_stance_cache.select_missing_pairs(existing, pairs) == {}


# ── issue #84：classify_pairs 的 budget_check 提前中止（保留 partial entries）──


def test_classify_pairs_budget_check_none_preserves_old_behavior():
    """`budget_check` 預設 `None` 時完全不檢查，逐字沿用既有行為（回歸）。"""
    fake_client = _FakeClient(label="entailment")
    pairs = {"k1": ("a", "b")}

    entries = gen_stance_cache.classify_pairs(fake_client, pairs)

    assert entries == {"k1": {"label": "entailment", "version": STANCE_CACHE_VERSION}}


def test_classify_pairs_stops_early_on_budget_exhausted_keeps_partial_entries():
    fake_client = _FakeClient(label="entailment")
    pairs = {
        "k1": ("claim a", "claim b"),
        "k2": ("claim c", "claim d"),
    }
    calls = {"n": 0}

    def _budget_check() -> bool:
        calls["n"] += 1
        return calls["n"] <= 1  # 只放行第一次呼叫，第二次視為額度用盡

    with pytest.raises(gen_stance_cache.BudgetExhausted) as excinfo:
        gen_stance_cache.classify_pairs(fake_client, pairs, budget_check=_budget_check)

    # 第一對已成功呼叫並保留在 exception 攜帶的 partial entries 中
    assert fake_client.calls == [("claim a", "claim b")]
    assert excinfo.value.entries == {
        "k1": {"label": "entailment", "version": STANCE_CACHE_VERSION}
    }


def test_classify_pairs_budget_check_blocks_before_any_call():
    fake_client = _FakeClient(label="entailment")
    pairs = {"k1": ("a", "b")}

    with pytest.raises(gen_stance_cache.BudgetExhausted) as excinfo:
        gen_stance_cache.classify_pairs(fake_client, pairs, budget_check=lambda: False)

    assert fake_client.calls == []
    assert excinfo.value.entries == {}


# ── issue #84：_make_budget_check（fail-closed 檢查 stance model 是否已計價）──
#    #84 review 必修 2（codex/harper HIGH）後，cap 是否還有餘裕改交給
#    classify_pairs() 內的 try_reserve_request_budget() 原子預留把關（見下方
#    專屬測試區），這裡只剩「model 是否已計價」這一件事。


def test_make_budget_check_true_when_model_priced(monkeypatch):
    monkeypatch.setattr(gen_stance_cache, "stance_model_priced", lambda: True)
    fake_client = _FakeClient()

    check = gen_stance_cache._make_budget_check(fake_client)

    assert check() is True


def test_make_budget_check_false_when_model_unpriced(monkeypatch):
    monkeypatch.setattr(gen_stance_cache, "stance_model_priced", lambda: False)
    fake_client = _FakeClient()

    check = gen_stance_cache._make_budget_check(fake_client)

    assert check() is False


# ── issue #84 review 必修 2（codex/harper HIGH）：classify_pairs 對每一次
# 真呼叫做 budget_guard 原子預留／釋放，取代舊版唯讀 peek，杜絕 TOCTOU ────────


def test_classify_pairs_reserves_and_releases_budget_around_each_real_call(monkeypatch):
    """`budget_check` 非 None 時，每次真呼叫前後都應該經過
    `try_reserve_request_budget()`/`release_request_budget()`，且順序正確：
    先 reserve 才呼叫真 client、呼叫完才 release。"""
    events: list[str] = []

    def _fake_reserve(*args, **kwargs):
        events.append("reserve")
        return 0.05

    def _fake_release(amount):
        events.append(f"release:{amount}")

    monkeypatch.setattr(gen_stance_cache, "try_reserve_request_budget", _fake_reserve)
    monkeypatch.setattr(gen_stance_cache, "release_request_budget", _fake_release)

    fake_client = _FakeClient(label="entailment")
    pairs = {"k1": ("a", "b"), "k2": ("c", "d")}

    entries = gen_stance_cache.classify_pairs(fake_client, pairs, budget_check=lambda: True)

    assert entries == {
        "k1": {"label": "entailment", "version": STANCE_CACHE_VERSION},
        "k2": {"label": "entailment", "version": STANCE_CACHE_VERSION},
    }
    assert events == ["reserve", "release:0.05", "reserve", "release:0.05"]


def test_classify_pairs_stops_before_call_when_reservation_denied(monkeypatch):
    """codex HIGH（#84 review 必修 2）：`try_reserve_request_budget()` 回
    `None`（額度不足/帳本讀取失敗）時，`classify_stance_strict` 完全不會被
    呼叫——呼叫前就中止，不是呼叫後才發現超支。"""
    monkeypatch.setattr(gen_stance_cache, "try_reserve_request_budget", lambda *a, **k: None)
    release_calls: list = []
    monkeypatch.setattr(
        gen_stance_cache, "release_request_budget", lambda amount: release_calls.append(amount)
    )

    fake_client = _FakeClient(label="entailment")
    pairs = {"k1": ("a", "b")}

    with pytest.raises(gen_stance_cache.BudgetExhausted) as excinfo:
        gen_stance_cache.classify_pairs(fake_client, pairs, budget_check=lambda: True)

    assert fake_client.calls == []  # 真呼叫從未啟動
    assert excinfo.value.entries == {}
    assert release_calls == []  # 沒拿到預留，也就沒有東西可釋放


def test_classify_pairs_releases_budget_even_when_call_raises(monkeypatch):
    """真呼叫失敗（raise）時，已經拿到的預留仍必須在 `finally` 被釋放，不能
    卡住不還（否則額度會被永久佔用，往後的呼叫全部誤判為不足）。"""
    monkeypatch.setattr(gen_stance_cache, "try_reserve_request_budget", lambda *a, **k: 0.05)
    release_calls: list = []
    monkeypatch.setattr(
        gen_stance_cache, "release_request_budget", lambda amount: release_calls.append(amount)
    )

    fake_client = _FlakyFakeClient(fail_on="b")
    pairs = {"k1": ("a", "b")}

    with pytest.raises(TimeoutError):
        gen_stance_cache.classify_pairs(fake_client, pairs, budget_check=lambda: True)

    assert release_calls == [0.05]


def test_classify_pairs_near_cap_real_budget_guard_blocks_call(monkeypatch):
    """codex HIGH（#84 review 必修 2）near-cap 整合測試：不 mock
    `try_reserve_request_budget()` 本身，改用真的 `budget_guard`（只調環境
    變數把每日 cap 壓到幾乎耗盡、並用真 `append_run()` 寫一筆「今天」已花費
    的歷史紀錄），驗證額度不足時 `classify_stance_strict` 真的從未被
    呼叫——舊版唯讀 peek 在額度逼近上限時仍可能誤放行，新版原子預留機制
    必須在呼叫前就擋下。"""
    import time as _time

    from trustforge.schema import iso_utc

    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "0.01")
    # 用真的 append_run() 寫一筆「今天」已花費 $0.009 的歷史紀錄
    # （TRUSTFORGE_COST_LEDGER_PATH 已由 conftest 的 _isolate_cost_ledger 隔離
    # 到 tmp_path，不會污染真實帳本）——只剩 $0.001 headroom，遠低於
    # try_reserve_request_budget() 的單呼保守上界 request_max_cost_usd()
    # （預設 $0.05），reserve 必然失敗。
    persisted = gen_stance_cache.append_run({
        "ts": iso_utc(_time.time()),
        "question_type": "stance",
        "coin": "BTC",
        "offline": False,
        "calls": [],
        "total_cost_usd": 0.009,
    })
    assert persisted  # 前置條件：這筆歷史花費真的寫進了（隔離後的）帳本

    fake_client = _FakeClient(label="entailment")
    pairs = {"k1": ("a", "b")}
    budget_check = gen_stance_cache._make_budget_check(fake_client)

    with pytest.raises(gen_stance_cache.BudgetExhausted) as excinfo:
        gen_stance_cache.classify_pairs(fake_client, pairs, budget_check=budget_check)

    assert fake_client.calls == []  # 真呼叫從未啟動
    assert excinfo.value.entries == {}


# ── issue #84：_record_batch_cost_to_ledger（把批次花費記回帳本）──────────────


def test_record_batch_cost_to_ledger_writes_total(monkeypatch):
    captured: list[dict] = []

    def _fake_append_run(record):
        captured.append(record)
        return True  # 持久化成功

    monkeypatch.setattr(gen_stance_cache, "append_run", _fake_append_run)
    fake_client = _CostAccruingFakeClient(cost_per_call=0.01)
    fake_client.cost_events = [
        {"model": "haiku", "tokens_in": 10, "tokens_out": 5, "cost_usd": 0.01},
        {"model": "haiku", "tokens_in": 12, "tokens_out": 6, "cost_usd": 0.012},
    ]

    gen_stance_cache._record_batch_cost_to_ledger(fake_client, now_fn=lambda: 1_700_000_000.0)

    assert len(captured) == 1
    record = captured[0]
    assert record["question_type"] == "stance_prewarm"
    assert record["total_cost_usd"] == 0.022
    assert len(record["calls"]) == 2


def test_record_batch_cost_to_ledger_records_unledgered_spend_and_warns_when_append_fails(
    monkeypatch, capsys
):
    """codex HIGH / harper LOW（#84 review 必修 3）：`append_run()` 回 `False`
    （primary + fallback 皆持久化失敗）時，這筆真實花費不能被靜默丟掉——
    必須呼叫 `budget_guard.record_unledgered_spend()`（比照 `orchestrator.py`
    既有慣例，讓 `daily_cost_usd()` 仍算得到這筆花費），並在 stderr 印出
    明確警示，提醒人工核對 `/costs`。"""
    from trustforge.budget_guard import daily_cost_usd

    monkeypatch.setattr(gen_stance_cache, "append_run", lambda record: False)
    fake_client = _CostAccruingFakeClient(cost_per_call=0.02)
    fake_client.cost_events = [
        {"model": "haiku", "tokens_in": 10, "tokens_out": 5, "cost_usd": 0.02},
    ]

    gen_stance_cache._record_batch_cost_to_ledger(fake_client, now_fn=lambda: 1_700_000_000.0)

    captured = capsys.readouterr()
    assert "未進帳本" in captured.err
    assert "/costs" in captured.err
    # 帳本本身沒收到（append_run 回 False），但這筆花費仍必須被
    # daily_cost_usd() 算進「今日已花費」，不因帳本沒記到就被當沒發生。
    assert daily_cost_usd() >= 0.02


def test_record_batch_cost_to_ledger_noop_when_no_cost_events_attr(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(gen_stance_cache, "append_run", lambda record: captured.append(record))
    fake_client = _FakeClient()  # 沒有 cost_events 屬性

    gen_stance_cache._record_batch_cost_to_ledger(fake_client)

    assert captured == []


def test_record_batch_cost_to_ledger_noop_when_cost_events_empty():
    fake_client = _CostAccruingFakeClient()
    fake_client.cost_events = []

    # 不 monkeypatch append_run：若真的呼叫到就會真的寫檔（本測試隔離環境
    # TRUSTFORGE_COST_LEDGER_PATH 由 conftest 的 autouse fixture 隔離到 tmp_path，
    # 但這裡直接斷言 no-op，確保空 cost_events 完全不觸碰帳本）。
    gen_stance_cache._record_batch_cost_to_ledger(fake_client)  # 不應丟例外


# ── issue #84：main() 端到端 — 冪等（全命中 → 不建 client）+ budget 提前中止 ──


def test_main_all_cached_skips_building_client_entirely(tmp_path, monkeypatch):
    out_path = tmp_path / "stance_cache.json"
    out_path.write_text(
        json.dumps({
            "k1": {"label": "entailment", "version": STANCE_CACHE_VERSION},
            "k2": {"label": "neutral", "version": STANCE_CACHE_VERSION},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(gen_stance_cache, "enumerate_candidate_pairs", lambda: _FAKE_PAIRS)

    def _must_not_be_called():
        raise AssertionError("全部命中時不該建立真 client（#84 冪等：零額外外呼）")

    monkeypatch.setattr(gen_stance_cache, "_build_live_client", _must_not_be_called)

    rc = gen_stance_cache.main(["--out", str(out_path)])

    assert rc == 0


def test_main_budget_exhausted_mid_batch_writes_partial_result(tmp_path, monkeypatch, capsys):
    out_path = tmp_path / "stance_cache.json"
    out_path.write_text(
        json.dumps({"keep-me": {"label": "contradiction", "version": STANCE_CACHE_VERSION}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gen_stance_cache, "enumerate_candidate_pairs", lambda: _FAKE_PAIRS)
    fake_client = _FakeClient(label="entailment")
    monkeypatch.setattr(gen_stance_cache, "_build_live_client", lambda: fake_client)

    calls = {"n": 0}

    def _budget_check_factory(client):
        def _check() -> bool:
            calls["n"] += 1
            return calls["n"] <= 1
        return _check

    monkeypatch.setattr(gen_stance_cache, "_make_budget_check", _budget_check_factory)

    rc = gen_stance_cache.main(["--out", str(out_path)])

    assert rc == 2  # budget 提前中止的專屬回傳碼
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["keep-me"] == {"label": "contradiction", "version": STANCE_CACHE_VERSION}
    # 只有第一個 pair（k1）成功，k2 因 budget 中止未寫入
    assert "k1" in written
    assert "k2" not in written
    captured = capsys.readouterr()
    assert "預算" in captured.err


def test_main_records_batch_cost_to_ledger_on_success(tmp_path, monkeypatch):
    out_path = tmp_path / "stance_cache.json"
    monkeypatch.setattr(gen_stance_cache, "enumerate_candidate_pairs", lambda: _FAKE_PAIRS)
    fake_client = _CostAccruingFakeClient(label="entailment", cost_per_call=0.01)
    monkeypatch.setattr(gen_stance_cache, "_build_live_client", lambda: fake_client)
    captured: list[dict] = []
    monkeypatch.setattr(
        gen_stance_cache, "append_run", lambda record: captured.append(record) or True
    )

    rc = gen_stance_cache.main(["--out", str(out_path)])

    assert rc == 0
    assert len(captured) == 1
    assert captured[0]["total_cost_usd"] == 0.02  # 2 pairs * 0.01
