"""CA-09: DB-free comparison snapshot synthesis tests.

測試從 out/snapshots/{coin}.json 讀取 A/B 快照並合成 ComparisonReport，
含 metadata 相容性檢查（revision/freshness/window）以及手動優先保留與重啟冪等。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trustforge.comparison_contract import (
    ComparisonReport,
    validate_comparison_report,
)
from trustforge.comparison_snapshot import (
    MAX_SNAPSHOT_AGE_DAYS,
    synthesize_comparison_from_snapshots,
)
from trustforge.schema import BasisItem, Evidence, Report


# ---------------------------------------------------------------------------
# Snapshot 輔助 factory
# ---------------------------------------------------------------------------

def _make_report_dict(coin: str) -> dict:
    """產生極簡但結構完整的 Report dict（可通過 _report_from_dict）。"""
    return {
        "coin": coin,
        "question_type": "multi_source",
        "question": f"分析 {coin} 近期市場狀況",
        "market_judgment": f"{coin} 市場展望中性偏正。",
        "facts": [f"{coin} 近週交易量上升 5%"],
        "inferences": [f"成交量上升反映市場活躍度提升"],
        "key_basis": [
            {"claim": f"{coin} 交易量上升", "explanation": "成交量數據證明", "evidence_idx": [0]},
        ],
        "confidence": 0.72,
        "limits": ["資料僅為一週區間"],
        "could_flip": ["重大監管變化可能推翻結論"],
        "contrarian": ["近期波動放大值得關注"],
        "generated_at": "2025-07-28T10:00:00Z",
        "schema_version": "3.1.0",
        "direction": "neutral",
        "cross_source_signal": None,
        "insights": None,
        "hypothesis_ledger": None,
        "calibrated_confidence": 0.65,
        "decision_state": "normal",
        "asset_context": None,
        "risk_notices": [],
        "asset_intrinsic_assessment": None,
        "term_annotations": [],
    }


def _make_evidence_list() -> list[dict]:
    """產生涵蓋四個比較面向（price/onchain/news/regulatory）的 Evidence dict 清單。"""
    return [
        {
            "source": "hoya-ohlcv",
            "fetched_at": "2025-07-28T09:00:00Z",
            "content_reference": "OHLCV 2025-07-21 O=62000 H=63500 L=61500 C=63000 V=12000",
            "related_claim": "價格走勢正面",
            "kind": "price",
            "trust": 0.85,
            "trust_components": {"accuracy": 0.9, "freshness": 0.8},
        },
        {
            "source": "glassnode",
            "fetched_at": "2025-07-28T09:05:00Z",
            "content_reference": "活躍地址數月增 8%，大額流入增加 12%",
            "related_claim": "鏈上活動活躍",
            "kind": "onchain",
            "trust": 0.78,
            "trust_components": {"accuracy": 0.8, "freshness": 0.75},
        },
        {
            "source": "coindesk",
            "fetched_at": "2025-07-28T09:10:00Z",
            "content_reference": "ETF 資金連續淨流入，分析師認為市場情緒偏多",
            "related_claim": "新聞情緒正面",
            "kind": "news",
            "trust": 0.72,
            "trust_components": {"accuracy": 0.7, "freshness": 0.75},
        },
        {
            "source": "sec-gov",
            "fetched_at": "2025-07-28T09:15:00Z",
            "content_reference": "ETF 期權獲 SEC 核准，擴大機構參與",
            "related_claim": "監管環境改善",
            "kind": "regulatory",
            "trust": 0.80,
            "trust_components": {"accuracy": 0.85, "freshness": 0.75},
        },
    ]


def _make_compatible_metadata(
    *,
    revision: str = "abc123def",
    freshness: datetime | None = None,
    window_days: int = 30,
) -> dict:
    """產生可通過相容性檢查的 metadata dict。

    可指定 revision、freshness（預設現在時間），與 window 天數（往後延伸 window_days 天）。
    """
    now = datetime.now(timezone.utc)
    return {
        "revision": revision,
        "freshness": (freshness or now).isoformat(),
        "window": {
            "start": (now - timedelta(days=window_days)).isoformat(),
            "end": (now + timedelta(days=7)).isoformat(),
        },
    }


def _write_snapshot(
    snapshot_dir: Path,
    coin: str,
    *,
    metadata: dict | None = None,
) -> None:
    """寫入 snapshot JSON 檔案到指定目錄。

    若未指定 metadata，預設產生相容的 metadata。
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "metadata": metadata if metadata is not None else _make_compatible_metadata(),
        "report": _make_report_dict(coin),
        "evidence": _make_evidence_list(),
    }
    (snapshot_dir / f"{coin}.json").write_text(
        json.dumps(payload, ensure_ascii=False)
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestComparisonSnapshot:
    """CA-09: snapshot synthesis 單元測試。"""

    def test_missing_snapshot_returns_none(self, tmp_path):
        """任一 snapshot 缺失時回傳 None。"""
        snap_dir = tmp_path / "snapshots"

        # 只放 BTC，缺 ETH
        _write_snapshot(snap_dir, "BTC")

        result = synthesize_comparison_from_snapshots(
            "BTC", "ETH", "BTC vs ETH 比較", snapshot_dir=snap_dir,
        )
        assert result is None

    def test_both_missing_returns_none(self, tmp_path):
        """兩個 snapshot 都缺失時回傳 None。"""
        snap_dir = tmp_path / "snapshots"
        # 不放任何 snapshot
        snap_dir.mkdir(parents=True, exist_ok=True)

        result = synthesize_comparison_from_snapshots(
            "BTC", "ETH", "BTC vs ETH 比較", snapshot_dir=snap_dir,
        )
        assert result is None

    def test_both_snapshots_produces_report(self, tmp_path):
        """A+B 皆存在時應回傳有效的 ComparisonReport。"""
        snap_dir = tmp_path / "snapshots"
        _write_snapshot(snap_dir, "BTC")
        _write_snapshot(snap_dir, "ETH")

        result = synthesize_comparison_from_snapshots(
            "BTC", "ETH", "BTC vs ETH 比較分析", snapshot_dir=snap_dir,
        )

        assert result is not None
        assert isinstance(result, ComparisonReport)
        assert result.coin_a == "BTC"
        assert result.coin_b == "ETH"
        assert result.query == "BTC vs ETH 比較分析"
        assert len(result.conclusion.strip()) > 0, "conclusion 不可為空"
        assert len(result.dimensions) == 4, f"預期 4 個面向，實際 {len(result.dimensions)} 個"
        assert len(result.supporting_evidence_a) > 0
        assert len(result.supporting_evidence_b) > 0

    def test_snapshot_report_passes_validation(self, tmp_path):
        """snapshot 產出的 ComparisonReport 必須通過 validate_comparison_report。"""
        snap_dir = tmp_path / "snapshots"
        _write_snapshot(snap_dir, "BTC")
        _write_snapshot(snap_dir, "ETH")

        result = synthesize_comparison_from_snapshots(
            "BTC", "ETH", "BTC vs ETH 比較", snapshot_dir=snap_dir,
        )

        assert result is not None
        violations = validate_comparison_report(result, _raise=False)
        assert len(violations) == 0, (
            f"snapshot 產出的 ComparisonReport 有 {len(violations)} 項契約違規：\n"
            + "\n".join(violations)
        )

    def test_corrupt_json_returns_none(self, tmp_path):
        """snapshot 檔案內容為損毀 JSON 時回傳 None。"""
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "BTC.json").write_text("this is not json")
        _write_snapshot(snap_dir, "ETH")

        result = synthesize_comparison_from_snapshots(
            "BTC", "ETH", "測試", snapshot_dir=snap_dir,
        )
        assert result is None

    def test_malformed_snapshot_missing_report_key_returns_none(self, tmp_path):
        """snapshot JSON 缺 "report" 或 "evidence" key 時回傳 None。"""
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        # 不完整的 snapshot：缺 evidence key（但含 metadata 所以先過相容檢查）
        (snap_dir / "BTC.json").write_text(
            json.dumps({
                "metadata": _make_compatible_metadata(),
                "report": _make_report_dict("BTC"),
            })
        )
        _write_snapshot(snap_dir, "ETH")

        result = synthesize_comparison_from_snapshots(
            "BTC", "ETH", "測試", snapshot_dir=snap_dir,
        )
        assert result is None

    # -------------------------------------------------------------------
    # CA-09 fix: metadata 相容性 guard + manual priority + restart 冪等
    # -------------------------------------------------------------------

    def test_revision_mismatch_returns_none(self, tmp_path):
        """A/B snapshot revision 不一致時回傳 None。"""
        snap_dir = tmp_path / "snapshots"
        _write_snapshot(snap_dir, "BTC",
                        metadata=_make_compatible_metadata(revision="revA"))
        _write_snapshot(snap_dir, "ETH",
                        metadata=_make_compatible_metadata(revision="revB"))

        result = synthesize_comparison_from_snapshots(
            "BTC", "ETH", "BTC vs ETH", snapshot_dir=snap_dir,
        )
        assert result is None

    def test_stale_snapshot_returns_none(self, tmp_path):
        """任一 snapshot freshness 超過 7 天時回傳 None。"""
        snap_dir = tmp_path / "snapshots"
        now = datetime.now(timezone.utc)
        stale_time = now - timedelta(days=MAX_SNAPSHOT_AGE_DAYS + 1)
        stale_meta = _make_compatible_metadata(freshness=stale_time)

        _write_snapshot(snap_dir, "BTC", metadata=stale_meta)
        _write_snapshot(snap_dir, "ETH")

        result = synthesize_comparison_from_snapshots(
            "BTC", "ETH", "BTC vs ETH", snapshot_dir=snap_dir,
        )
        assert result is None

    def test_window_no_overlap_returns_none(self, tmp_path):
        """A/B snapshot window 無時間重疊時回傳 None。"""
        snap_dir = tmp_path / "snapshots"
        now = datetime.now(timezone.utc)

        # A 的時間範圍在過去 (60~45 天前)
        meta_a = _make_compatible_metadata()
        meta_a["window"] = {
            "start": (now - timedelta(days=60)).isoformat(),
            "end": (now - timedelta(days=45)).isoformat(),
        }
        # B 的時間範圍在最近 (30 天前 ~ 現在)，與 A 不重疊
        meta_b = _make_compatible_metadata()
        meta_b["window"] = {
            "start": (now - timedelta(days=30)).isoformat(),
            "end": now.isoformat(),
        }

        _write_snapshot(snap_dir, "BTC", metadata=meta_a)
        _write_snapshot(snap_dir, "ETH", metadata=meta_b)

        result = synthesize_comparison_from_snapshots(
            "BTC", "ETH", "BTC vs ETH", snapshot_dir=snap_dir,
        )
        assert result is None

    def test_compatible_snapshot_produces_report(self, tmp_path):
        """revision/freshness/window 全部相容時應產出有效 ComparisonReport。"""
        snap_dir = tmp_path / "snapshots"
        _write_snapshot(snap_dir, "BTC")
        _write_snapshot(snap_dir, "ETH")

        result = synthesize_comparison_from_snapshots(
            "BTC", "ETH", "相容測試", snapshot_dir=snap_dir,
        )

        assert result is not None
        assert isinstance(result, ComparisonReport)
        assert result.coin_a == "BTC"
        assert result.coin_b == "ETH"
        assert len(result.conclusion.strip()) > 0

    def test_manual_priority_preserved(self, tmp_path):
        """手寫 snapshot 內容應被保留使用，不被自動合成改寫。"""
        snap_dir = tmp_path / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)

        # 手動撰寫 snapshot，含特殊識別字串以證明未被覆寫
        manual_report_a = _make_report_dict("BTC")
        manual_report_a["market_judgment"] = "MANUAL_JUDGMENT_BTC_PRESERVED"
        manual_report_a["facts"] = ["手工 BTC 事實：獨家資訊"]
        payload_a = {
            "metadata": _make_compatible_metadata(),
            "report": manual_report_a,
            "evidence": _make_evidence_list(),
        }
        (snap_dir / "BTC.json").write_text(
            json.dumps(payload_a, ensure_ascii=False)
        )

        manual_report_b = _make_report_dict("ETH")
        manual_report_b["market_judgment"] = "MANUAL_JUDGMENT_ETH_PRESERVED"
        payload_b = {
            "metadata": _make_compatible_metadata(),
            "report": manual_report_b,
            "evidence": _make_evidence_list(),
        }
        (snap_dir / "ETH.json").write_text(
            json.dumps(payload_b, ensure_ascii=False)
        )

        result = synthesize_comparison_from_snapshots(
            "BTC", "ETH", "手動優先測試", snapshot_dir=snap_dir,
        )
        assert result is not None

        # 驗證 supporting_report 保有手寫內容
        assert result.supporting_report_a is not None
        assert result.supporting_report_b is not None
        assert "MANUAL_JUDGMENT_BTC" in result.supporting_report_a.market_judgment
        assert "MANUAL_JUDGMENT_ETH" in result.supporting_report_b.market_judgment

        # 驗證磁碟上的 snapshot 檔案未遭修改
        btc_after = json.loads((snap_dir / "BTC.json").read_text())
        assert btc_after["report"]["market_judgment"] == "MANUAL_JUDGMENT_BTC_PRESERVED"
        assert btc_after["report"]["facts"] == ["手工 BTC 事實：獨家資訊"]

    def test_restart_idempotent(self, tmp_path):
        """兩次 synthesize 呼叫應回傳相同結果（deterministic fallback 冪等）。"""
        snap_dir = tmp_path / "snapshots"
        _write_snapshot(snap_dir, "BTC")
        _write_snapshot(snap_dir, "ETH")

        result1 = synthesize_comparison_from_snapshots(
            "BTC", "ETH", "重啟測試", snapshot_dir=snap_dir,
        )
        result2 = synthesize_comparison_from_snapshots(
            "BTC", "ETH", "重啟測試", snapshot_dir=snap_dir,
        )

        assert result1 is not None
        assert result2 is not None
        assert result1.coin_a == result2.coin_a
        assert result1.coin_b == result2.coin_b
        assert result1.conclusion == result2.conclusion
        assert result1.confidence == result2.confidence
        assert len(result1.dimensions) == len(result2.dimensions)
        # 每個 dimension 的 decision 一致
        for d1, d2 in zip(result1.dimensions, result2.dimensions):
            assert d1.dimension == d2.dimension
            assert d1.decision == d2.decision
