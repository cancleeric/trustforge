import type { ReactNode } from 'react'
import { TIER_COLOR, type GalaxyCoin, type SelectedDerivation, type TrustComponent } from '../lib/hermesData'
import { useHermesI18n } from './hermesI18n'
import type { AnalysisFlowData, AnalysisJourneyData } from '../lib/endpoints'
import type { CrossSourceSignal } from '../lib/types'
import GlossaryTerm, { type GlossaryKey } from '../components/GlossaryTerm'

interface HermesRightRailProps {
  selCoin: GalaxyCoin
  components: TrustComponent[]
  derivation: SelectedDerivation
  displayScore?: number
  derived?: boolean
  flow?: AnalysisFlowData | null
  journey?: AnalysisJourneyData | null
  crossSignal?: CrossSourceSignal | null
  trainingStatus?: ReactNode
  onOpenComposite: () => void
  onOpenDivergence: () => void
}

export default function HermesRightRail({
  selCoin, components, derivation, displayScore, derived, flow, journey, crossSignal, trainingStatus, onOpenComposite, onOpenDivergence,
}: HermesRightRailProps) {
  const { t } = useHermesI18n()
  const { score, tier, full } = selCoin
  const scoreColor = TIER_COLOR[tier]
  const scoreLabel = tier === 'healthy' ? t('highTrust') : tier === 'moderate' ? t('moderateTrust') : t('lowTrust')
  const scoreDim = tier === 'healthy' ? 'rgba(77,216,224,.13)' : tier === 'moderate' ? 'rgba(232,179,77,.13)' : 'rgba(255,95,95,.14)'
  const gaugeScore = displayScore ?? score
  const activeStages = flow?.stages.filter((stage) => stage.current) ?? []
  const queued = flow?.stages.reduce((sum, stage) => sum + stage.queued, 0) ?? 0
  const latestJob = journey?.jobs.find((job) => job.coin === selCoin.name || job.coin === selCoin.id?.toUpperCase())

  const C = 2 * Math.PI * 80
  const arcSpan = 0.75 * C
  const arcTrack = `${arcSpan.toFixed(1)} ${C.toFixed(1)}`
  const arcVal = `${(arcSpan * gaugeScore / 100).toFixed(1)} ${C.toFixed(1)}`

  const divDock = derivation
  const dockColor = divDock.divColor
  const divergenceSummary = crossSignal ? crossSignal.summary
    : tier === 'healthy' ? `${t('alignment')} · Δ ${divDock.divergence}%`
      : tier === 'moderate' ? `${t('monitor')} · Δ ${divDock.divergence}%`
        : `${t('conflict')} · Δ ${divDock.divergence}%`
  const componentLabel = (label: string) => label === 'Reputation' ? t('reputation') : label === 'Corroboration' ? t('corroboration') : label === 'Recency' ? t('recency') : t('resistance')
  const componentGlossary = (label: string): GlossaryKey => label === 'Reputation' ? 'reputation' : label === 'Corroboration' ? 'corroboration' : label === 'Recency' ? 'recency' : 'manipulation'

  return (
    <div
      className="hermes-glass hermes-right-rail"
      style={{
        position: 'absolute', right: 0, top: 'var(--hermes-top)', width: 'var(--hermes-rail)', height: 'calc(100% - var(--hermes-top) - var(--hermes-bottom))', zIndex: 5,
        borderLeft: '1px solid var(--color-hermes-bd)', padding: '14px 16px',
        display: 'flex', flexDirection: 'column', gap: 12,
        overflowY: 'auto',
      }}
    >
      <div style={{ fontSize: 10, letterSpacing: '1.2px', color: 'var(--color-hermes-tx2)' }}>{t('focused')}: <b style={{ color: 'var(--color-hermes-cyan)' }}>{full}</b></div>

      {/* gauge */}
      <div className="hermes-clip" style={{ background: 'var(--color-hermes-card)', border: '1px solid var(--color-hermes-bd)', borderRadius: 8, padding: 14, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
        <div style={{ alignSelf: 'flex-start', fontSize: 10, letterSpacing: '1.2px', color: 'var(--color-hermes-tx3)', marginBottom: 6 }}><GlossaryTerm term="trustScore" label={t('trustScore')} compact /></div>
        <div style={{ position: 'relative', width: 140, height: 140 }}>
          <svg viewBox="0 0 200 200" width="140" height="140">
            <circle cx="100" cy="100" r="80" fill="none" style={{ stroke: 'var(--color-hermes-bd2)' } as React.CSSProperties} strokeWidth="16" strokeLinecap="round" strokeDasharray={arcTrack} transform="rotate(135 100 100)" />
            <circle cx="100" cy="100" r="80" fill="none" style={{ stroke: scoreColor } as React.CSSProperties} strokeWidth="16" strokeLinecap="round" strokeDasharray={arcVal} transform="rotate(135 100 100)" />
          </svg>
          <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
            <div data-testid="right-rail-score" style={{ fontWeight: 700, fontSize: 32, lineHeight: 1, color: scoreColor }}>{gaugeScore}</div>
            <div style={{ fontSize: 10, color: 'var(--color-hermes-tx3)', marginTop: 2 }}>/ 100</div>
          </div>
        </div>
        <div style={{ marginTop: 6, fontSize: 11, fontWeight: 600, letterSpacing: '.5px', color: scoreColor, background: scoreDim, border: `1px solid ${scoreColor}`, borderRadius: 4, padding: '3px 10px' }}>{scoreLabel}</div>
      </div>

      <div className="hermes-clip" style={{ background: 'var(--color-hermes-card)', border: '1px solid var(--color-hermes-bd)', borderRadius: 8, padding: '9px 12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, letterSpacing: 1, color: 'var(--color-hermes-tx3)', marginBottom: 7 }}>
          <span>CONTINUOUS ENGINE</span><b style={{ color: activeStages.length ? 'var(--color-hermes-cyan)' : 'var(--color-hermes-amber)' }}>{activeStages.length} RUNNING</b>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', gap: 4 }}>
          {(flow?.stages ?? []).map((stage, index) => <div key={stage.id} title={stage.current ? `${stage.current.coin} · ${stage.current.question}` : `${stage.queued} queued`}
            style={{ height: 5, borderRadius: 3, background: stage.current ? 'var(--color-hermes-cyan)' : stage.queued ? 'var(--color-hermes-amber)' : 'var(--color-hermes-bd2)', boxShadow: stage.current ? '0 0 8px var(--color-hermes-cyan)' : undefined }} aria-label={`stage ${index + 1}`} />)}
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 7, color: 'var(--color-hermes-tx2)', fontSize: 9.5 }}>
          <span>排隊 {queued}</span><span>{latestJob ? `${latestJob.mode} · ${latestJob.state}` : '等待此幣快照'}</span>
        </div>
      </div>

      {/* breakdown */}
      <div className="hermes-clip" style={{ flex: 1, minHeight: 0, background: 'var(--color-hermes-card)', border: '1px solid var(--color-hermes-bd)', borderRadius: 8, padding: '12px 14px', display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 10, letterSpacing: '1.6px', color: 'var(--color-hermes-tx3)', marginBottom: 10 }}>
          {t('trustBreakdown')}
          {derived && (
            <GlossaryTerm term="proxy" label={t('overviewProxy')} compact />
          )}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
          {components.map((comp) => (
            <div key={comp.label} data-testid={`right-rail-component-${comp.label.toLowerCase().replaceAll(' ', '-')}`} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ width: 7, height: 7, borderRadius: 2, background: comp.barColor, flexShrink: 0 }} />
                <span style={{ fontSize: 11.5, color: 'var(--color-hermes-tx)', flex: 1 }}><GlossaryTerm term={componentGlossary(comp.label)} label={componentLabel(comp.label)} compact /></span>
                <span style={{ fontSize: 12, fontWeight: 600, color: comp.barColor }}>{comp.score}</span>
              </div>
              <div style={{ height: 4, width: '100%', background: 'var(--color-hermes-inset)', borderRadius: 2, overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${comp.score}%`, background: comp.barColor }} />
              </div>
            </div>
          ))}
        </div>
        <button
          onClick={onOpenComposite}
          style={{ marginTop: 'auto', width: '100%', background: 'transparent', border: '1px solid var(--color-hermes-bd2)', borderRadius: 5, color: 'var(--color-hermes-tx2)', fontSize: 10.5, padding: 7, cursor: 'pointer' }}
          onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--color-hermes-cyan)'; e.currentTarget.style.color = 'var(--color-hermes-tx)' }}
          onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--color-hermes-bd2)'; e.currentTarget.style.color = 'var(--color-hermes-tx2)' }}
        >
          {t('fullBreakdown')} →
        </button>
      </div>

      {trainingStatus && <div className="hermes-training-status-slot">{trainingStatus}</div>}

      {/* divergence alert dock */}
      <div
        className="hermes-clip hermes-divergence-dock"
        style={{
          background: divDock.divDim, border: `1px solid ${divDock.divBd}`, borderRadius: 8, padding: '11px 14px',
          animation: tier === 'danger' ? 'hermes-alert-flash 2.4s ease-in-out infinite' : undefined,
        }}
        onMouseEnter={(e) => (e.currentTarget.style.transform = 'translateY(-1px)')}
        onMouseLeave={(e) => (e.currentTarget.style.transform = 'none')}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
          <span style={{ color: dockColor, fontSize: 13 }}>⚠</span>
          <span style={{ fontSize: 11, fontWeight: 700, color: dockColor, letterSpacing: '.5px' }}>
            <GlossaryTerm term="divergence" label={t('divergence')} compact />
          </span>
        </div>
        <button
          type="button"
          aria-label={`${t('divergence')}：${divergenceSummary}；${t('tapReview')}`}
          onClick={onOpenDivergence}
          style={{
            width: '100%', padding: 0, border: 0, background: 'transparent',
            color: 'var(--color-hermes-tx2)', font: 'inherit', fontSize: 10.5,
            textAlign: 'left', cursor: 'pointer',
          }}
        >
          {divergenceSummary}
        </button>
      </div>
    </div>
  )
}
