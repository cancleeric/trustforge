import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAnalyze } from '../lib/endpoints'
import type { AnalyzeParams } from '../lib/endpoints'
import type { AnalyzeData } from '../lib/types'
import { COIN_POOL } from '../lib/constants'
import QueryConsole, { type QueryValues } from '../components/QueryConsole'
import AnalysisReportView from '../components/AnalysisReportView'
import { ErrorState, LoadingState } from '../components/StatusStates'

function defaultQuery(coin: string): string {
  return `分析${coin}近期市場狀況，整合多源資料`
}

function paramsFromSearch(sp: URLSearchParams): AnalyzeParams {
  const coin = sp.get('coin') || COIN_POOL[0]
  const type = (sp.get('type') as AnalyzeParams['type']) || 'multi_source'
  const q = sp.get('q') || defaultQuery(coin)
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
    <main
      className="relative mx-auto max-w-7xl px-4 py-6 sm:px-6"
      style={{ background: 'radial-gradient(ellipse at 50% 0%,#0b1420 0%,#050810 70%)', minHeight: 'calc(100vh - 57px)' }}
    >
      <div
        className="pointer-events-none absolute inset-0 z-0"
        style={{ background: 'repeating-linear-gradient(rgba(255,255,255,.012) 0px,rgba(255,255,255,.012) 1px,transparent 1px,transparent 3px)' }}
      />
      <div className="relative z-10">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3 border-b border-tf-border pb-4">
        <div>
          <p className="font-mono text-xs font-semibold uppercase tracking-[1.6px] text-tf-link">Hermes analysis run</p>
          <h1 className="mt-1 text-2xl font-bold text-tf-text">分析工作區 <span className="text-tf-link">· BRIDGE</span></h1>
          <p className="mt-1 text-sm text-tf-text2">每次執行固定一個 run，保留來源、節點、證據與輸出供後續稽核。</p>
        </div>
        <p className="font-mono text-xs text-tf-muted">asset: {params.coin} · mode: {params.type}</p>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[288px_minmax(0,1fr)]">
      <aside className="lg:sticky lg:top-4 lg:self-start">
        <QueryConsole initial={{ coin: params.coin, type: params.type, q: params.q }} onSubmit={handleSubmit} />
      </aside>

      <section className="flex flex-col gap-4">
        {loading && <LoadingState label={`分析 ${params.coin} 中…`} />}
        {!loading && error && <ErrorState code={error.code} message={error.message} />}
        {!loading && !error && data && <AnalysisReportView data={data} />}
      </section>
      </div>
      </div>
    </main>
  )
}
