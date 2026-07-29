# 設計：跨源分歧偵測與新聞信任校準退件修正

> Issue: #864（退件修正）

## 定位聲明

本單確認為**既有行為驗證**，不修改 production code。審查退件的核心問題是：

1. fixture 過於人工（刻意高 overlap）→ 補充真實風格 fixture
2. 缺實際分布證據 → 用 fixture 固化信任分預期範圍
3. 未驗 report/API 層 → 補 build_report 端到端測試
4. 缺 attestation → PR 中明確記載

## 新增檔案

### `tests/test_news_trust_distribution_evidence.py`

真實風格 fixture + 信任分分布斷言：

```python
class TestCoinDeskTrustDistribution:
    """CoinDesk 風格新聞 claim 在完整 pipeline 下的信任分分布。"""

    def test_solo_coindesk_no_corroboration(self):
        """單獨 CoinDesk 報導，無佐證。
        預期：KIND_REP(0.65)×0.5 + 0.15×recency ≈ 0.325 + 0.15 = 0.475 (< 0.5)
        """
        docs = [Document(
            id="cd-001", kind="news", source="coindesk",
            text="According to analysts at Goldman Sachs, Bitcoin's correlation "
                 "with traditional risk assets has declined significantly over the "
                 "past quarter, suggesting a maturing market structure that may "
                 "attract additional institutional capital allocation.",
            url="https://coindesk.com/analysis/btc-correlation", ts=NOW,
            meta={"coin": "BTC"},
        )]
        scored = _docs_to_scored(docs)
        # 無佐證 → trust < 0.5
        assert all(sc.trust < 0.50 for sc in scored)
        assert all(sc.trust > 0.30 for sc in scored)

    def test_coindesk_with_cointelegraph_corroboration(self):
        """CoinDesk + CoinTelegraph 同議題 → 互相佐證 → trust ≥ 0.5。"""
        # 同議題不同措辭，自然 token overlap
        docs = [
            Document(id="cd-002", kind="news", source="coindesk",
                     text="Bitcoin ETF net inflows reached $500M this week, "
                          "marking the strongest weekly performance since March. "
                          "BlackRock's IBIT led with $280M in new subscriptions.",
                     url="", ts=NOW, meta={"coin": "BTC"}),
            Document(id="ct-001", kind="news", source="cointelegraph",
                     text="Spot Bitcoin ETF products recorded substantial net inflows "
                          "exceeding $500 million for the week, with BlackRock's IBIT "
                          "accounting for the majority of new capital.",
                     url="", ts=NOW, meta={"coin": "BTC"}),
        ]
        scored = _docs_to_scored(docs)
        # 有佐證 → 至少一筆 ≥ 0.5
        trusts = [sc.trust for sc in scored]
        assert max(trusts) >= 0.50

    def test_expired_news_48h(self):
        """48 小時前的新聞，recency 衰減。"""
        ...

    def test_manipulation_keyword_penalty(self):
        """操縱關鍵詞命中時信任顯著降低。"""
        ...
```

### `tests/test_864_report_api_output.py`

端到端 build_report 驗證：

```python
class TestReportCrossSourceSignal:
    """驗證 build_report 輸出的 cross_source_signal 可見性。"""

    def test_divergence_in_report(self):
        """端到端：price bullish + news bearish → Report 含 divergence signal。"""
        # 構建足夠佐證的 docs → run build_report
        # 斷言 report.cross_source_signal is not None
        # 斷言 report.cross_source_signal["type"] == "divergence"
        # 斷言 report.cross_source_signal["supporting_claim_ids"] 非空
        ...

    def test_claim_ids_traceable_in_evidence(self):
        """supporting_claim_ids 中每個 id 可追溯到 evidence list。"""
        # 走完 build_report → 取 evidence list
        # 對每個 claim_id 驗證格式正確（CLAIM_ID_RE）
        ...

    def test_summary_contains_direction_labels(self):
        """summary 含偏多/偏空方向標籤。"""
        ...
```

## 不修改的檔案

- `src/trustforge/trust/scoring.py` — 不改
- `src/trustforge/agent/orchestrator.py` — 不改
- `DEFAULT_WEIGHTS`、`KIND_REPUTATION` — 不改

## 回歸風險

- 無 production code 修改 → 回歸風險為零
- 新增測試只增加覆蓋度，不影響既有行為
