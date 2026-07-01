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
    """固定回傳同一個 label 的假 client：不匯入 boto3，不打真 AWS。"""

    def __init__(self, label: str = "entailment"):
        self.label = label
        self.calls: list[tuple[str, str]] = []

    def classify_stance(self, a: str, b: str) -> str:
        self.calls.append((a, b))
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


# ── classify_pairs：假 client，不打真 AWS ───────────────────────────────────


def test_classify_pairs_uses_client_and_tags_version():
    fake_client = _FakeClient(label="contradiction")
    pairs = {"k1": ("claim text a", "claim text b")}

    entries = gen_stance_cache.classify_pairs(fake_client, pairs)

    assert entries == {"k1": {"label": "contradiction", "version": STANCE_CACHE_VERSION}}
    assert fake_client.calls == [("claim text a", "claim text b")]


# ── --dry-run：不呼叫 client、不寫檔 ─────────────────────────────────────────


def test_main_dry_run_does_not_write_file(tmp_path, capsys):
    out_path = tmp_path / "stance_cache.json"

    rc = gen_stance_cache.main(["--dry-run", "--out", str(out_path)])

    assert rc == 0
    assert not out_path.exists()
    captured = capsys.readouterr()
    assert "候選對數" in captured.out
    assert "--dry-run" in captured.out
