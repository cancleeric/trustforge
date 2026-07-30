# #808 設計：分離的五角度 Synthesis 契約

## 資料模型

`AngleResult` 增加不可省略的 provenance context：`mode`、`question`、`snapshot_id`。缺少任一欄位時，結果標記為不完整而非補上合成預設值。

`MultiAngleReport` 使用三個獨立 collection：

```text
DirectionDivergence { angle_a, angle_b, direction_a, direction_b }
CompletenessGap { angle, completeness, missing_fields, comparison_baseline? }
EvidenceOverlap { angle_a, angle_b, shared_source_ids, union_source_ids, overlap_ratio }
EvidenceIndependence { ratio, independent_source_ids, shared_source_ids, active_source_ids }
```

相容的 `conflicts` 若仍對外輸出，只能由這三種 collection 投影產生，包含 `kind` 與對應 collection item 的 ID。前端與敘事消費者必須轉用獨立欄位。

## 演算法

1. 依 fixed mode order 正規化五個 payload；解析 job context 取得 mode/question/snapshot。
2. 對每個結果計算 completeness（必要欄位與 evidence availability），記錄缺欄但不捏造。
3. 只在 active、非 abstain 配對中，比較 opposite direction，產生每對至多一筆 `DirectionDivergence`。
4. 對每個 angle 記錄完整度，僅在差距達可設定門檻時形成 `CompletenessGap`；此流程不讀 direction/confidence。
5. 對每個 angle pair 的規範化 source ID 集合計算交/聯集及 overlap；此流程不讀 direction/confidence。
6. 依所有 active 集合計算 independence；ratio 為零時設定 `independent_evidence_available=false`，並新增不可略過的 limit。
7. 依方向與有效 confidence 推導 consensus；組裝 template summary，禁止使用「獨立交叉佐證」除非 `ratio > 0` 且存在獨立 source IDs。

## 真實 payload 驗收設計

新增受版本控制的 production-payload manifest，只儲存 snapshot ID、可重建/讀取位置、payload digest、擷取時間和預期五個 mode，不提交敏感或可變 production 資料。整合測試透過既有 durable store/匯出機制讀取 `snap-btc-eca5b069d33ea8ac`，驗證 manifest digest 後才 synthesize。測試若找不到該 snapshot 必須明確 fail/skip 為「production acceptance unavailable」，不可退回 synthetic fixture 而視為通過。

## 不變量

- `len(direction_divergences) <= C(active_non_abstain_angles, 2)`。
- 一個 angle pair 的 sources 即使重疊十組，也只會產生一筆 `EvidenceOverlap`。
- `direction_divergences` 的數量與 `EvidenceOverlap` 的 shared source 數量沒有因果關係。
- `evidence_independence.ratio == 0` 時，所有下游 narrative facts 都帶 `independent_cross_validation=false`。
