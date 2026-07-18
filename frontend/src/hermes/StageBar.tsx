import { HERMES_CYAN, HERMES_AMBER, HERMES_RED, STAGE_DEFS, type GalaxyCoin, type SelectedDerivation } from '../lib/hermesData'
import { useHermesI18n } from './hermesI18n'
import type { HermesWorkspaceModule } from './HermesModuleDeck'
import type { BridgeHologramData } from '../components/BridgeHologramContext'
import type { AnalysisFlowData } from '../lib/endpoints'

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
  const moduleLabels: Record<HermesWorkspaceModule, [string, string, string, string, string]> = locale === 'zh-TW' ? {
    analyze: ['來源蒐集', '主張抽取', '信任推理', '證據綁定', '報告交付'],
    compare: ['市場 A', '市場 B', '基準正規化', '差異向量', '比較結論'],
    history: ['歷史封存', '時間切片', '每日回放', '結果回標', '校準趨勢'],
    status: ['來源連線', '快取狀態', '資料鮮度', '異常告警', '系統健康'],
    costs: ['呼叫收集', '模型分組', 'Token 計量', '帳本封存', '累計成本'],
  } : {
    analyze: ['SOURCE INTAKE', 'CLAIM EXTRACTION', 'TRUST REASONING', 'EVIDENCE BINDING', 'REPORT DELIVERY'],
    compare: ['MARKET A', 'MARKET B', 'NORMALIZE', 'DELTA VECTOR', 'VERDICT'],
    history: ['ARCHIVE', 'TIME SLICE', 'DAILY REPLAY', 'OUTCOME LABEL', 'CALIBRATION'],
    status: ['UPLINK', 'CACHE', 'FRESHNESS', 'ALERTS', 'HEALTH'],
    costs: ['CALLS', 'MODELS', 'TOKENS', 'LEDGER', 'TOTAL COST'],
  }
  const liveFlow = !telemetry?.runId && flow?.stages.some((stage) => stage.current || stage.queued > 0)
  const engineStages = mode === 'analyze' && liveFlow ? flow?.stages.map((stage) => ({
    id: stage.id,
    label: moduleLabels.analyze[flow.stages.indexOf(stage)],
    metric: String(stage.current ? 1 : 0),
    unit: stage.next_retry_at
      ? `排隊 ${stage.queued} · ${Math.max(0, Math.ceil(stage.next_retry_at - Date.now() / 1000))}s 後重試`
      : `${stage.current ? '處理中' : '待命'} · 排隊 ${stage.queued} · 重試 ${stage.current?.retry_count ?? 0}`,
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
        <b>{currentWork ? 'ANALYZING' : telemetry?.runId ? 'SNAPSHOT LOCKED' : 'CONTINUOUS'}</b>
        <span>{currentWork?.coin ?? activity?.coin ?? selCoin.name}</span>
        <span>{currentWork?.mode ?? telemetry?.analysisMode ?? activity?.mode ?? mode ?? 'multi_source'}</span>
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
              onClick={() => onSelectStage(stage.id)}
              className={`hermes-energy-station${selected ? ' is-selected' : ''}`}
              style={{ '--station-color': stage.color } as React.CSSProperties}
            >
              <span className="hermes-energy-index">0{index + 1}</span>
              <i className="hermes-energy-junction"><b /></i>
              <span className="hermes-energy-copy">
                <strong>{stage.label}</strong>
                <small><b>{stage.metric}</b> {stage.unit}</small>
              </span>
            </button>
          )
        })}
      </div>
      <div className="hermes-engine" aria-label="Hermes Engine">
        <span className="hermes-engine-rings"><i /><b /></span>
        <span><strong>HERMES</strong><small>ENGINE · CONTINUOUS</small></span>
      </div>
    </section>
  )
}

function tierColor(coin: GalaxyCoin): string {
  return coin.tier === 'healthy' ? HERMES_CYAN : coin.tier === 'moderate' ? HERMES_AMBER : HERMES_RED
}
