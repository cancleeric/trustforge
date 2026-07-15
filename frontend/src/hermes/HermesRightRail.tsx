import { TIER_COLOR, TIER_LABEL, type GalaxyCoin, type SelectedDerivation, type TrustComponent } from '../lib/hermesData'

interface HermesRightRailProps {
  selCoin: GalaxyCoin
  components: TrustComponent[]
  derivation: SelectedDerivation
  derived?: boolean
  onOpenComposite: () => void
  onOpenDivergence: () => void
}

export default function HermesRightRail({
  selCoin, components, derivation, derived, onOpenComposite, onOpenDivergence,
}: HermesRightRailProps) {
  const { score, tier, full } = selCoin
  const scoreColor = TIER_COLOR[tier]
  const scoreLabel = TIER_LABEL[tier]
  const scoreDim = tier === 'healthy' ? 'rgba(77,216,224,.13)' : tier === 'moderate' ? 'rgba(232,179,77,.13)' : 'rgba(255,95,95,.14)'

  const C = 2 * Math.PI * 80
  const arcSpan = 0.75 * C
  const arcTrack = `${arcSpan.toFixed(1)} ${C.toFixed(1)}`
  const arcVal = `${(arcSpan * score / 100).toFixed(1)} ${C.toFixed(1)}`

  const divDock = derivation
  const dockColor = divDock.divColor

  return (
    <div
      className="hermes-glass hermes-right-rail"
      style={{
        position: 'absolute', right: 0, top: 44, width: 300, height: 736, zIndex: 5,
        borderLeft: '1px solid var(--color-hermes-bd)', padding: '14px 16px',
        display: 'flex', flexDirection: 'column', gap: 12,
      }}
    >
      <div style={{ fontSize: 10, letterSpacing: '1.2px', color: 'var(--color-hermes-tx2)' }}>FOCUSED: <b style={{ color: 'var(--color-hermes-cyan)' }}>{full}</b></div>

      {/* gauge */}
      <div className="hermes-clip" style={{ background: 'var(--color-hermes-card)', border: '1px solid var(--color-hermes-bd)', borderRadius: 8, padding: 14, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
        <div style={{ alignSelf: 'flex-start', fontSize: 10, letterSpacing: '1.6px', color: 'var(--color-hermes-tx3)', marginBottom: 6 }}>TRUST SCORE</div>
        <div style={{ position: 'relative', width: 140, height: 140 }}>
          <svg viewBox="0 0 200 200" width="140" height="140">
            <circle cx="100" cy="100" r="80" fill="none" style={{ stroke: 'var(--color-hermes-bd2)' } as React.CSSProperties} strokeWidth="16" strokeLinecap="round" strokeDasharray={arcTrack} transform="rotate(135 100 100)" />
            <circle cx="100" cy="100" r="80" fill="none" style={{ stroke: scoreColor } as React.CSSProperties} strokeWidth="16" strokeLinecap="round" strokeDasharray={arcVal} transform="rotate(135 100 100)" />
          </svg>
          <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ fontWeight: 700, fontSize: 32, lineHeight: 1, color: scoreColor }}>{score}</div>
            <div style={{ fontSize: 10, color: 'var(--color-hermes-tx3)', marginTop: 2 }}>/ 100</div>
          </div>
        </div>
        <div style={{ marginTop: 6, fontSize: 11, fontWeight: 600, letterSpacing: '.5px', color: scoreColor, background: scoreDim, border: `1px solid ${scoreColor}`, borderRadius: 4, padding: '3px 10px' }}>{scoreLabel}</div>
      </div>

      {/* breakdown */}
      <div className="hermes-clip" style={{ flex: 1, minHeight: 0, background: 'var(--color-hermes-card)', border: '1px solid var(--color-hermes-bd)', borderRadius: 8, padding: '12px 14px', display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 10, letterSpacing: '1.6px', color: 'var(--color-hermes-tx3)', marginBottom: 10 }}>
          TRUST BREAKDOWN
          {derived && (
            <span title="Derived from overview score; run analysis for evidence-bound components" style={{ color: 'var(--color-hermes-amber)', fontSize: 8.5 }}>
              OVERVIEW PROXY
            </span>
          )}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
          {components.map((comp) => (
            <div key={comp.label} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ width: 7, height: 7, borderRadius: 2, background: comp.barColor, flexShrink: 0 }} />
                <span style={{ fontSize: 11.5, color: 'var(--color-hermes-tx)', flex: 1 }}>{comp.label}</span>
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
          FULL BREAKDOWN + REASONING →
        </button>
      </div>

      {/* divergence alert dock */}
      <div
        onClick={onOpenDivergence}
        className="hermes-clip"
        style={{
          cursor: 'pointer', background: divDock.divDim, border: `1px solid ${divDock.divBd}`, borderRadius: 8, padding: '11px 14px',
          animation: tier === 'danger' ? 'hermes-alert-flash 2.4s ease-in-out infinite' : undefined,
        }}
        onMouseEnter={(e) => (e.currentTarget.style.transform = 'translateY(-1px)')}
        onMouseLeave={(e) => (e.currentTarget.style.transform = 'none')}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
          <span style={{ color: dockColor, fontSize: 13 }}>⚠</span>
          <span style={{ fontSize: 11, fontWeight: 700, color: dockColor, letterSpacing: '.5px' }}>CROSS-SOURCE DIVERGENCE</span>
        </div>
        <div style={{ fontSize: 10.5, color: 'var(--color-hermes-tx2)' }}>
          {tier === 'healthy' ? `Alignment nominal · Δ ${divDock.divergence}% — tap to review`
            : tier === 'moderate' ? `Monitor divergence · Δ ${divDock.divergence}% — tap to review`
              : `Conflict detected · Δ ${divDock.divergence}% — tap to review`}
        </div>
      </div>
    </div>
  )
}
