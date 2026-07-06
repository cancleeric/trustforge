import type { TrustHistoryEntry } from './types'

export type TrendDelta =
  | { kind: 'no-baseline' }
  | { kind: 'delta'; latest: TrustHistoryEntry; previous: TrustHistoryEntry; delta: number }

/** #89：分析結果頁「今日對比昨日」變化量——純函式、不依賴 React，方便
 * vitest 直接測（沿用 `manipRisk.ts`/`sortCoins.ts` 既有慣例：業務邏輯抽
 * 出元件外）。
 *
 * **#62 護欄條款**（issue #62 CEO 拍板關閉留言）：這裡只吃 `/api/history`
 * 回傳的序列（單一來源），不得混讀 `/api/analyze`／`/api/overview` 的
 * 即時 `latest` key，維持「單畫面單 key」不變量——呼叫端（`AnalysisReportView`
 * 的信任趨勢區塊）只能把 `getHistory()` 的結果餵進來，不可額外傳入
 * `data.report` 之類的即時分析值。
 *
 * `history` 依既有 `get_trust_history()` 保證由舊到新排序（見
 * `src/trustforge/ingestion/cache.py::get_trust_history`），故「今日」
 * 「昨日」就是陣列最後兩筆——不是行事曆日期減一，缺漏的日子本來就被
 * 上游跳過、不補假值（#24 鐵律），這裡沿用同一個「有資料就比，沒有就
 * 老實說沒有」原則。
 *
 * 少於 2 筆（0 或 1 筆）時明確回報「尚無對比基準」，不得裝作變化量是 0
 * （見 issue #89 驗收標準）。
 */
export function computeTrendDelta(history: TrustHistoryEntry[]): TrendDelta {
  if (history.length < 2) return { kind: 'no-baseline' }
  const latest = history[history.length - 1]
  const previous = history[history.length - 2]
  return { kind: 'delta', latest, previous, delta: latest.trust_score - previous.trust_score }
}
