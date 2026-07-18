import { lazy, Suspense } from 'react'
import type { AnalyzeData } from '../lib/types'
import ConfidenceGauge from './ConfidenceGauge'
import TrustBreakdown from './TrustBreakdown'
import FactsInferenceLadder from './FactsInferenceLadder'
import KeyBasisList from './KeyBasisList'
import CrossSourceSignalPanel from './CrossSourceSignalPanel'
import InsightExplainabilityPanel from './InsightExplainabilityPanel'
import HypothesisLedgerPanel from './HypothesisLedgerPanel'
import EvidenceTable from './EvidenceTable'
import EvidenceTrailPanel from './EvidenceTrailPanel'
import PriceProvenancePanel from './PriceProvenancePanel'
import TrustTrendSection from './TrustTrendSection'
import { DirectionBadge } from './Badges'
import { LoadingState } from './StatusStates'
import HermesExecutionPanel from './HermesExecutionPanel'
import { formatTimestamp } from '../lib/format'
import PlainLanguageResultSummary from './PlainLanguageResultSummary'

// recharts（含 d3 相依）體積大，code-split 成獨立 chunk，不拖慢首屏/其餘頁面
// 的初始 JS 下載（credit-safe build 不受影響，純前端載入效能考量）。
const TrustRadarChart = lazy(() => import('./TrustRadarChart'))

/** 單份分析報告的完整渲染區塊——`AnalyzePage`（單幣）與 `ComparePage`
 * （雙幣並列，各自渲染一份 `report_a`/`report_b`）共用同一顆元件，兩邊
 * 讀到的資料形狀完全相同（皆為 `AnalyzeData`），避免同一份渲染邏輯分岔
 * 維護兩份。`heading` 可選——比較頁需要在標題列多加一個幣種角色標籤
 * （「幣種 A」/「幣種 B」），單幣頁不需要。 */
export default function AnalysisReportView({ data, heading }: { data: AnalyzeData; heading?: string }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="border-b border-tf-border pb-4">
        <div className="flex flex-wrap items-center gap-3">
          {heading && (
            <span className="rounded-full border border-tf-accent px-2 py-0.5 text-xs font-semibold text-tf-link">
              {heading}
            </span>
          )}
          <h2 className="text-xl font-bold text-tf-text">{data.report.coin}</h2>
          <DirectionBadge direction={data.report.direction} />
          <span className="font-mono text-xs text-tf-muted">run {data.execution?.run_id ?? 'legacy-run'}</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-tf-muted">
          <span title={data.report.generated_at}>生成於 {formatTimestamp(data.report.generated_at)}</span>
          <span>{data.evidence.length} 筆可追溯證據</span>
          <span>版本 {data.version}</span>
        </div>
      </div>

      <PlainLanguageResultSummary data={data} />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[220px_minmax(0,1fr)]">
        <ConfidenceGauge
          calibratedConfidence={data.report.calibrated_confidence}
          rawConfidence={data.report.confidence}
          decisionState={data.report.decision_state}
        />
        <section className="hermes-clip border-l-2 border-tf-accent bg-tf-card p-4" aria-label="市場結論">
          <p className="text-xs font-semibold uppercase tracking-wide text-tf-link">市場結論</p>
          <p className="mt-2 text-base font-semibold leading-7 text-tf-text">{data.report.market_judgment}</p>
          <div className="mt-4 grid grid-cols-3 gap-2 border-t border-tf-border pt-3 text-xs">
            <div><p className="text-tf-muted">事實</p><p className="tf-num mt-1 font-semibold text-tf-text">{data.report.facts.length}</p></div>
            <div><p className="text-tf-muted">推論</p><p className="tf-num mt-1 font-semibold text-tf-text">{data.report.inferences.length}</p></div>
            <div><p className="text-tf-muted">反方訊號</p><p className="tf-num mt-1 font-semibold text-tf-text">{data.report.contrarian.length}</p></div>
          </div>
        </section>
      </div>

      <details id="technical-analysis" className="hermes-technical-details hermes-clip border border-tf-border bg-tf-card">
        <summary>查看完整技術分析 <span>執行管線、信任拆解、趨勢與雷達圖</span></summary>
        <div className="flex flex-col gap-4 p-4">
          <HermesExecutionPanel execution={data.execution} events={data.execution_log} report={data.report} evidence={data.evidence} />
          <TrustBreakdown data={data.trust_components_aggregate} />
          <TrustTrendSection coin={data.report.coin} />
          <Suspense fallback={<LoadingState label="雷達圖載入中…" />}>
            <TrustRadarChart radar={data.trust_radar} />
          </Suspense>
        </div>
      </details>

      <FactsInferenceLadder
        facts={data.report.facts}
        inferences={data.report.inferences}
        marketJudgment={data.report.market_judgment}
      />

      <div id="key-basis"><KeyBasisList items={data.report.key_basis} /></div>

      <CrossSourceSignalPanel signal={data.report.cross_source_signal} />

      <InsightExplainabilityPanel insights={data.report.insights} />

      <HypothesisLedgerPanel ledger={data.report.hypothesis_ledger} evidence={data.evidence} />

      <PriceProvenancePanel priceProvenance={data.price_provenance} evidence={data.evidence} />

      {data.report.limits.length > 0 && (
        <div id="known-limits" className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
          <h3 className="mb-2 text-sm font-semibold text-tf-text">已知限制 / 資料不足</h3>
          <ul className="list-disc space-y-1 pl-5 text-sm text-tf-text2">
            {data.report.limits.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </div>
      )}

      {data.report.could_flip.length > 0 && (
        <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
          <h3 className="mb-2 text-sm font-semibold text-tf-text">可能推翻結論的條件</h3>
          <ul className="list-disc space-y-1 pl-5 text-sm text-tf-text2">
            {data.report.could_flip.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </div>
      )}

      {data.report.contrarian.length > 0 && (
        <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
          <h3 className="mb-2 text-sm font-semibold text-tf-text">反方 / 低信任證據（已標記，未納入主結論）</h3>
          <ul className="list-disc space-y-1 pl-5 text-sm text-tf-text2">
            {data.report.contrarian.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </div>
      )}

      <EvidenceTrailPanel evidence={data.evidence} signal={data.report.cross_source_signal} />

      <div id="evidence-list">
        <h3 className="mb-3 text-sm font-semibold text-tf-text">證據清單</h3>
        <EvidenceTable evidence={data.evidence} />
      </div>
    </div>
  )
}
