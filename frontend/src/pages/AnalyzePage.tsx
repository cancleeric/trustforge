import { lazy, Suspense, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAnalyze } from '../lib/endpoints'
import type { AnalyzeParams } from '../lib/endpoints'
import type { AnalyzeData } from '../lib/types'
import { COIN_POOL } from '../lib/constants'
import QueryConsole, { type QueryValues } from '../components/QueryConsole'
import ConfidenceGauge from '../components/ConfidenceGauge'
import TrustBreakdown from '../components/TrustBreakdown'
import FactsInferenceLadder from '../components/FactsInferenceLadder'
import KeyBasisList from '../components/KeyBasisList'
import CrossSourceSignalPanel from '../components/CrossSourceSignalPanel'
import EvidenceTable from '../components/EvidenceTable'
import PriceProvenancePanel from '../components/PriceProvenancePanel'
import { DirectionBadge } from '../components/Badges'
import { ErrorState, LoadingState } from '../components/StatusStates'

// recharts（含 d3 相依）體積大，code-split 成獨立 chunk，不拖慢首屏/其餘頁面
// 的初始 JS 下載（credit-safe build 不受影響，純前端載入效能考量）。
const TrustRadarChart = lazy(() => import('../components/TrustRadarChart'))

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
        {!loading && !error && data && (
          <>
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-tf-border bg-tf-card p-4">
              <h1 className="text-xl font-bold text-tf-text">{data.report.coin}</h1>
              <DirectionBadge direction={data.report.direction} />
              <span className="text-xs text-tf-muted">生成於 {data.report.generated_at}</span>
              <span className="text-xs text-tf-muted">版本 {data.version}</span>
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[220px_1fr]">
              <ConfidenceGauge
                calibratedConfidence={data.report.calibrated_confidence}
                rawConfidence={data.report.confidence}
                decisionState={data.report.decision_state}
              />
              <TrustBreakdown data={data.trust_components_aggregate} />
            </div>

            <Suspense fallback={<LoadingState label="雷達圖載入中…" />}>
              <TrustRadarChart radar={data.trust_radar} />
            </Suspense>

            <FactsInferenceLadder
              facts={data.report.facts}
              inferences={data.report.inferences}
              marketJudgment={data.report.market_judgment}
            />

            <KeyBasisList items={data.report.key_basis} />

            <CrossSourceSignalPanel signal={data.report.cross_source_signal} />

            <PriceProvenancePanel priceProvenance={data.price_provenance} evidence={data.evidence} />

            {data.report.limits.length > 0 && (
              <div className="rounded-lg border border-tf-border bg-tf-card p-4">
                <h3 className="mb-2 text-sm font-semibold text-tf-text">已知限制 / 資料不足</h3>
                <ul className="list-disc space-y-1 pl-5 text-sm text-tf-text2">
                  {data.report.limits.map((l, i) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              </div>
            )}

            {data.report.could_flip.length > 0 && (
              <div className="rounded-lg border border-tf-border bg-tf-card p-4">
                <h3 className="mb-2 text-sm font-semibold text-tf-text">可能推翻結論的條件</h3>
                <ul className="list-disc space-y-1 pl-5 text-sm text-tf-text2">
                  {data.report.could_flip.map((l, i) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              </div>
            )}

            {data.report.contrarian.length > 0 && (
              <div className="rounded-lg border border-tf-border bg-tf-card p-4">
                <h3 className="mb-2 text-sm font-semibold text-tf-text">反方 / 低信任證據（已標記，未納入主結論）</h3>
                <ul className="list-disc space-y-1 pl-5 text-sm text-tf-text2">
                  {data.report.contrarian.map((l, i) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              </div>
            )}

            <h3 className="text-sm font-semibold text-tf-text">證據清單</h3>
            <EvidenceTable evidence={data.evidence} />
          </>
        )}
      </section>
    </main>
  )
}
