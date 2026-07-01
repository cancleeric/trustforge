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
    """

    def __init__(self, fail_on: str, label: str = "entailment"):
        self.fail_on = fail_on
        self.label = label
        self.calls: list[tuple[str, str]] = []

    def classify_stance_strict(self, a: str, b: str) -> str:
        self.calls.append((a, b))
        if a == self.fail_on or b == self.fail_on:
            raise TimeoutError(f"simulated Bedrock failure on {self.fail_on!r}")
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
