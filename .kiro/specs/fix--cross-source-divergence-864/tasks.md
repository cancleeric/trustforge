# 實作任務：#864 退件修正

## Task 1: 接近真實 connector 輸出的 fixture（FR-1）

- [ ] 新增 `tests/test_news_trust_distribution_evidence.py`
- [ ] CoinDesk 風格 fixture：正式語氣、引述分析師、數據
- [ ] CoinTelegraph 風格 fixture：口語、數據導向
- [ ] 無佐證單獨新聞 fixture
- [ ] 有跨源佐證（同議題不同措辭）fixture
- [ ] 過期新聞（>48h）fixture
- [ ] 操縱關鍵詞命中 fixture
- [ ] Token overlap 由自然語言模式產生，不刻意安排

## Task 2: 信任分分布證據（FR-2）

- [ ] 無佐證 CoinDesk：斷言 trust 在 0.30–0.50
- [ ] 有佐證 CoinDesk+CoinTelegraph：斷言 trust ≥ 0.50
- [ ] 過期 48h 無佐證：斷言 trust 在 0.25–0.35
- [ ] 操縱懲罰：斷言 trust < 0.30
- [ ] 雙源佐證最新：斷言 trust 在 0.55–0.75

## Task 3: report/API 端到端驗證（FR-3）

- [ ] 新增 `tests/test_864_report_api_output.py`
- [ ] test: build_report divergence → Report.cross_source_signal["type"] == "divergence"
- [ ] test: supporting_claim_ids 非空
- [ ] test: 每個 claim_id 可追溯（格式正確 + 在 evidence 可查）
- [ ] test: summary 含方向標籤（偏多/偏空）

## Task 4: issue 範圍重新界定與 attestation

- [ ] PR description 明確記載：本單為既有行為驗證，非 production 修正
- [ ] 列出已驗證的 production 行為清單
- [ ] 記錄「不修改 production code」的理由與依據
- [ ] reviewer attestation：確認驗證範圍與結論

## Task 5: 回歸驗證

- [ ] 既有 test_cross_source_divergence_calibration.py 21 tests 全綠
- [ ] 既有 test_cross_source_signal.py T1–T8 全綠
- [ ] 既有 test_source_dedup_invariant.py 全綠
- [ ] pytest 全套通過
- [ ] lint / type-check 通過
