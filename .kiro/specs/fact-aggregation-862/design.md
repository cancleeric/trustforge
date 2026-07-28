# 設計：非破壞式事實聚合與介面呈現優化

> Issue: #862

## 架構決策

### AD-1: 聚合層位置——Report 渲染後處理

聚合邏輯**不插入** trust scoring pipeline（Layer 2），而是作為 Layer 3 的 post-processing：

```
Layer 2 (Trust)        Layer 3 (Agent)                    Output
────────────────  →  ────────────────────────────────  →  ──────
score + aggregate     build_report → evidence_grouper     report.md
                                     (新增模組)            evidence.json (不變)
                                                          evidence_groups (新增)
```

理由：
- 信任評分引擎不受影響，符合「顯示去重與資料保存必須分離」設計要求
- evidence.json 輸出保持完整（符合競賽交付規格）
- 聚合邏輯是純確定性函式，可獨立測試

### AD-2: 相似度判定策略——確定性規則，非 LLM

不使用 Bedrock 做相似度判斷，改用確定性規則組合：

1. **指標名稱匹配**（權重最高）：從 `content_reference` 提取數值指標名稱
   - 正則：`r"([\w\s/]+?)\s*[:：=]\s*([\d,.]+\s*\w*)"` 抓「指標名: 值」
   - 相同指標名 → 判定為同指標時序更新
2. **Jaccard 相似度**（輔助）：對 `content_reference` 做 token 化比對
   - 閾值 0.7（比 `_coordination_template_flags` 的 0.8 稍低，因為數值差異會降低文字重疊）
   - 僅在指標名稱匹配 inconclusive 時作為 fallback
3. **同 source + 同 kind 前置條件**：上述比對只在同源同類內執行

複用既有程式碼：`trust.scoring._normalize` + `_jaccard` 函式。

### AD-3: 趨勢計算

從群組成員的數值提取趨勢：

```python
def _compute_trend(values: list[tuple[float, float]]) -> str | None:
    """values: [(timestamp, numeric_value), ...]，按時間排序。
    
    - len < 2 → None（無法判定）
    - 最新值 > 首值 × 1.02 → "rising"
    - 最新值 < 首值 × 0.98 → "falling"  
    - 否則 → "stable"
    """
```

值域格式：`"{min_val}–{max_val} {unit}"` — 取自 content_reference 中的數值與單位。

### AD-4: evidence_groups 資料流

```
orchestrator.build_report()
  → evidence: list[Evidence]     # 完整 flat list（不變）
  → evidence_groups = group_evidence(evidence)  # 新增呼叫
  → report.evidence_groups = [g.to_dict() for g in evidence_groups]
  → evidence.json 仍寫 evidence（不改）
  → analyze response 多帶 evidence_groups
```

## 新增模組設計

### `src/trustforge/agent/evidence_grouper.py`

```python
"""事實聚合引擎：將同源同指標的時序 Evidence 群組化，供呈現層使用。

設計原則：
  - 只讀 Evidence list，不修改
  - 輸出群組結構，保留所有原始索引（溯源）
  - 確定性規則，不呼叫 Bedrock
  - 不影響 evidence.json 輸出
"""

@dataclass
class EvidenceGroup:
    representative_idx: int
    member_indices: list[int]
    trend: str | None           # "rising" | "falling" | "stable"
    value_range: str | None     # "828–891 TH/s"
    latest_value: str | None

def group_evidence(
    evidence: list[Evidence],
    *,
    time_window_days: int = 7,
    similarity_threshold: float = 0.70,
) -> list[EvidenceGroup]:
    """主入口：將 Evidence list 聚合為群組。

    演算法：
      1. 按 (source, kind) 分桶
      2. 桶內按指標名稱 + Jaccard 相似度再分組
      3. 每組計算趨勢/值域
      4. 單筆獨立成一組（member_indices 長度 = 1）
    
    回傳覆蓋所有 evidence index，保證：
      union(g.member_indices for g in groups) == set(range(len(evidence)))
    """

def _extract_metric_key(content_reference: str) -> str | None:
    """嘗試從 content_reference 提取指標名稱（如 '算力'、'Gas Fee'）。"""

def _extract_numeric_value(content_reference: str) -> tuple[float, str] | None:
    """嘗試提取數值與單位（如 (891.0, 'TH/s')）。"""

def _compute_trend(values: list[tuple[float, float]]) -> str | None:
    """從 (timestamp, value) 序列計算趨勢方向。"""

def _format_value_range(values: list[float], unit: str) -> str:
    """格式化值域字串。"""
```

### 前端新增 `frontend/src/lib/evidenceGrouping.ts`

```typescript
import type { Evidence } from './types'

export interface EvidenceGroup {
  representative_idx: number
  member_indices: number[]
  trend: 'rising' | 'falling' | 'stable' | null
  value_range: string | null
  latest_value: string | null
}

/**
 * 將後端回傳的 evidence_groups 結構轉為前端渲染用的群組映射。
 * 若後端無此欄位，回傳 null（前端退回 flat 模式）。
 */
export function buildGroupMap(
  groups: EvidenceGroup[] | undefined
): Map<number, EvidenceGroup> | null
```

### 前端修改 `EvidenceTable.tsx`

新增 `EvidenceGroupRow` 元件：

```tsx
function EvidenceGroupRow({ group, evidence }: { group: EvidenceGroup; evidence: Evidence[] }) {
  const [expanded, setExpanded] = useState(false)
  const rep = evidence[group.representative_idx]
  // 折疊態：顯示代表 + 趨勢 badge + 成員數
  // 展開態：所有成員各自渲染為 EvidenceRow
}
```

渲染邏輯：
- 有 `evidence_groups` → 按群組渲染（群組間按代表 trust 降序）
- 無 `evidence_groups` → 原始 flat 逐列渲染（向後相容）

## Report schema 擴充

`schema.py::Report` 新增可選欄位：

```python
evidence_groups: list[dict] | None = None   # EvidenceGroup.to_dict() 序列化
```

`data_contracts.py` schema 對應更新（向後相容，optional field）。

## key_basis 去重策略

在 `build_report` 組裝 `key_basis` 時：

```python
# 既有：逐筆 supporting → 逐筆 BasisItem
# 修改：先 group → 每群組取 representative → 一群組一 BasisItem

for group in evidence_groups:
    if all(evidence[i].related_claim == judgment_tag for i in group.member_indices):
        rep_sc = ...  # 對應 representative 的 ScoredClaim
        key_basis.append(BasisItem(
            claim=rep_sc.claim.text,
            explanation=...,
            evidence_idx=group.member_indices,  # 全組索引
        ))
```

並加入面向多樣性守則：若前 N 條 `key_basis` 的 `(source, kind)` 已出現過同組合，跳過該群組，優先選取不同面向。

## Report.facts 聚合摘要

`facts` 章節改為按群組產生：

```python
for group in supporting_groups:
    if len(group.member_indices) >= 2 and group.value_range:
        # "BTC 算力 828–891 TH/s（上升趨勢），來源 f2pool，4 筆觀測 [E3, E7, E12, E15]"
        facts.append(f"{metric} {group.value_range}（{trend_text}），"
                     f"來源 {source}，{count} 筆觀測 [{indices}]")
    else:
        facts.append(sc.claim.text)  # 單筆照舊
```

## 安全考量

- 聚合是**純讀取 + 生成新結構**，不修改/刪除任何既有資料
- `content_reference` 已經過 `_untrusted_prompt_text` 消毒（長度 + injection 防護），聚合引擎不做二次處理
- 前端展開群組成員時直接複用既有 `EvidenceRow`（已有 safeHref、XSS 防護）

## 測試策略

- 單元測試 `tests/test_evidence_grouper.py`：
  - 同源同指標不同值 → 正確聚合 + 趨勢判定
  - 不同方向 → 不聚合
  - 同源不同 kind → 不聚合
  - flagged 條目 → 獨立成組
  - 空 list / 單筆 → 退化為逐筆
  - 數值提取邊界（無單位、中文指標、多數值）
- 整合測試：`build_report` 加入 `evidence_groups` 後，report.facts 不出現 3+ 重複
- 前端測試：`EvidenceTable` 群組折疊/展開渲染正確
