"""事實聚合引擎單元測試 (issue #862)。

測試 evidence_grouper 模組的核心函式：
- extract_metric_key: 指標名稱提取
- extract_numeric_value: 數值與單位提取
- compute_trend: 趨勢計算
- format_value_range: 值域格式化
- group_evidence: 主聚合邏輯
"""
import pytest

from trustforge.agent.evidence_grouper import (
    EvidenceGroup,
    compute_trend,
    extract_metric_key,
    extract_numeric_value,
    format_value_range,
    group_evidence,
)
from trustforge.schema import Evidence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ev(
    source: str = "test_source",
    fetched_at: str = "2026-07-20T10:00:00Z",
    content_reference: str = "test content",
    related_claim: str = "BTC 市場判斷",
    kind: str = "onchain",
    trust: float = 0.7,
    trust_components: dict | None = None,
    flags: list | None = None,
) -> Evidence:
    """快速建立 Evidence fixture。"""
    return Evidence(
        source=source,
        fetched_at=fetched_at,
        content_reference=content_reference,
        related_claim=related_claim,
        kind=kind,
        trust=trust,
        trust_components=trust_components or {},
        flags=flags or [],
    )


# ---------------------------------------------------------------------------
# extract_metric_key
# ---------------------------------------------------------------------------

class TestExtractMetricKey:
    def test_chinese_metric_colon(self):
        assert extract_metric_key("算力: 891 TH/s") == "算力"

    def test_chinese_metric_full_colon(self):
        assert extract_metric_key("算力：891 TH/s") == "算力"

    def test_english_metric_equals(self):
        assert extract_metric_key("Gas Fee = 12.5 Gwei") == "gas fee"

    def test_english_metric_colon(self):
        assert extract_metric_key("price: 67500 USD") == "price"

    def test_no_metric(self):
        assert extract_metric_key("some random text without metrics") is None

    def test_short_key_rejected(self):
        """單字元指標名應被過濾。"""
        assert extract_metric_key("x: 5") is None

    def test_pure_digit_key_rejected(self):
        """純數字不應被當成指標名。"""
        assert extract_metric_key("123: 456") is None

    def test_metric_with_slash(self):
        assert extract_metric_key("Gas Fee/Avg: 15 Gwei") == "gas fee/avg"


# ---------------------------------------------------------------------------
# extract_numeric_value
# ---------------------------------------------------------------------------

class TestExtractNumericValue:
    def test_integer_with_unit(self):
        assert extract_numeric_value("算力: 891 TH/s") == (891.0, "TH/s")

    def test_decimal_with_unit(self):
        assert extract_numeric_value("Gas Fee = 12.5 Gwei") == (12.5, "Gwei")

    def test_comma_separated(self):
        assert extract_numeric_value("price: 67,500 USD") == (67500.0, "USD")

    def test_fallback_pattern(self):
        """無 metric 格式但有 "數值 單位" 的句子。"""
        result = extract_numeric_value("目前約 2.3 億美元流入")
        assert result is not None
        assert result[0] == 2.3

    def test_no_numeric(self):
        assert extract_numeric_value("no numbers here at all") is None

    def test_large_number(self):
        assert extract_numeric_value("市值: 1,234,567 USD") == (1234567.0, "USD")


# ---------------------------------------------------------------------------
# compute_trend
# ---------------------------------------------------------------------------

class TestComputeTrend:
    def test_rising(self):
        values = [(1.0, 100.0), (2.0, 103.0)]
        assert compute_trend(values) == "rising"

    def test_falling(self):
        values = [(1.0, 100.0), (2.0, 95.0)]
        assert compute_trend(values) == "falling"

    def test_stable(self):
        values = [(1.0, 100.0), (2.0, 100.5)]
        assert compute_trend(values) == "stable"

    def test_single_point(self):
        assert compute_trend([(1.0, 100.0)]) is None

    def test_empty(self):
        assert compute_trend([]) is None

    def test_first_zero_rising(self):
        """首值為零、末值正 → rising。"""
        assert compute_trend([(1.0, 0.0), (2.0, 5.0)]) == "rising"

    def test_first_zero_stable(self):
        """首值末值皆為零 → None（無法判定）。"""
        assert compute_trend([(1.0, 0.0), (2.0, 0.0)]) is None

    def test_boundary_exactly_1_02(self):
        """剛好 1.02 倍，不含邊界 → stable。"""
        values = [(1.0, 100.0), (2.0, 102.0)]
        assert compute_trend(values) == "stable"

    def test_above_1_02(self):
        values = [(1.0, 100.0), (2.0, 102.1)]
        assert compute_trend(values) == "rising"

    def test_multiple_points_trend(self):
        """多點序列，首末差距決定趨勢。"""
        values = [(1.0, 100.0), (2.0, 95.0), (3.0, 90.0), (4.0, 110.0)]
        assert compute_trend(values) == "rising"


# ---------------------------------------------------------------------------
# format_value_range
# ---------------------------------------------------------------------------

class TestFormatValueRange:
    def test_range(self):
        result = format_value_range([828.0, 855.0, 891.0], "TH/s")
        assert result == "828–891 TH/s"

    def test_single_value(self):
        result = format_value_range([100.0], "USD")
        assert result == "100 USD"

    def test_empty(self):
        assert format_value_range([], "X") == ""

    def test_no_unit(self):
        result = format_value_range([50.0, 60.0], "")
        assert result == "50–60"

    def test_decimal_values(self):
        result = format_value_range([12.3, 15.7], "Gwei")
        assert result == "12.3–15.7 Gwei"

    def test_same_min_max(self):
        result = format_value_range([500.0, 500.0], "ETH")
        assert result == "500 ETH"


# ---------------------------------------------------------------------------
# group_evidence: 主聚合邏輯
# ---------------------------------------------------------------------------

class TestGroupEvidence:
    def test_empty_list(self):
        assert group_evidence([]) == []

    def test_single_item(self):
        evs = [_ev()]
        groups = group_evidence(evs)
        assert len(groups) == 1
        assert groups[0].member_indices == [0]
        assert groups[0].trend is None

    def test_same_source_same_kind_same_metric_grouped(self):
        """同源同 kind 同指標 → 聚合為一組。"""
        evs = [
            _ev(source="f2pool", fetched_at="2026-07-20T10:00:00Z",
                content_reference="算力: 828 TH/s", trust=0.8),
            _ev(source="f2pool", fetched_at="2026-07-21T10:00:00Z",
                content_reference="算力: 855 TH/s", trust=0.85),
            _ev(source="f2pool", fetched_at="2026-07-22T10:00:00Z",
                content_reference="算力: 891 TH/s", trust=0.9),
        ]
        groups = group_evidence(evs)
        big = [g for g in groups if len(g.member_indices) >= 2]
        assert len(big) == 1
        assert sorted(big[0].member_indices) == [0, 1, 2]
        assert big[0].representative_idx == 2  # trust 最高
        assert big[0].trend == "rising"
        assert "828" in (big[0].value_range or "")
        assert "891" in (big[0].value_range or "")

    def test_different_kind_not_grouped(self):
        """不同 kind → 不聚合。"""
        evs = [
            _ev(source="f2pool", kind="onchain",
                content_reference="算力: 828 TH/s", trust=0.8),
            _ev(source="f2pool", kind="price",
                content_reference="算力: 855 TH/s", trust=0.85),
        ]
        groups = group_evidence(evs)
        assert len(groups) == 2
        assert all(len(g.member_indices) == 1 for g in groups)

    def test_different_source_not_grouped(self):
        """不同來源 → 不聚合。"""
        evs = [
            _ev(source="f2pool", content_reference="算力: 828 TH/s", trust=0.8),
            _ev(source="antpool", content_reference="算力: 855 TH/s", trust=0.85),
        ]
        groups = group_evidence(evs)
        assert len(groups) == 2
        assert all(len(g.member_indices) == 1 for g in groups)

    def test_flagged_manipulation_stays_independent(self):
        """flagged (manipulation > 0) 條目獨立成組。"""
        evs = [
            _ev(source="reddit", fetched_at="2026-07-20T10:00:00Z",
                content_reference="BTC 大漲啦暴漲", kind="social", trust=0.3,
                trust_components={"manipulation": 0.4}),
            _ev(source="reddit", fetched_at="2026-07-20T11:00:00Z",
                content_reference="BTC 大漲啦暴漲到天", kind="social", trust=0.35,
                trust_components={"manipulation": 0.0}),
        ]
        groups = group_evidence(evs)
        # flagged 那筆獨立，另一筆也獨立（桶內只剩 1 筆，無法配對）
        assert len(groups) == 2
        assert all(len(g.member_indices) == 1 for g in groups)

    def test_time_window_exceeded_splits_groups(self):
        """超出時間窗口（預設 7 天）→ 不聚合或切分。"""
        evs = [
            _ev(source="f2pool", fetched_at="2026-07-01T10:00:00Z",
                content_reference="算力: 800 TH/s", trust=0.8),
            _ev(source="f2pool", fetched_at="2026-07-20T10:00:00Z",
                content_reference="算力: 900 TH/s", trust=0.9),
        ]
        groups = group_evidence(evs, time_window_days=7)
        # 相隔 19 天，超出 7 天窗口 → 不聚合
        assert len(groups) == 2
        assert all(len(g.member_indices) == 1 for g in groups)

    def test_all_dissimilar_each_independent(self):
        """全部不相似 → 每筆獨立一組。"""
        evs = [
            _ev(source="src1", content_reference="BTC ETF 資金流入 2.3 億", kind="news", trust=0.6),
            _ev(source="src2", content_reference="SOL 鏈上活動增加", kind="onchain", trust=0.7),
            _ev(source="src3", content_reference="監管機構發布新規", kind="regulatory", trust=0.8),
        ]
        groups = group_evidence(evs)
        assert len(groups) == 3
        assert all(len(g.member_indices) == 1 for g in groups)

    def test_full_coverage_invariant(self):
        """所有 evidence index 必須恰好被一個群組覆蓋（全覆蓋不漏項）。"""
        evs = [
            _ev(source="f2pool", fetched_at=f"2026-07-{20+i}T10:00:00Z",
                content_reference=f"算力: {800+i*10} TH/s", trust=0.7 + i * 0.05)
            for i in range(5)
        ] + [
            _ev(source="coindesk", content_reference="some news", kind="news", trust=0.5),
        ]
        groups = group_evidence(evs)
        all_indices = set()
        for g in groups:
            # 成員不重疊
            for idx in g.member_indices:
                assert idx not in all_indices, f"index {idx} appears in multiple groups"
                all_indices.add(idx)
        # 全覆蓋
        assert all_indices == set(range(len(evs)))

    def test_jaccard_fallback_grouping(self):
        """無指標名稱但文字高度相似 → Jaccard fallback 聚合。"""
        evs = [
            _ev(source="coindesk", fetched_at="2026-07-20T10:00:00Z",
                content_reference="比特幣現貨 ETF 今日資金淨流入 2.3 億美元，連續第五天淨流入",
                kind="news", trust=0.6),
            _ev(source="coindesk", fetched_at="2026-07-21T10:00:00Z",
                content_reference="比特幣現貨 ETF 今日資金淨流入 2.8 億美元，連續第六天淨流入",
                kind="news", trust=0.65),
        ]
        groups = group_evidence(evs, similarity_threshold=0.60)
        big = [g for g in groups if len(g.member_indices) >= 2]
        assert len(big) == 1

    def test_source_case_insensitive(self):
        """來源正規化：大小寫不同視為同源。"""
        evs = [
            _ev(source="F2Pool", fetched_at="2026-07-20T10:00:00Z",
                content_reference="算力: 828 TH/s", trust=0.8),
            _ev(source="f2pool", fetched_at="2026-07-21T10:00:00Z",
                content_reference="算力: 855 TH/s", trust=0.85),
        ]
        groups = group_evidence(evs)
        big = [g for g in groups if len(g.member_indices) >= 2]
        assert len(big) == 1

    def test_group_sorted_by_representative_trust(self):
        """群組按代表 trust 降序排列。"""
        evs = [
            _ev(source="low_trust_src", content_reference="data: 10 X", kind="news", trust=0.3),
            _ev(source="high_trust_src", content_reference="data: 20 Y", kind="onchain", trust=0.9),
        ]
        groups = group_evidence(evs)
        trusts = [evs[g.representative_idx].trust for g in groups]
        assert trusts == sorted(trusts, reverse=True)

    def test_to_dict(self):
        """EvidenceGroup.to_dict() 序列化正確。"""
        g = EvidenceGroup(
            representative_idx=2,
            member_indices=[0, 1, 2],
            trend="rising",
            value_range="828–891 TH/s",
            latest_value="891.0 TH/s",
        )
        d = g.to_dict()
        assert d["representative_idx"] == 2
        assert d["member_indices"] == [0, 1, 2]
        assert d["trend"] == "rising"
        assert d["value_range"] == "828–891 TH/s"
        assert d["latest_value"] == "891.0 TH/s"

    def test_different_metric_same_source_not_grouped(self):
        """同源同 kind 但不同指標名稱 → 不聚合。"""
        evs = [
            _ev(source="f2pool", fetched_at="2026-07-20T10:00:00Z",
                content_reference="算力: 828 TH/s", trust=0.8),
            _ev(source="f2pool", fetched_at="2026-07-21T10:00:00Z",
                content_reference="難度: 85.5 T", trust=0.85),
        ]
        groups = group_evidence(evs)
        assert len(groups) == 2
        assert all(len(g.member_indices) == 1 for g in groups)
