# 非破壞式事實聚合與介面呈現優化

> Issue: #862
> 依賴: #851（設計文件補充）
> Labels: data-quality, enhancement, frontend, size:M

## 背景

同一來源在不同時間抓取的近似事實（如 BTC 算力 828 TH/s → 891 TH/s），會在報告中重複顯示多條幾乎相同的條目，造成使用者閱讀疲勞且無法區分「真的有多筆獨立訊號」與「同一件事被重複呈現」。

## 範圍

本單**只調整報告與介面的呈現層**：
- 原始 Document、Claim、claim_id 與歷史資料**完整保留**。
- 不刪除、不覆寫底層事實資料（evidence.json 輸出不減項）。
- 同來源、同指標的近似條目，在報告/前端中聚合為一個顯示群組。
- 數值隨時間變動時顯示範圍、最新值及趨勢（如「算力 828–891 TH/s，呈上升」）。
- 「三個主要原因」(key_basis) 應選取不同面向，不重複改寫同一件事。
- 聚合後仍可展開查看所有原始 claim_id 與來源。

## 功能需求

### FR-1: 事實聚合引擎 (Presentation Aggregator)

在 `build_report` 產出完整 `Evidence[]` 之後、report.md 渲染之前，加入聚合步驟：

- **聚合規則**（同組條件，須全部成立）：
  1. 同一 `source`（正規化後，見 `_normalize_source_key`）
  2. 同一 `kind`
  3. 內容為「同指標的時序更新」——以語意相似度 + 指標名稱匹配判定
  4. 時間窗口內（預設 7 天）
- **不聚合的例外**：
  - 不同 `direction`（bullish vs bearish）的主張不可聚合
  - `trust_components["manipulation"] > 0` 的 flagged 條目獨立顯示
  - 跨 `kind` 不聚合

### FR-2: 聚合群組資料結構

```python
@dataclass
class EvidenceGroup:
    representative: Evidence          # 群組中 trust 最高者作為代表
    members: list[Evidence]           # 所有原始 Evidence（含代表自己）
    member_indices: list[int]         # 原始 Evidence 索引（溯源用）
    trend: str | None                 # "rising" / "falling" / "stable" / None
    value_range: str | None           # "828–891 TH/s" 格式（數值型才填）
    latest_value: str | None          # 最近一筆的數值摘要
```

- evidence.json 輸出**不改動**：仍為完整 flat list，群組資訊只在 report.md 與前端額外承載。
- Report 新增 `evidence_groups` 欄位（JSON array），供前端使用。

### FR-3: Report 事實章節去重呈現

`report.facts` 中原本逐筆列出的客觀事實，改為聚合後的群組摘要：
- 群組 ≥ 2 筆時：「{指標} {value_range}（{trend_text}），來源 {source}，{count} 筆觀測 [E{idx}…]」
- 群組 = 1 筆時：維持原樣

### FR-4: key_basis 面向多樣性

`build_report` 組裝 `key_basis` 時，以聚合群組為單位而非逐筆：
- 同一群組只取代表 claim 產生一條 `BasisItem`
- `evidence_idx` 欄位帶入該群組所有成員索引
- 保證最終 `key_basis` 各項覆蓋不同面向（kind 或 source 不同）

### FR-5: 前端聚合呈現

EvidenceTable 渲染改為群組模式：
- 群組 ≥ 2 時，顯示為可折疊的群組列（預設折疊）
  - 摘要行：代表內容 + 趨勢標籤 + 值域 + 成員數
  - 展開後：所有成員各自為子列（保留原始 EvidenceRow 結構）
- 群組 = 1 時：渲染不變（等同現行行為）

### FR-6: 後端 API 擴充

`/api/analyze` response 增加 `evidence_groups` 欄位（optional，向後相容）：
```typescript
interface EvidenceGroup {
  representative_idx: number      // 代表 Evidence 在 evidence[] 中的索引
  member_indices: number[]        // 群組所有成員索引
  trend: 'rising' | 'falling' | 'stable' | null
  value_range: string | null
  latest_value: string | null
}
```

## 非功能需求

- **NFR-1: 非破壞性保證** — evidence.json 輸出筆數 = 聚合前筆數，一筆不少
- **NFR-2: 效能** — 聚合計算 O(n log n)，n = evidence 筆數（通常 < 50），不引入額外 Bedrock 呼叫
- **NFR-3: 向後相容** — `evidence_groups` 欄位 optional，前端缺此欄位時退回 flat 顯示
- **NFR-4: 零外部依賴** — 相似度判定用確定性規則（Jaccard + 指標名稱比對），不新增第三方套件
- **NFR-5: 可測試性** — 聚合規則以 fixture 定義，覆蓋邊界情況

## 驗收條件

1. 報告畫面不出現 3 條以上「幾乎相同」的事實條目（同 source + 同 kind + 內容相似度 > 0.8）
2. 聚合條目可展開追溯全部原始 claim_id
3. `evidence.json` 原始 claim 數量及內容不因顯示聚合而減少
4. 數值不同的時序資料不會被誤判為重複而遺失（顯示為趨勢範圍）
5. key_basis 摘要涵蓋不同面向（同群組不重複佔位）
6. 前端缺 `evidence_groups` 欄位時正常退回 flat 顯示

## 約束

- 不引入額外第三方依賴（純 stdlib + boto3 原則）
- 不新增 Bedrock 呼叫（相似度判定為確定性規則）
- 信任評分公式與權重不變
- 聚合邏輯放在呈現層（`build_report` 之後），不影響 trust scoring pipeline
