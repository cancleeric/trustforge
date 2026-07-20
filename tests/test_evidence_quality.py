"""Issue #253: Evidence 組裝品質追蹤 — /api/evidence-quality 端點測試。

驗證：
- 無資料時回 status=no_data、各指標為 None
- 有 completed jobs 時正確計算 completeness/source_diversity/avg_evidence_count/freshness
- evidence 有缺欄位時 completeness < 1.0
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest


@pytest.fixture
def analysis_db(tmp_path, monkeypatch):
    """建立一個有 analysis_results 的 SQLite DB 供 AnalysisFlow(readonly=True) 讀取。"""
    db_path = tmp_path / "trustforge.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE analysis_results (
          result_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, snapshot_id TEXT NOT NULL,
          coin TEXT NOT NULL, mode TEXT NOT NULL, question TEXT NOT NULL,
          payload_json TEXT NOT NULL, published_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX idx_analysis_results_lookup
          ON analysis_results(coin, mode, question, published_at DESC)
    """)
    conn.commit()
    conn.close()

    # Monkeypatch the _db_path so AnalysisFlow(readonly=True) finds our test db
    import trustforge.analysis_flow as af_mod
    monkeypatch.setattr(af_mod, "_db_path", lambda path=None: db_path)
    return db_path


def _insert_result(db_path: Path, job_id: str, evidence_list: list[dict], published_at: float | None = None):
    """Insert a fake analysis result with given evidence into the test DB."""
    conn = sqlite3.connect(str(db_path))
    payload = json.dumps({
        "evidence": evidence_list,
        "report": {"market_judgment": "test"},
    }, ensure_ascii=False)
    conn.execute(
        "INSERT INTO analysis_results VALUES(?,?,?,?,?,?,?,?)",
        (f"result-{job_id}", job_id, f"snap-{job_id}", "BTC", "multi_source",
         "test question", payload, published_at or time.time()),
    )
    conn.commit()
    conn.close()


def _make_evidence(source: str = "ohlcv-csv", fetched_at: str = "2026-07-20T00:00:00Z",
                   content_reference: str = "BTC close=68000", related_claim: str = "price stable") -> dict:
    """Helper to create a complete evidence dict."""
    return {
        "source": source,
        "fetched_at": fetched_at,
        "content_reference": content_reference,
        "related_claim": related_claim,
        "kind": "price",
        "trust": 0.85,
    }


class TestEvidenceQualityNoData:
    """無資料情況。"""

    def test_no_db_returns_no_data(self, tmp_path, monkeypatch):
        """SQLite 不存在時回 no_data。"""
        import trustforge.analysis_flow as af_mod
        monkeypatch.setattr(af_mod, "_db_path", lambda path=None: tmp_path / "nonexistent.sqlite3")

        from trustforge.web import _handle_api_evidence_quality
        code, body = _handle_api_evidence_quality()
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert data["data"]["status"] == "no_data"
        assert data["data"]["jobs_sampled"] == 0
        assert data["data"]["completeness"] is None

    def test_empty_db_returns_no_data(self, analysis_db):
        """DB 存在但無 results 時回 no_data。"""
        from trustforge.web import _handle_api_evidence_quality
        code, body = _handle_api_evidence_quality()
        assert code == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert data["data"]["status"] == "no_data"
        assert data["data"]["jobs_sampled"] == 0


class TestEvidenceQualityWithData:
    """有 completed jobs 資料的情況。"""

    def test_full_completeness(self, analysis_db):
        """所有 evidence 必填欄位都有值時 completeness=1.0。"""
        _insert_result(analysis_db, "job-1", [
            _make_evidence("ohlcv-csv"),
            _make_evidence("coingecko-price", fetched_at="2026-07-19T12:00:00Z"),
        ])
        _insert_result(analysis_db, "job-2", [
            _make_evidence("news-rss", content_reference="BTC breakout news"),
        ])

        from trustforge.web import _handle_api_evidence_quality
        code, body = _handle_api_evidence_quality()
        assert code == 200
        data = json.loads(body)["data"]
        assert data["status"] == "ok"
        assert data["jobs_sampled"] == 2
        assert data["total_evidence"] == 3
        assert data["completeness"] == 1.0

    def test_partial_completeness(self, analysis_db):
        """部分 evidence 缺必填欄位時 completeness < 1.0。"""
        _insert_result(analysis_db, "job-1", [
            _make_evidence("ohlcv-csv"),
            # Missing content_reference and related_claim
            {"source": "social", "fetched_at": "2026-07-20T00:00:00Z",
             "content_reference": "", "related_claim": ""},
        ])

        from trustforge.web import _handle_api_evidence_quality
        code, body = _handle_api_evidence_quality()
        data = json.loads(body)["data"]
        # First evidence: 4/4 complete, Second: 2/4 (source+fetched_at ok, content_ref+related_claim empty)
        # Total: 6/8 = 0.75
        assert data["completeness"] == 0.75

    def test_source_diversity(self, analysis_db):
        """unique sources / total evidence。"""
        _insert_result(analysis_db, "job-1", [
            _make_evidence("ohlcv-csv"),
            _make_evidence("ohlcv-csv"),  # same source
            _make_evidence("news-rss"),
        ])

        from trustforge.web import _handle_api_evidence_quality
        code, body = _handle_api_evidence_quality()
        data = json.loads(body)["data"]
        # 2 unique sources / 3 total = 0.6667
        assert data["unique_sources"] == 2
        assert abs(data["source_diversity"] - 2 / 3) < 0.001

    def test_avg_evidence_count(self, analysis_db):
        """平均 evidence 筆數。"""
        _insert_result(analysis_db, "job-1", [_make_evidence()] * 4)
        _insert_result(analysis_db, "job-2", [_make_evidence()] * 6)

        from trustforge.web import _handle_api_evidence_quality
        code, body = _handle_api_evidence_quality()
        data = json.loads(body)["data"]
        assert data["avg_evidence_count"] == 5.0

    def test_freshness_calculation(self, analysis_db):
        """freshness 計算正確（從 fetched_at 到現在的秒差平均）。"""
        # Use a timestamp ~1 hour ago
        from datetime import datetime, timezone, timedelta
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

        _insert_result(analysis_db, "job-1", [
            _make_evidence(fetched_at=one_hour_ago),
            _make_evidence(fetched_at=two_hours_ago),
        ])

        from trustforge.web import _handle_api_evidence_quality
        code, body = _handle_api_evidence_quality()
        data = json.loads(body)["data"]
        # Average should be ~5400 seconds (1.5 hours), allow some tolerance
        assert data["freshness_avg_seconds"] is not None
        assert 5000 < data["freshness_avg_seconds"] < 6000

    def test_max_20_jobs_sampled(self, analysis_db):
        """最多讀取 20 個 jobs。"""
        for i in range(25):
            _insert_result(analysis_db, f"job-{i}", [_make_evidence()])

        from trustforge.web import _handle_api_evidence_quality
        code, body = _handle_api_evidence_quality()
        data = json.loads(body)["data"]
        assert data["jobs_sampled"] == 20
