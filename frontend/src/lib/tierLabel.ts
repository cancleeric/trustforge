// #171（旗艦增量：離散分層評分 UI / Tier Label）：純函式分桶，不新增任何
// 分數欄位、不發明數字、不做信賴區間——連續百分比/數字照常保留，本函式只是
// 它的文字標籤。門檻**逐字對齊後端** `src/trustforge/schema.py::
// Report.confidence_label()`（見該檔 decision_state 欄位註解，W4 codex 對抗審
// [HIGH-1] 修法）：
//
//   decision_state == "abstain"        → 棄權／資料不足（tone: bad）
//   decision_state == "low_confidence" → 資訊完整度偏低（tone: warn）
//   normal（含 normalize 落 normal 的 legacy/未知值）：
//     吃 **calibrated_confidence**（不是裸 confidence），
//       c >= 0.7   → 高（good）
//       c >= 0.45  → 中（warn）
//       否則       → 低（bad）
//
// 三態優先於純數字分桶：abstain/low_confidence 直接回結構化狀態文字，不會因
// 校準值落在門檻附近被誤標成「中/高」，跟後端 market_judgment 措辭矛盾。
// 前端一律先 `normalizeDecisionState`（處理 legacy/未知 enum 落到 normal）再判斷，
// 不對原始字面值比對。顏色沿用專案既有 token（good/warn/bad →
// `var(--color-tf-*)`，跟 `decisionColor.ts` / `Badges.tsx` 同一套）。

import { normalizeDecisionState } from './types'

export type TierTone = 'good' | 'warn' | 'bad'

// ── 門檻常數（對齊 schema.py::Report.confidence_label()）──────────────────
const TIER_HIGH_THRESHOLD = 0.7 // schema.py: c >= 0.7 → 高
const TIER_MID_THRESHOLD = 0.45 // schema.py: c >= 0.45 → 中

/** tone → CSS var 顏色 token，對齊 `decisionColor.ts` / `Badges.tsx` 既有用法。 */
export const TONE_COLOR: Record<TierTone, string> = {
  good: 'var(--color-tf-good)',
  warn: 'var(--color-tf-warn)',
  bad: 'var(--color-tf-bad)',
}

export interface TierLabel {
  label: string
  tone: TierTone
}

/** 離散分層標籤純函式（不依賴 React，方便 vitest 純單元測試邊界值）。
 *
 * `heroValue` 是「要被分桶的連續值」——呼叫端對 normal 態**必須**傳入
 * `calibrated_confidence`（後端對齊語意，絕非裸 confidence）；對 abstain/
 * low_confidence 態本函式不會用到它（直接回結構化狀態），呼叫端傳何值都不影響。 */
export function tierLabel(decisionState: string, heroValue: number): TierLabel {
  const state = normalizeDecisionState(decisionState)
  // 三態優先：結構化狀態直接標示，明確不是「等級」。
  if (state === 'abstain') return { label: '棄權／資料不足', tone: 'bad' }
  if (state === 'low_confidence') return { label: '資訊完整度偏低', tone: 'warn' }
  // normal（含 normalize 落 normal 的 legacy/未知值）：依校準後值（calibrated_confidence）分桶。
  if (heroValue >= TIER_HIGH_THRESHOLD) return { label: '高', tone: 'good' }
  if (heroValue >= TIER_MID_THRESHOLD) return { label: '中', tone: 'warn' }
  return { label: '低', tone: 'bad' }
}
