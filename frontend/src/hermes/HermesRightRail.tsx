import type { ReactNode } from 'react'
import { TIER_COLOR, type GalaxyCoin, type SelectedDerivation, type TrustComponent } from '../lib/hermesData'
import { jobStateLabel, modeLabel, useHermesI18n } from './hermesI18n'
import type { AnalysisFlowData, AnalysisJourneyData } from '../lib/endpoints'
import type { CrossSourceSignal } from '../lib/types'
import GlossaryTerm, { type GlossaryKey } from '../components/GlossaryTerm'
import WhaleActivityPanel, { type WhaleSummary } from '../components/WhaleActivityPanel'

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
  whaleSummary?: WhaleSummary | null
  onOpenComposite: () => void
  onOpenDivergence: () => void
}

export default function HermesRightRail({
  selCoin, components, derivation, displayScore, derived, flow, journey, crossSignal, trainingStatus, whaleSummary, onOpenComposite, onOpenDivergence,
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
        position: 'absolute', right: 0, top: 'var(--hermes-top)', width: 'var(--hermes-right-rail)',         /* N50：高度從 `calc(100% - top - bottom)` 改成 `calc(100% - top)`。
           管線條右緣改成停在右軌左緣之後，右軌下方那條 `--hermes-bottom`
           高的帶狀區域不再有東西填，就變成右下角一個空格（老闆原話
           「右下空了一格」）。跟 N44 左軌的處理一致：軌道自己貼到畫面底緣。 */
        height: 'calc(100% - var(--hermes-top))', zIndex: 5,
        borderLeft: '1px solid var(--color-hermes-bd)', padding: 0,
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      {/* === 固定頂部：信任分數 + 引擎狀態 === */}
      <div style={{ flexShrink: 0, padding: '14px 16px 10px', borderBottom: '1px solid var(--color-hermes-bd)' }}>
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
          <span>{t('continuousEngineLabel')}</span><b style={{ color: activeStages.length ? 'var(--color-hermes-cyan)' : 'var(--color-hermes-amber)' }}>{activeStages.length} {t('runningLabel')}</b>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', gap: 4 }}>
          {(flow?.stages ?? []).map((stage, index) => <div key={stage.id} title={stage.current ? `${stage.current.coin} · ${stage.current.question}` : `${stage.queued} queued`}
            style={{ height: 5, borderRadius: 3, background: stage.current ? 'var(--color-hermes-cyan)' : stage.queued ? 'var(--color-hermes-amber)' : 'var(--color-hermes-bd2)', boxShadow: stage.current ? '0 0 8px var(--color-hermes-cyan)' : undefined }} aria-label={`stage ${index + 1}`} />)}
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 7, color: 'var(--color-hermes-tx2)', fontSize: 9.5 }}>
          <span>{t('queued')} {queued}</span><span>{latestJob ? `${modeLabel(latestJob.mode, t)} · ${jobStateLabel(latestJob.state, t)}` : t('waitingSnapshot')}</span>
        </div>
      </div>

      </div>{/* end fixed top */}

      {/* === 可滾動下方 === */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '10px 16px 16px', display: 'flex', flexDirection: 'column', gap: 12, scrollbarWidth: 'thin' as unknown as undefined }}>

      {/* breakdown */}
      {/* N36: 原本是 `flex: 1, minHeight: 0`——minHeight:0 明確允許這塊被壓到比內容還矮，
          而 .hermes-clip 只有 clip-path、overflow 是 visible，壓縮後小孩不會滾、直接溢出盒外。
          實測本盒高度：1920x1080=254px（正常）→ 1440x900=74px → 1280x800=26px，但 4 條
          因子列＋「查看完整拆解與推理」CTA 仍畫在 y=454~610 的原尺寸位置，跑到下一個
          兄弟（.hermes-training-status-slot，y=445~630）的地盤上，被它較晚繪製的文字蓋住：
          1280x800 有 7 顆控件 elementFromPoint 打不到自己，1440x900 有 5 顆。
          改 `flex: 1 0 auto`（可長不可縮）＋拿掉 minHeight:0：右軌自己已經是 overflow-y:auto
          （實測 clientHeight 647 / scrollHeight 686 本來就在滾），空間不夠時讓右軌滾，
          而不是把面板壓扁讓內容逃出去。 */}
      <div className="hermes-clip" style={{ flex: '1 0 auto', background: 'var(--color-hermes-card)', border: '1px solid var(--color-hermes-bd)', borderRadius: 8, padding: '12px 14px', display: 'flex', flexDirection: 'column' }}>
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

      {/* Whale Alert — 鯨魚大額轉帳即時信號 */}
      <WhaleActivityPanel summary={whaleSummary ?? null} />

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
            // N36: padding:0 + fontSize 10.5 讓這顆實測只有 204x16，低於 24px。
            // 外層 dock 有 11px 14px padding，撐到 24px 不會壓到鄰居。
            width: '100%', minHeight: 24, display: 'flex', alignItems: 'center',
            padding: 0, border: 0, background: 'transparent',
            color: 'var(--color-hermes-tx2)', font: 'inherit', fontSize: 10.5,
            textAlign: 'left', cursor: 'pointer',
          }}
        >
          {divergenceSummary}
        </button>
      </div>
      </div>{/* end scrollable */}
    </div>
  )
}
