import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAnalyze } from '../lib/endpoints'
import type { AnalyzeParams } from '../lib/endpoints'
import type { AnalyzeData } from '../lib/types'
import { COIN_POOL } from '../lib/constants'
import QueryConsole, { type QueryValues } from '../components/QueryConsole'
import AnalysisReportView from '../components/AnalysisReportView'
import { ErrorState, LoadingState } from '../components/StatusStates'

function paramsFromSearch(sp: URLSearchParams): AnalyzeParams {
  const coin = sp.get('coin') || COIN_POOL[0]
  const type = (sp.get('type') as AnalyzeParams['type']) || 'multi_source'
  const q = sp.get('q') || `分析${coin}近期市場狀況，整合多源資料`
  const sample = sp.get('sample') === '1' ? '1' : undefined
  return { coin, type, q, sample }
}

export default function AnalyzePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const params = paramsFromSearch(searchParams)

  const [data, setData] = useState<AnalyzeData | null>(null)
  const [error, setError] = useState<{ code: string; message: string } | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // 真的 abort 底層 fetch（不只是忽略回應）：參數變更/卸載時中止「已被
    // 取代」的舊請求，避免它晚到覆蓋新狀態（race），也省下無謂的等待。
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    getAnalyze(params, controller.signal).then((res) => {
      // 已被取消（cleanup）——這是被取代的舊請求，靜默捨棄，不當錯誤
      // UI、不覆蓋新狀態。逾時（backend stall）不會走到這裡，會正常落到
      // 下面的 res.ok===false 分支顯示錯誤狀態。
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.coin, params.type, params.q, params.sample])

  const handleSubmit = (values: QueryValues) => {
    const next: Record<string, string> = { coin: values.coin, type: values.type, q: values.q }
    if (params.sample) next.sample = params.sample
    setSearchParams(next)
  }

  return (
    <main className="mx-auto grid max-w-6xl grid-cols-1 gap-4 px-4 py-6 sm:px-6 lg:grid-cols-[280px_1fr]">
      <aside>
        <QueryConsole initial={{ coin: params.coin, type: params.type, q: params.q }} onSubmit={handleSubmit} />
      </aside>

      <section className="flex flex-col gap-4">
        {loading && <LoadingState label={`分析 ${params.coin} 中…`} />}
        {!loading && error && <ErrorState code={error.code} message={error.message} />}
        {!loading && !error && data && <AnalysisReportView data={data} />}
      </section>
    </main>
  )
}
