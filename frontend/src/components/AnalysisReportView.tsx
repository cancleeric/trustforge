import { lazy, Suspense } from 'react'
import type { AnalyzeData } from '../lib/types'
import ConfidenceGauge from './ConfidenceGauge'
import TrustBreakdown from './TrustBreakdown'
import FactsInferenceLadder from './FactsInferenceLadder'
import KeyBasisList from './KeyBasisList'
import CrossSourceSignalPanel from './CrossSourceSignalPanel'
import InsightExplainabilityPanel from './InsightExplainabilityPanel'
import EvidenceTable from './EvidenceTable'
import PriceProvenancePanel from './PriceProvenancePanel'
import TrustTrendSection from './TrustTrendSection'
import { DirectionBadge } from './Badges'
import { LoadingState } from './StatusStates'

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
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-tf-border bg-tf-card p-4">
        {heading && (
          <span className="rounded-full border border-tf-accent px-2 py-0.5 text-xs font-semibold text-tf-link">
            {heading}
          </span>
        )}
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

      <TrustTrendSection coin={data.report.coin} />

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

      <InsightExplainabilityPanel insights={data.report.insights} />

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
    </div>
  )
}
