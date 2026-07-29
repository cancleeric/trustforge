# 退件修正：跨源分歧偵測與新聞信任校準

> Issue: #864（退件重開）
> 前置 spec: done--cross-source-divergence-864
> Labels: trust-engine, data-quality, fix

## 背景

PR #901 交付了完整的 fixture 測試（21 tests），但未包含任何 production code 修正。審查退件指出：

1. 只有 spec 與測試，沒有 production code 調整
2. fixture 刻意安排高 token overlap 的人工句子，不能代表真實案例
3. 缺實際新聞 claim 信任分分布證據
4. report/API 輸出未驗證分歧判斷
5. 缺 reviewer attestation

## 退件分析與定位

經分析 production code（`orchestrator.py` 的 `detect_cross_source_signal`、`scoring.py`），既有邏輯**已經正確實作**了分歧偵測與新聞信任校準。本單原始目的是「調通」——確認行為正確，不需要改演算法。

因此本單重新界定為：**既有行為端到端驗證 + 接近真實的 fixture 補充 + report/API 層驗證**。

## 修正項目

### FR-1: 補充接近真實 connector 輸出的 fixture

**問題**：現有 fixture 用刻意安排高 token overlap 的人工句子。

**修正**：新增以真實 CoinDesk/CoinTelegraph 風格文本為基礎的 fixture：
- CoinDesk 風格：正式、引述分析師、較長句子
- CoinTelegraph 風格：較口語、數據導向
- 無佐證單獨新聞
- 有跨源佐證（CoinDesk + CoinTelegraph 同議題不同措辭）
- 過期新聞（>48h）
- 操縱關鍵詞命中案例

Token overlap 由真實語言模式自然產生，不刻意安排。

### FR-2: 提供實際新聞 claim 信任分分布證據

**修正**：新增 `tests/test_news_trust_distribution_evidence.py`，以 fixture 驗證以下信任分分布：

| 案例 | 預期 trust 範圍 | 來源 |
|------|----------------|------|
| CoinDesk 單獨無佐證 | 0.32–0.48 | KIND_REP(0.65)×0.5 + recency |
| CoinTelegraph 有佐證 | 0.52–0.72 | +corroboration |
| 過期 48h 無佐證 | 0.25–0.35 | recency 衰減 |
| 操縱懲罰 | < 0.30 | -manipulation |
| 雙源佐證最新 | 0.55–0.75 | 最佳案例 |

### FR-3: 驗證 report/API 輸出含分歧判斷

**問題**：只驗內部函式呼叫，沒驗 user-visible output。

**修正**：新增端到端測試走完 `build_report()`，斷言：
- `Report.cross_source_signal` 含 `type="divergence"` 或 `type="consensus"`
- `Report.cross_source_signal["supporting_claim_ids"]` 非空
- 每個 `claim_id` 可在 `evidence` list 中追溯
- `Report.cross_source_signal["summary"]` 含方向標籤

### FR-4: 明確 reviewer attestation 定位

本單為**既有行為驗證**（非 production 修正）：
- 確認既有 `detect_cross_source_signal` 邏輯正確
- 確認信任分布符合設計預期
- 不修改 `DEFAULT_WEIGHTS`、`KIND_REPUTATION`、任何 scoring 邏輯
- 唯一 production 變更：無（純測試/驗證）

## 非功能需求

- 所有 fixture 為離線確定性（不打 Bedrock）
- 不修改既有 scoring 權重或公式
- 不引入新依賴
- 全部既有測試通過

## 驗收條件

- [ ] 以接近真實 connector 輸出的 fixture 覆蓋 CoinDesk/CoinTelegraph 風格
- [ ] 信任分分布證據涵蓋 5 種案例（無佐證/有佐證/過期/操縱/最佳）
- [ ] 端到端 build_report 驗證 cross_source_signal 在 Report/API 輸出中
- [ ] supporting_claim_id 可追溯到 evidence list
- [ ] issue 範圍重新界定為「既有行為驗證」並記錄在結案聲明
- [ ] reviewer attestation 清楚記載「無 production 修正，僅驗證既有行為正確性」
- [ ] 全部既有測試通過（無回歸）
- [ ] 新 branch → PR → attestation 完整流程
