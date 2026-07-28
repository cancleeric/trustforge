# Multi-angle Frontend 設計文件

## 元件結構

```
HermesDashboard.tsx
  └── MultiAngleSection (新區塊)
        ├── 觸發按鈕 "執行五角度綜合分析"
        ├── MultiAngleOverview.tsx (結果到達後顯示)
        │     ├── ConsensusHeader (共識 + 信心 + independence)
        │     ├── AngleTable / AngleCards
        │     │     └── ConflictBadge.tsx (有衝突時顯示)
        │     └── LimitsFooter (限制聲明)
        └── AngleDrilldown (展開時)
              └── AnalysisReportView (既有元件)
```

## TypeScript Interfaces

```typescript
// frontend/src/lib/multiAngleEndpoints.ts

interface AngleResult {
  angle: string
  qtype: string
  direction: string
  calibrated_confidence: number
  decision_state: string
  key_basis: string[]
  evidence_sources: string[]
  evidence_count: number
  market_judgment: string
  snapshot_id: string
  job_id: string | null
}

interface AngleConflict {
  angle_a: string
  angle_b: string
  conflict_type: string
  detail: Record<string, unknown>
  summary: string
}

interface MultiAngleReport {
  coin: string
  snapshot_id: string
  angles: AngleResult[]
  consensus: string
  consensus_confidence: number
  conflicts: AngleConflict[]
  agreement_matrix: Record<string, Record<string, string>>
  synthesis_summary: string
  evidence_independence: number
  limits: string[]
  generated_at: string
}

interface MultiAngleSubmitResponse {
  snapshot_id: string
  job_ids: Record<string, string | null>
  coin: string
}
```

## 視覺設計

### 總覽表格 (Desktop)

```
┌──────────────────────────────────────────────────────┐
│  BTC 五角度綜合分析          snapshot: snap-btc-... │
│  共識：偏多（加權信心 0.65）  獨立性：85%           │
├──────┬────────┬──────┬────────┬──────────────────────┤
│ 角度 │ 結論   │ 信心 │ 狀態   │ 分歧                 │
├──────┼────────┼──────┼────────┼──────────────────────┤
│ 風險 │ 偏空   │ 0.62 │ normal │ ⚠️ 與 sentiment 相反 │
│ 情緒 │ 偏多   │ 0.58 │ low_c  │ ⚠️ 與 risk 相反     │
│ 新聞 │ 中性   │ 0.41 │ low_c  │ —                    │
│ 基本 │ —      │ —    │abstain │ —                    │
│ 催化 │ 偏多   │ 0.66 │ normal │ —                    │
└──────┴────────┴──────┴────────┴──────────────────────┘
```

### Mobile Card

每角度一張卡：角度名 + direction badge + confidence bar + conflict pill

### 觸發按鈕

```
[⚡ 執行五角度綜合分析]
   消耗約 5× 分析預算
```

## i18n Keys

```
maTitle: '五角度綜合分析' / 'Multi-angle Analysis'
maConsensus: '共識' / 'Consensus'
maIndependence: '證據獨立性' / 'Evidence independence'
maConflict: '分歧' / 'Conflict'
maAngle: '角度' / 'Angle'
maDirection: '結論' / 'Direction'
maConfidence: '信心' / 'Confidence'
maState: '狀態' / 'State'
maSubmit: '執行五角度綜合分析' / 'Run multi-angle analysis'
maCostWarning: '消耗約 5× 分析預算' / 'Uses ~5× analysis budget'
maNoResult: '尚無五角度綜合分析結果' / 'No multi-angle result yet'
maPartialAbstain: '部分角度棄權' / 'Partial abstain'
maFullAbstain: '全部角度棄權' / 'Full abstain'
maDivergence: '角度間方向分歧' / 'Direction divergence'
```
