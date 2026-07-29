# 設計：事實聚合 Production 缺陷修復

> Issue: #862（退件修正）

## 修改範圍

### 1. `src/trustforge/agent/evidence_grouper.py`

#### 1.1 _normalize_source 改用 canonical_source

```python
# Before
def _normalize_source(source: str) -> str:
    return source.strip().casefold()

# After
from trustforge_core.source_identity import canonical_source

def _normalize_source(source: str) -> str:
    """來源正規化：沿用 repo-wide canonical alias 規則。"""
    return canonical_source(source)
```

#### 1.2 group_evidence 分桶加入 direction 維度

Evidence 沒有 direction 欄位，但有 `related_claim` 標籤：
- `related_claim == "反方／低信任訊號"` → direction_bucket = "contrarian"
- 其他 → direction_bucket = "supporting"

```python
# Step 1 修改：分桶 key 加入 direction bucket
def _direction_bucket(ev: Evidence) -> str:
    if ev.related_claim == "反方／低信任訊號":
        return "contrarian"
    return "supporting"

# buckets key: (normalized_source, kind, direction_bucket)
key = (_normalize_source(ev.source), ev.kind, _direction_bucket(ev))
```

#### 1.3 _finalize_group 單位一致性檢查

```python
def _finalize_group(...):
    # 提取 (value, unit) 後檢查 unit 是否一致
    units_seen: set[str] = set()
    for idx in member_indices:
        extracted = extract_numeric_value(evidence[idx].content_reference)
        if extracted:
            _, u = extracted
            if u:
                units_seen.add(u.lower())

    # 若 unit 不一致，不計算數值摘要
    unit_consistent = len(units_seen) <= 1

    if not unit_consistent:
        trend = None
        value_range = None
        latest_value = None
    else:
        # 原有邏輯...
```

### 2. `src/trustforge/agent/orchestrator.py` — key_basis 多樣性

修改 `build_report()` 中 `#862 key_basis 面向多樣性` 區塊：

```python
# 前 3 條必須各自有不同的 (source, kind) 組合
_seen_source_kind: set[tuple[str, str]] = set()
deduped_basis: list[BasisItem] = []
for bi in key_basis:
    if not bi.evidence_idx:
        deduped_basis.append(bi)
        continue
    primary_idx = bi.evidence_idx[0]
    grp_id = _idx_to_group.get(primary_idx)
    if grp_id is not None and grp_id in _seen_groups:
        continue
    ev_rep = evidence[primary_idx]
    sk_key = (_normalize_source_key(ev_rep.source), ev_rep.kind)
    # 前 3 條強制不同面向
    if sk_key in _seen_source_kind and len(deduped_basis) < 3:
        continue  # 前 3 條強制跳過重複面向
    if grp_id is not None:
        _seen_groups.add(grp_id)
        g = ev_groups[grp_id]
        if len(g.member_indices) >= 2:
            bi = BasisItem(
                claim=bi.claim,
                explanation=bi.explanation,
                evidence_idx=list(g.member_indices),
            )
    _seen_source_kind.add(sk_key)
    deduped_basis.append(bi)
```

## 測試策略

新增 `tests/test_evidence_grouper_fix862.py`：

1. `test_direction_isolation_no_merge_bullish_bearish` — 正反方 Evidence 不合組
2. `test_unit_mismatch_no_value_range` — 不同單位時 value_range=None
3. `test_canonical_source_alias` — coindesk.com 與 coindesk 聚合為同組
4. `test_key_basis_top3_diversity` — 前 3 條 BasisItem 面向互異
5. `test_coverage_invariant_with_direction` — 加入 direction 後全覆蓋不變式仍成立

## 回歸風險

- `_normalize_source` 改為 `canonical_source` 可能讓**更多** Evidence 被歸入同桶（alias 收斂），聚合行為更激進 → 不增不減（只是更準確），且全覆蓋不變式保護
- direction 分桶會讓同一 (source, kind) 的 bullish/bearish 不再聚合 → 正確行為，可能增加群組數
- key_basis 前 3 條強制不同面向可能讓某些面向被跳過 → 第 4 條起可補回
