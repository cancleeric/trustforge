// #2 修復（PR #103 Round 2，low_confidence 顏色語意分裂）：state-aware
// 配色純函式，抽成獨立、不依賴 React 的模組，方便 vitest 純單元測試邊界值
// （本專案 frontend 測試慣例只測 lib/ 純函式，元件本身不另外裝 RTL/
// jsdom，同 `manipRisk.ts`）。`ConfidenceGauge.tsx` 引用這裡的實作，
// 不在元件檔內另外定義，避免同時匯出元件與純函式觸發
// `react(only-export-components)` fast-refresh lint 警告。
//
// 跟 SSR `web.py::_decision_color` 同一套規則：abstain 一律紅、
// low_confidence 一律琥珀（不論數值），normal（或已由呼叫端正規化的
// 缺失/未知值）才按數值門檻分桶——改前 React 已如此，但 SSR 只按數值
// 門檻分色、完全不看 `decision_state`，導致同一份報告在 React 顯琥珀、
// SSR 卻顯紅（0.40 邊界值）。

import type { DecisionState } from './types'

/** 對應後端 `Report.confidence_label()`：三態優先於純數字分桶。
 *  回傳 CSS var 引用（而非寫死 hex），確保 light/dark 切主題時 gauge
 *  顏色跟著 `--color-tf-*` token 一起變，不會卡在 dark 色。 */
export function bucketColor(decisionState: DecisionState, heroValue: number): string {
  if (decisionState === 'abstain') return 'var(--color-tf-bad)'
  if (decisionState === 'low_confidence') return 'var(--color-tf-warn)'
  if (heroValue >= 0.7) return 'var(--color-tf-good)'
  if (heroValue >= 0.45) return 'var(--color-tf-warn)'
  return 'var(--color-tf-bad)'
}
