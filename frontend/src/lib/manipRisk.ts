// #86：跨幣信任×操縱風險排行——操縱風險徽章的純分級邏輯，抽成獨立、
// 不依賴 React 的模組，方便 vitest 純單元測試（本專案 frontend 測試慣例
// 只測 lib/ 純函式，元件本身不另外裝 RTL/jsdom）。

export type ManipRiskTier = 'high' | 'medium' | 'low' | 'unscored'

export interface ManipRiskDisplay {
  tier: ManipRiskTier
  label: string
  color: string
}

/** 沿用 issue #86 定案數字：≥0.3 高風險／≥0.1 中風險／其餘低風險。 */
export const MANIP_RISK_HIGH_THRESHOLD = 0.3
export const MANIP_RISK_MEDIUM_THRESHOLD = 0.1

const NEUTRAL_COLOR = 'var(--color-tf-muted)'
export const MANIP_RISK_UNSCORED_LABEL = '操縱風險未評分'

/**
 * codex 複審 HIGH 修復（風險 invariant 定案）：這裡吃的 `manipScore` 語意
 * 是後端 `_calc_manip_signal()` 算出的 **worst-case（max，any-hit）**，
 * 不是算術平均——平均會被 evidence 筆數稀釋（15 筆裡 1 筆已確認操縱
 * `manipulation=1.0`，平均只剩 0.067，會被門檻誤判低風險），只有
 * worst-case 能保證「只要出現一筆已確認操縱，就不可能顯示低風險」這個
 * 不變量，見 `scripts/fetch_scheduler.py::_calc_manip_signal()` docstring
 * 與 `manipRisk.test.ts::單筆確認操縱`。`manip_score_mean` 只當輔助資訊
 * （呼叫端另外顯示於 tooltip），不參與這裡的分級判斷。
 *
 * codex 複審 MEDIUM 修復（缺分數不可悄悄消失）：`manipScore` 為
 * `undefined`（本輪無 evidence、或舊格式快照本欄位新增前寫入）時回傳
 * 明確的 `unscored` 中性態，顏色用中性灰（跟 `low` 的綠色分開，不會被
 * 誤讀成「已評估、風險低」）——「沒評分」跟「評分後風險低」在 UI 上必須
 * 可區分。
 */
export function manipRiskDisplay(manipScore: number | undefined): ManipRiskDisplay {
  if (manipScore === undefined) {
    return { tier: 'unscored', label: MANIP_RISK_UNSCORED_LABEL, color: NEUTRAL_COLOR }
  }
  if (manipScore >= MANIP_RISK_HIGH_THRESHOLD) {
    return { tier: 'high', label: '⚠ 高操縱風險', color: 'var(--color-tf-bad)' }
  }
  if (manipScore >= MANIP_RISK_MEDIUM_THRESHOLD) {
    return { tier: 'medium', label: '⚡ 中操縱風險', color: 'var(--color-tf-warn)' }
  }
  return { tier: 'low', label: '✓ 低操縱風險', color: 'var(--color-tf-good)' }
}
