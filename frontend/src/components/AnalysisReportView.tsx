import { lazy, Suspense, useState, type KeyboardEvent } from 'react'
import type { AnalyzeData } from '../lib/types'
import ConfidenceGauge from './ConfidenceGauge'
import TrustBreakdown from './TrustBreakdown'
import FactsInferenceLadder from './FactsInferenceLadder'
import KeyBasisList from './KeyBasisList'
import InsightExplainabilityPanel from './InsightExplainabilityPanel'
import HypothesisLedgerPanel from './HypothesisLedgerPanel'
import EvidenceTable from './EvidenceTable'
import EvidenceTrailPanel from './EvidenceTrailPanel'
import PriceProvenancePanel from './PriceProvenancePanel'
import TrustTrendSection from './TrustTrendSection'
import { DirectionBadge } from './Badges'
import { LoadingState } from './StatusStates'
import HermesExecutionPanel from './HermesExecutionPanel'
import ReportDownloads from './ReportDownloads'
import { formatTimestamp } from '../lib/format'
import PlainLanguageResultSummary from './PlainLanguageResultSummary'
import GlossaryTerm from './GlossaryTerm'
import AnnotatedText from './AnnotatedText'
import { useHermesI18n } from '../hermes/hermesI18n'
import AssetIntrinsicShadowPanel from './AssetIntrinsicShadowPanel'
import ProConPanel from './ProConPanel'

// recharts（含 d3 相依）體積大，code-split 成獨立 chunk，不拖慢首屏/其餘頁面
// 的初始 JS 下載（credit-safe build 不受影響，純前端載入效能考量）。
const TrustRadarChart = lazy(() => import('./TrustRadarChart'))
const EvidenceDistributionCharts = lazy(() => import('./EvidenceDistributionCharts'))

type DeepDiveTab = 'trust' | 'reasoning' | 'risk'

function relativeMinutes(timestamp: string, copy: { unknown: string; justNow: string; minutesAgo: (minutes: number) => string }): string {
  const value = Date.parse(timestamp)
  if (!Number.isFinite(value)) return copy.unknown
  const minutes = Math.max(0, Math.floor((Date.now() - value) / 60_000))
  if (minutes < 1) return copy.justNow
  return copy.minutesAgo(minutes)
}

function evidenceKindCounts(data: AnalyzeData): Array<[string, number]> {
  const counts = new Map<string, number>()
  for (const item of data.evidence) {
    const kind = item.kind.trim() || 'unknown'
    counts.set(kind, (counts.get(kind) ?? 0) + 1)
  }
  return [...counts.entries()].sort(([a], [b]) => a.localeCompare(b))
}

/** 單份分析報告的完整渲染區塊——`AnalyzePage`（單幣）與 `ComparePage`
 * （雙幣並列，各自渲染一份 `report_a`/`report_b`）共用同一顆元件，兩邊
 * 讀到的資料形狀完全相同（皆為 `AnalyzeData`），避免同一份渲染邏輯分岔
 * 維護兩份。`heading` 可選——比較頁需要在標題列多加一個幣種角色標籤
 * （「幣種 A」/「幣種 B」），單幣頁不需要。`mode` 亦可選——來自請求參數
 * （`AnalyzeParams['type']`），非 `AnalyzeData` 回應本身的欄位，單純用來
 * 在標題列顯示本次分析用的模式（呼應設計稿 R2 mode: multi_source 標籤）。 */
export default function AnalysisReportView({ data, heading, mode, compact }: { data: AnalyzeData; heading?: string; mode?: string; compact?: boolean }) {
  const { t } = useHermesI18n()
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [deepDiveTab, setDeepDiveTab] = useState<DeepDiveTab>('trust')
  // N71（CEO：「手動分析的報告要在哪裡下載？執行過程的 LOG 要在哪裡看」）：
  // 三顆下載鈕本來只在最底下那個預設收合的「技術細節」裡，跑完分析根本找不到。
  // 這裡在報告抬頭補一排永遠看得見的動作，外加一顆帶去執行過程面板的按鈕
  // （順手把 `<details>` 展開——不展開的話捲過去只會看到收合的標題列）。
  const openExecution = () => {
    const details = document.getElementById('evidence-list') as HTMLDetailsElement | null
    if (!details) return
    details.open = true
    details.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  const execution = data.execution ?? {
    agent: 'hermes', run_id: 'legacy-run', started_at: data.report.generated_at,
    elapsed_sec: 0, budget_sec: 900, nodes: [],
  }
  const kindCounts = evidenceKindCounts(data)
  const generatedAgo = relativeMinutes(data.report.generated_at, {
    unknown: t('arvTimeUnknown'),
    justNow: t('arvTimeJustNow'),
    minutesAgo: (minutes) => t('arvMinutesAgo', { minutes }),
  })
  const deepDiveTabs: Array<[DeepDiveTab, string]> = [
    ['trust', t('arvTrustTab')],
    ['reasoning', t('arvReasoningTab')],
    ['risk', t('arvRiskTab')],
  ]
  const selectDeepDiveTab = (event: KeyboardEvent<HTMLButtonElement>, current: DeepDiveTab) => {
    const currentIndex = deepDiveTabs.findIndex(([id]) => id === current)
    let targetIndex = currentIndex
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') targetIndex = (currentIndex + 1) % deepDiveTabs.length
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') targetIndex = (currentIndex - 1 + deepDiveTabs.length) % deepDiveTabs.length
    else if (event.key === 'Home') targetIndex = 0
    else if (event.key === 'End') targetIndex = deepDiveTabs.length - 1
    else return
    event.preventDefault()
    const target = deepDiveTabs[targetIndex][0]
    setDeepDiveTab(target)
    document.getElementById(`deep-dive-tab-${target}`)?.focus()
  }
  return (
    <div className="flex flex-col gap-4">
      <div className="border-b border-tf-border pb-4">
        <div className="flex flex-wrap items-center gap-3">
          {heading && (
            <span className="rounded-full border border-tf-accent px-2 py-0.5 text-xs font-semibold text-tf-link">
              {heading}
            </span>
          )}
          <h2 className={`${compact ? 'text-base' : 'text-xl'} font-bold text-tf-text`}>{data.report.coin}</h2>
          <span className="font-mono text-xs text-tf-muted">run {data.execution?.run_id ?? 'legacy-run'}</span>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-tf-muted">
          <span title={`${t('arvGeneratedAtPrefix')}${formatTimestamp(data.report.generated_at)}`}>
            {t('arvUpdatedAt', { time: generatedAgo })}
          </span>
          <span>{data.evidence.length}{t('arvEvidenceCountSuffix')}</span>
          <span>{t('arvVersionPrefix')}{data.version}</span>
          {mode === 'multi_source' && (
            <span>mode: <GlossaryTerm term="multiSource" label="multi_source" compact /></span>
          )}
          <a href="/help" className="ml-auto text-tf-link no-underline hover:underline">
            {t('arvViewFullHelp')}
          </a>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2" aria-label={t('arvReportActions')}>
          <ReportDownloads
            execution={execution}
            events={data.execution_log}
            report={data.report}
            evidence={data.evidence}
            onOpenExecution={openExecution}
          />
        </div>
      </div>

      <section aria-labelledby="executive-summary-title" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-tf-link">L1 · Executive Summary</p>
          <DirectionBadge direction={data.report.direction} />
          <span className="ml-auto text-xs text-tf-muted">{t('arvEvidenceCount', { count: data.evidence.length })}</span>
          {kindCounts.map(([kind, count]) => (
            <span key={kind} className="rounded-full border border-tf-border px-2 py-0.5 text-xs text-tf-muted">{kind} {count}</span>
          ))}
        </div>
        <h3 id="executive-summary-title" className="sr-only">Executive Summary</h3>
        <div className={`grid grid-cols-1 gap-4 ${compact ? '' : 'xl:grid-cols-[220px_minmax(0,1fr)]'}`}>
          <ConfidenceGauge
            calibratedConfidence={data.report.calibrated_confidence}
            rawConfidence={data.report.confidence}
            decisionState={data.report.decision_state}
          />
          <PlainLanguageResultSummary data={data} />
        </div>
      </section>

      <ProConPanel
        facts={data.report.facts}
        contrarian={data.report.contrarian}
        evidence={data.evidence}
        signal={data.report.cross_source_signal}
        insights={data.report.insights}
        compact={compact}
      />

      <details id="technical-analysis" className="hermes-technical-details hermes-clip border border-tf-border bg-tf-card">
        <summary>{t('arvDeepDiveSummary')} <span>{t('arvDeepDiveHint')}</span></summary>
        <div className="flex flex-col gap-4 p-4">
          <div role="tablist" aria-label={t('arvDeepDiveTabs')} className="flex flex-wrap gap-2 border-b border-tf-border pb-2">
            {deepDiveTabs.map(([id, label]) => (
              <button
                key={id}
                id={`deep-dive-tab-${id}`}
                type="button"
                role="tab"
                aria-selected={deepDiveTab === id}
                aria-controls={`deep-dive-panel-${id}`}
                tabIndex={deepDiveTab === id ? 0 : -1}
                className={`rounded-md px-3 py-1.5 text-sm ${deepDiveTab === id ? 'bg-tf-accent text-white' : 'border border-tf-border text-tf-text2'}`}
                onClick={() => setDeepDiveTab(id)}
                onKeyDown={(event) => selectDeepDiveTab(event, id)}
              >
                {label}
              </button>
            ))}
          </div>
          {deepDiveTab === 'trust' && (
            <div id="deep-dive-panel-trust" role="tabpanel" aria-labelledby="deep-dive-tab-trust" className="flex flex-col gap-4">
              <TrustBreakdown data={data.trust_components_aggregate} />
              <Suspense fallback={<LoadingState label={t('arvRadarLoading')} />}>
                <TrustRadarChart radar={data.trust_radar} />
              </Suspense>
              <InsightExplainabilityPanel insights={data.report.insights} />
            </div>
          )}
          {deepDiveTab === 'reasoning' && (
            <div id="deep-dive-panel-reasoning" role="tabpanel" aria-labelledby="deep-dive-tab-reasoning" className="flex flex-col gap-4">
              <FactsInferenceLadder facts={data.report.facts} inferences={data.report.inferences} marketJudgment={data.report.market_judgment} />
              <div id="key-basis"><KeyBasisList items={data.report.key_basis} /></div>
              <HypothesisLedgerPanel ledger={data.report.hypothesis_ledger} evidence={data.evidence} />
            </div>
          )}
          {deepDiveTab === 'risk' && (
            <div id="deep-dive-panel-risk" role="tabpanel" aria-labelledby="deep-dive-tab-risk" className="flex flex-col gap-4">
              <AssetIntrinsicShadowPanel value={data.report.asset_intrinsic_assessment} />
              <TrustTrendSection coin={data.report.coin} />
              {data.report.limits.length > 0 && (
                <div id="known-limits" className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
                  <h3 className="mb-2 text-sm font-semibold text-tf-text">{t('arvKnownLimits')}</h3>
                  <ul className="list-disc space-y-1 pl-5 text-sm text-tf-text2">
                    {data.report.limits.map((item, index) => <li key={index}><AnnotatedText text={item} /></li>)}
                  </ul>
                </div>
              )}
              {data.report.could_flip.length > 0 && (
                <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
                  <h3 className="mb-2 text-sm font-semibold text-tf-text">{t('arvCouldFlip')}</h3>
                  <ul className="list-disc space-y-1 pl-5 text-sm text-tf-text2">
                    {data.report.could_flip.map((item, index) => <li key={index}><AnnotatedText text={item} /></li>)}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      </details>

      <details id="evidence-list" className="trustforge-collapse hermes-clip rounded-lg border border-tf-border bg-tf-card" onToggle={(event) => setEvidenceOpen(event.currentTarget.open)}>
        <summary>L4 · {t('arvEvidenceList')}（{data.evidence.length}）</summary>
        <div className="flex flex-col gap-4 p-4">
          {evidenceOpen && <Suspense fallback={<LoadingState label={t('arvChartsLoading')} />}><EvidenceDistributionCharts evidence={data.evidence} /></Suspense>}
          <EvidenceTable evidence={data.evidence} evidenceGroups={data.report.evidence_groups} />
          <EvidenceTrailPanel evidence={data.evidence} signal={data.report.cross_source_signal} />
          <PriceProvenancePanel priceProvenance={data.price_provenance} evidence={data.evidence} />
          <HermesExecutionPanel execution={data.execution} events={data.execution_log} report={data.report} evidence={data.evidence} />
        </div>
      </details>
    </div>
  )
}
