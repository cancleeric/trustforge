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
 *
 * codex 複審 MEDIUM 修復：這裡只取陣列最後兩筆算 delta，**不驗證兩筆
 * 日期是否真的相鄰**（排程失敗/缺漏本來就是既有支援情境，見上方缺漏
 * 日子被跳過的說明）——`latest`/`previous` 之間可能實際上相隔數天，UI
 * 端不能因此就寫死「較昨日」誤導使用者做短期變化判讀。呼叫端改用
 * `trendComparisonLabel()`（見下方）依實際日期差決定文案，這裡本身不
 * 擋、不補值，維持「有資料就比」原則不變。
 */
export function computeTrendDelta(history: TrustHistoryEntry[]): TrendDelta {
  if (history.length < 2) return { kind: 'no-baseline' }
  const latest = history[history.length - 1]
  const previous = history[history.length - 2]
  return { kind: 'delta', latest, previous, delta: latest.trust_score - previous.trust_score }
}

/** `date` 皆為 `/api/history` 回傳的 `YYYY-MM-DD` 字串（UTC 日期，無時區
 * 位移疑慮），用 `Date.UTC` 換算天數差，避免本地時區/夏令時把差值算歪。 */
function daysBetween(earlierDate: string, laterDate: string): number {
  const [ey, em, ed] = earlierDate.split('-').map(Number)
  const [ly, lm, ld] = laterDate.split('-').map(Number)
  const earlier = Date.UTC(ey, em - 1, ed)
  const later = Date.UTC(ly, lm - 1, ld)
  return Math.round((later - earlier) / 86_400_000)
}

/** codex 複審 MEDIUM 修復（CEO 終批方案一）：`computeTrendDelta()` 的
 * `latest`/`previous` 不保證行事曆相鄰（排程失敗/缺漏可能讓兩筆快照隔
 * 好幾天），文案不能一律寫「較昨日」——真的相鄰（差 1 天）才顯示
 * 「較昨日」這個特例；其餘一律誠實標「較前次快照（YYYY-MM-DD）」，把
 * 對比基準日期攤開給使用者自己判斷，不裝作是連續每日比較。 */
export function trendComparisonLabel(previous: TrustHistoryEntry, latest: TrustHistoryEntry): string {
  const gapDays = daysBetween(previous.date, latest.date)
  if (gapDays === 1) return '較昨日'
  return `較前次快照（${previous.date}）`
}
