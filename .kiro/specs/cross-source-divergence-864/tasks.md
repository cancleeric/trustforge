# 實作任務：跨源分歧偵測與新聞信任校準

> Issue: #864

## 任務清單

- [ ] 1. 建立端到端校準測試檔 `tests/test_cross_source_divergence_calibration.py` 骨架
  - 建立 fixture 工廠函式（`_doc`, `_sc`, `_docs_to_scored`）
  - 使用固定 NOW 時間戳
  - import 必要模組（`extract_claims`, `score`, `detect_cross_source_signal`, `_independent_source_keys`）
  - 確認 import 無誤、空測試可執行

- [ ] 2. 實作 `TestNewsTrustDistribution` — 新聞信任分布校準
  - `test_solo_news_claim_below_threshold`：單獨新聞 claim（無佐證）走完整 pipeline 驗證 trust < 0.5
  - `test_news_with_corroboration_above_threshold`：新聞 + 另一來源佐證 → trust ≥ 0.5
  - `test_news_with_manipulation_flag_penalty`：操縱關鍵詞命中時 trust 顯著下降
  - `test_news_recency_decay`：過期新聞 trust 衰減驗證

- [ ] 3. 實作 `TestDivergenceFixture` — 固定 fixture 分歧觸發
  - `test_price_bullish_vs_news_bearish_divergence`：客觀(price) bullish + 情緒(news+social 互為佐證) bearish → `type="divergence"`
  - `test_onchain_bearish_vs_social_bullish_divergence`：客觀(onchain) bearish + 情緒(social+news 互為佐證) bullish → `type="divergence"`
  - 使用端到端 pipeline（Document → extract_claims → score → detect_cross_source_signal）
  - 斷言 result 包含 `objective_direction`、`sentiment_direction`、`supporting_claim_ids`

- [ ] 4. 實作 `TestConsensusFixture` + `TestNotTriggered` — 共識與未觸發
  - `test_price_and_news_both_bullish_consensus`：客觀 + 情緒同向 → `type="consensus"`
  - `test_no_trigger_missing_sentiment`：缺情緒類 → None
  - `test_no_trigger_low_trust`：全低 trust → None
  - `test_no_trigger_neutral_dominant`：主導 neutral → None
  - `test_no_trigger_same_source_inflated`：同源變體不膨脹 → 來源不足 → None

- [ ] 5. 實作 `TestSourceNormalization` — 來源正規化不變量
  - `test_case_whitespace_variants_collapse`：大小寫/空白變體收斂
  - `test_alias_variants_collapse`：已知別名收斂
  - `test_truly_distinct_sources_preserved`：不同來源不過度合併
  - `test_divergence_with_normalized_source_still_triggers`：正規化後 ≥2 源仍觸發

- [ ] 6. 實作 `TestExplainability` — 結果可追溯性
  - `test_supporting_claim_ids_present`：結果包含 claim_ids
  - `test_claim_ids_traceable_to_source`：claim_id 可追回 Document source/kind
  - `test_summary_contains_direction_labels`：summary 包含方向標籤文字
  - `test_no_investment_advice_in_summary`：summary 無買賣決策字眼

- [ ] 7. 全量回歸驗證
  - 執行 `pytest tests/test_cross_source_signal.py` — T1–T8 全綠
  - 執行 `pytest tests/test_source_dedup_invariant.py` — 全綠
  - 執行 `pytest tests/test_cross_source_divergence_calibration.py` — 新測試全綠
  - 確認無其他測試被影響
