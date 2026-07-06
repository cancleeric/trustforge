import { lazy, Suspense, useEffect, useState } from 'react'
import { getHistory } from '../lib/endpoints'
import { computeTrendDelta } from '../lib/trustTrend'
import type { HistoryData } from '../lib/types'
import { ErrorState, LoadingState } from './StatusStates'

// recharts 體積大，比照 `HistoryPage.tsx`／`AnalysisReportView.tsx` 既有
// 慣例 code-split 成獨立 chunk。
const TrustHistoryChart = lazy(() => import('./TrustHistoryChart'))

const TREND_DAYS = 14

/** #89：AnalysisReportView 嵌入的「信任趨勢」區塊——複用 `HistoryPage.tsx`
 * 已上線的資料來源（`/api/history`）、優雅降級文案與顏色邏輯，只是換個
 * 宿主（單幣分析報告內嵌一小塊，而非獨立整頁）。不重造 `TrustHistoryChart`
 * ，也不動它內部（tooltip 措辭由 #90 另案處理）。
 *
 * **#62 護欄條款**：這裡的網路請求只打 `/api/history` 一支，「今日對比
 * 昨日」變化量交給 `computeTrendDelta()`（純函式，見 `lib/trustTrend.ts`）
 * 純吃這支 API 回傳的序列——不讀 `data.report`（`/api/analyze` 的即時值）
 * 也不讀 `/api/overview`，維持「單畫面單 key」不變量。
 */
export default function TrustTrendSection({ coin }: { coin: string }) {
  const [data, setData] = useState<HistoryData | null>(null)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    getHistory({ coin, days: TREND_DAYS }, controller.signal).then((res) => {
      if (controller.signal.aborted) return
      setLoading(false)
      if (res.ok) {
        setData(res.data)
        setError(null)
      } else {
        setData(null)
        setError(res.error)
      }
    })
    return () => {
      controller.abort()
    }
  }, [coin])

  const trend = data ? computeTrendDelta(data.history) : null

  return (
    <div className="rounded-lg border border-tf-border bg-tf-card p-4">
      <h3 className="mb-2 text-sm font-semibold text-tf-text">信任趨勢</h3>

      {loading && <LoadingState label="信任趨勢載入中…" />}
      {!loading && error && <ErrorState code={error.code} message={error.message} />}

      {!loading && !error && data && (
        <div className="flex flex-col gap-3">
          {trend?.kind === 'delta' && (
            <p className="tf-num text-sm text-tf-text">
              {coin} 信任分：{trend.latest.trust_score.toFixed(2)}{' '}
              <span
                className={
                  trend.delta > 0 ? 'text-tf-good' : trend.delta < 0 ? 'text-tf-bad' : 'text-tf-muted'
                }
              >
                {trend.delta > 0 ? '↑' : trend.delta < 0 ? '↓' : '→'}{' '}
                {trend.delta > 0 ? '+' : ''}
                {trend.delta.toFixed(2)}
              </span>{' '}
              <span className="text-tf-muted">（較昨日）</span>
            </p>
          )}
          {trend?.kind === 'no-baseline' && <p className="text-sm text-tf-muted">尚無對比基準</p>}

          {data.history.length === 0 && (
            <div className="rounded-lg border border-tf-border bg-tf-bg p-4 text-center text-sm text-tf-muted">
              {coin} 歷史累積中——排程尚未寫入任何按日快照，稍後再回來看看。
            </div>
          )}
          {data.history.length > 0 && data.history.length < 3 && (
            <div
              className="rounded-lg border border-tf-warn bg-[color-mix(in_srgb,var(--color-tf-warn)_8%,transparent)] p-3 text-xs text-tf-warn"
              role="status"
            >
              目前僅累積 {data.history.length} 筆資料點，趨勢線尚不具代表性，持續累積中。
            </div>
          )}
          {data.history.length > 0 && (
            <Suspense fallback={<LoadingState label="趨勢圖載入中…" />}>
              <TrustHistoryChart history={data.history} />
            </Suspense>
          )}
        </div>
      )}
    </div>
  )
}
