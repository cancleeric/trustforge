# 跨源分歧偵測與新聞信任校準

> Issue: #864
> 依賴: #851（設計文件補充）
> Labels: trust-engine, data-quality, enhancement, size:M

## 背景

`detect_cross_source_signal` 已有跨源分歧偵測骨架（T1–T8 測試覆蓋），但實際上線時新聞來源（CoinDesk、CoinTelegraph 等）的信任分布、觸發條件與使用者可理解性仍需調通。本單聚焦：

1. 確認觸發行為是否正確（客觀 vs 情緒，各至少一筆且 trust 達門檻）。
2. 校準新聞 claim 在 `KIND_REPUTATION`（0.65）× 時效 × 佐證 × 操縱懲罰 組合後的實際分布。
3. 確保「同來源不同大小寫/格式」不會被誤算成多個獨立來源。
4. 優化分歧/共識判斷結果的可解釋摘要。
5. 保留完整溯源（supporting claim_id、來源、stance、信任分）。

## 範圍

- 調通觸發邏輯：客觀 ≥1 筆 + 情緒 ≥1 筆 + trust ≥ 門檻 + 來源合計 ≥2。
- 檢查並以 fixture 驗證新聞 claim 的信任分組成。
- 確保 `_normalize_source_key` / `_canonical_source` 收斂同來源別名。
- 優化 summary 文字：背離/共識/未觸發的三態均有明確中文說明。
- 觸發失敗時提供診斷：缺來源、低信任、無分歧。

## 功能需求

### FR-1: 固定 Fixture 建立分歧/共識案例

建立穩定的 fixture 組合，不依賴即時新聞：

- **分歧案例**：至少一組「客觀資料 bullish（price/onchain）+ 市場情緒 bearish（news/social）」。
- **共識案例**：客觀 + 情緒同向。
- **邊界案例**：情緒類僅 1 筆但 trust 足夠、兩類各 1 source 仍可觸發。
- **不觸發案例**：
  - 缺來源（只有客觀沒情緒）
  - 低信任（全部 < 0.5）
  - 無分歧（方向相同但未達共識門檻）

### FR-2: 新聞信任校準驗證

以 fixture 驗證新聞 claim 在完整 scoring pipeline 下的信任分布：

- 單獨新聞 claim（無佐證）：`KIND_REPUTATION(0.65) × recency × (1 - manipulation)`
- 有佐證的新聞 claim：佐證加分後是否能穩定突破 0.5 門檻
- 操縱關鍵詞命中時的信任衰減幅度
- 確認校準結果不是透過「提高 KIND_REPUTATION 固定值」達成

### FR-3: 來源正規化不變量

同來源不同大小寫/格式不得被誤算為多個獨立來源：

- `"CoinDesk"` / `" coindesk "` / `"COINDESK"` → 同一源
- `"coindesk"` / `"coindesk.com"` → 經 `_canonical_source` 別名收斂為同一源
- 驗證 `_independent_source_keys` 正規化後 set 長度正確
- 驗證 `detect_cross_source_signal` 中 `obj_sources`/`sent_sources` 計數正確

### FR-4: 判斷結果可解釋性

分歧/共識判斷附帶完整可追溯資訊：

- `supporting_claim_ids`：列出所有佐證 claim（客觀面 + 情緒面方向符合者）
- 每個 claim_id 可追回原始 Document（source、kind、url、timestamp）
- `stance_pairs`（若有）：列出語意矛盾配對的來源、stance、claim 文字
- 未觸發時（回 None）：測試能解釋原因（哪個條件不滿足）

### FR-5: 分歧觸發條件精確化

明確文件化並以測試固定以下觸發規則：

| 條件 | 規格 |
|------|------|
| 客觀類至少 1 筆 | kind ∈ OBJECTIVE_KINDS, trust ≥ 0.5 |
| 情緒類至少 1 筆 | kind ∈ _SENTIMENT_KINDS, trust ≥ 0.5 |
| 獨立來源合計 ≥ 2 | `obj_sources ∪ sent_sources` 正規化後 ≥ 2 |
| 各類主導方向 ≠ neutral | 加權投票最高票 ≥ 0.3 × 該類總 trust |
| 背離 | obj_dir ≠ sent_dir 且都非 neutral |
| 共識 | obj_dir == sent_dir 且都非 neutral |

### FR-6: 未觸發診斷資訊

`detect_cross_source_signal` 回 None 時，測試應能驗證具體是哪個前置條件未滿足：

- 缺客觀類（OBJECTIVE_KINDS 無 trust ≥ 0.5 的主張）
- 缺情緒類（_SENTIMENT_KINDS 無 trust ≥ 0.5 的主張）
- 獨立來源不足（正規化後 < 2）
- 主導方向為 neutral（加權投票最高票 < 0.3 × total）

## 非功能需求

- **NFR-1: 不破壞既有測試** — T1–T8 既有 cross_source_signal 測試全綠；source_dedup_invariant 測試全綠。
- **NFR-2: 不修改信任公式權重** — `DEFAULT_WEIGHTS`、`KIND_REPUTATION` 不可改動數值。
- **NFR-3: 不新增 Bedrock 呼叫** — fixture 校準為離線確定性驗證，不打 LLM。
- **NFR-4: 零外部依賴** — 純 stdlib + boto3 原則。
- **NFR-5: 時間預算安全** — 新增邏輯不引入 O(n²) 以上複雜度，不影響 15 分鐘執行窗口。

## 驗收條件

1. 使用固定 fixture 建立至少一組「客觀資料 vs 市場情緒」分歧案例，穩定輸出 `type="divergence"`，不依賴當日即時新聞。
2. 同來源不同大小寫或格式不會被誤算成多個獨立來源（`_independent_source_keys` invariant）。
3. 判斷結果列出 `supporting_claim_ids`，並能解釋未觸發時是缺來源、低信任或無分歧。
4. 現有共識（T2）、低信心（T5）與 abstain 路徑無回歸。
5. 新聞 claim 的信任分布以 fixture 驗證，校準不透過「提高 KIND_REPUTATION 固定值」達成。

## 約束

- 不引入額外第三方依賴（純 stdlib + boto3 原則）
- 不修改 `DEFAULT_WEIGHTS` / `KIND_REPUTATION` 的數值
- 信任評分公式不變：`w_src×Reputation + w_corr×Corroboration + w_rec×Recency − w_manip×Manipulation`
- 所有新測試使用 `BedrockClient(offline=True)` 或 fixture，不打真正的 Bedrock
- `detect_cross_source_signal` 函式簽章向後相容（不破壞既有呼叫端）
