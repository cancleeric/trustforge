import { HERMES_CYAN, HERMES_AMBER, HERMES_RED, STAGE_DEFS, type GalaxyCoin, type SelectedDerivation } from '../lib/hermesData'
import { modeLabel, useHermesI18n } from './hermesI18n'
import type { HermesWorkspaceModule } from './HermesModuleDeck'
import type { BridgeHologramData } from '../components/BridgeHologramContext'
import type { AnalysisFlowData } from '../lib/endpoints'
import { moduleStageLabels } from './stagePresentation'

interface StageBarProps {
  selCoin: GalaxyCoin
  derivation: SelectedDerivation
  selectedStage: string | null
  onSelectStage: (id: string) => void
  mode?: HermesWorkspaceModule | null
  telemetry?: BridgeHologramData | null
  activity?: { status: 'ready' | 'loading'; coin: string; mode: string; question: string }
  flow?: AnalysisFlowData | null
}

export default function StageBar({ selCoin, derivation, selectedStage, onSelectStage, mode = null, telemetry = null, activity, flow }: StageBarProps) {
  const { locale, t } = useHermesI18n()
  const moduleLabels = moduleStageLabels(locale)
  const hasTypedDetails = mode === null
  const liveFlow = !telemetry?.runId && flow?.stages.some((stage) => stage.current || stage.queued > 0)
  const engineStages = mode === 'analyze' && liveFlow ? flow?.stages.map((stage) => ({
    id: stage.id,
    label: moduleLabels.analyze[flow.stages.indexOf(stage)],
    metric: String(stage.current ? 1 : 0),
    unit: stage.next_retry_at
      ? `${t('queued')} ${stage.queued} · ${Math.max(0, Math.ceil(stage.next_retry_at - Date.now() / 1000))}s ${t('retryIn')}`
      : `${stage.current ? t('processing') : t('standby')} · ${t('queued')} ${stage.queued} · ${t('retryLabel')} ${stage.current?.retry_count ?? 0}`,
    status: stage.current ? 'pending' as const : 'completed' as const,
  })) : mode === 'analyze' ? telemetry?.pipelineStages : undefined
  const currentWork = telemetry?.runId ? undefined : flow?.stages.find((stage) => stage.current)?.current
  const stages = STAGE_DEFS.map((stage, index) => {
    const metric = derivation.stageMetrics[stage.id]
    const engine = engineStages?.[index]
    return {
      ...stage,
      color: engine ? engine.status === 'failed' ? HERMES_RED : engine.status === 'completed' ? HERMES_CYAN : HERMES_AMBER : mode ? HERMES_AMBER : index === 2 ? derivation.divColor : stage.id === 'manipulation' ? HERMES_AMBER : stage.id === 'composite' ? tierColor(selCoin) : HERMES_CYAN,
      metric: engine?.metric ?? (mode ? '--' : metric?.metric ?? '--'),
      unit: engine?.unit ?? (mode ? (locale === 'zh-TW' ? '等待執行' : 'PENDING') : stage.id === 'scan' ? t('scanned') : stage.id === 'filter' ? t('passed') : stage.id === 'crossverify' ? t('divergenceUnit') : stage.id === 'manipulation' ? t('flagged') : metric?.unit ?? ''),
      label: engine?.label ?? (mode ? moduleLabels[mode][index] : stage.id === 'scan' ? t('scan') : stage.id === 'filter' ? t('filter') : stage.id === 'crossverify' ? t('crossverify') : stage.id === 'manipulation' ? t('manipulation') : t('composite')),
    }
  })

  return (
    <section className="hermes-energy-deck" aria-label={locale === 'zh-TW' ? 'Hermes 能量管線' : 'Hermes energy pipeline'}>
      <div className="hermes-engine-activity" role="status" aria-live="polite">
        <i className={currentWork || activity?.status === 'loading' ? 'is-running' : 'is-complete'} />
        <b>{currentWork ? t('analyzingLabel') : telemetry?.runId ? t('snapshotLockedLabel') : t('continuousLabel')}</b>
        <span>{currentWork?.coin ?? activity?.coin ?? selCoin.name}</span>
        <span>{modeLabel(currentWork?.mode ?? telemetry?.analysisMode ?? activity?.mode ?? mode ?? 'multi_source', t)}</span>
        <strong title={currentWork?.question ?? telemetry?.question ?? activity?.question}>{currentWork?.question ?? telemetry?.question ?? activity?.question ?? '等待有效題目'}</strong>
        {currentWork?.snapshot_id && <small>snapshot {currentWork.snapshot_id}</small>}
        {telemetry?.snapshotAt && <small>snapshot {telemetry.snapshotAt}</small>}
        {telemetry?.runId && <small>run {telemetry.runId}</small>}
      </div>
      <div className="hermes-energy-conduit" aria-hidden="true">
        <i className="hermes-energy-packet packet-a" />
        <i className="hermes-energy-packet packet-b" />
        <i className="hermes-energy-packet packet-c" />
      </div>
      <div className="hermes-energy-nodes">
        {stages.map((stage, index) => {
          const selected = selectedStage === stage.id
          return (
            <button
              type="button"
              key={stage.id}
              aria-pressed={selected}
              disabled={!hasTypedDetails}
              aria-label={!hasTypedDetails
                ? `${stage.label} · ${locale === 'zh-TW' ? '階段資料尚未提供，無法開啟明細' : 'stage data unavailable; details cannot be opened'}`
                : undefined}
              title={!hasTypedDetails
                ? (locale === 'zh-TW' ? '此工作區尚未提供可驗證的階段資料與明細。' : 'This workspace does not yet provide verifiable stage data or details.')
                : undefined}
              onClick={() => onSelectStage(stage.id)}
              className={`hermes-energy-station${selected ? ' is-selected' : ''}`}
              style={{ '--station-color': stage.color } as React.CSSProperties}
            >
              <span className="hermes-energy-index">0{index + 1}</span>
              <i className="hermes-energy-junction"><b /></i>
              <span className="hermes-energy-copy">
                <strong>{stage.label}</strong>
                {/* N80：狀態串（「0 待命 · 排隊 5 · 重試 0」）在窄欄位會被截，
                    CSS 已放寬成兩列，這裡再補 title 當最後保險——兩列還放不下時
                    滑鼠停留仍能看到全文。 */}
                <small title={`${stage.metric} ${stage.unit}`.trim()}><b>{stage.metric}</b> {stage.unit}</small>
              </span>
            </button>
          )
        })}
      </div>
      <div className="hermes-engine" aria-label="Hermes Engine">
        <span className="hermes-engine-rings"><i /><b /></span>
        <span><strong>HERMES</strong><small>{t('engineContinuousLabel')}</small></span>
      </div>
    </section>
  )
}

function tierColor(coin: GalaxyCoin): string {
  return coin.tier === 'healthy' ? HERMES_CYAN : coin.tier === 'moderate' ? HERMES_AMBER : HERMES_RED
}
