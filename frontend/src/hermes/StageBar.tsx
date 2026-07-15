import { HERMES_CYAN, HERMES_AMBER, HERMES_RED, STAGE_DEFS, type GalaxyCoin, type SelectedDerivation } from '../lib/hermesData'
import { useHermesI18n } from './hermesI18n'

interface StageBarProps {
  selCoin: GalaxyCoin
  derivation: SelectedDerivation
  selectedStage: string | null
  onSelectStage: (id: string) => void
}

const ARC_H = [72, 64, 80, 64, 72]

export default function StageBar({ selCoin, derivation, selectedStage, onSelectStage }: StageBarProps) {
  const { t } = useHermesI18n()
  const divColor = derivation.divColor
  const stages = STAGE_DEFS.map((s, i) => {
    const isSel = selectedStage === s.id
    const color = i === 2 ? divColor : s.id === 'manipulation' ? HERMES_AMBER : s.id === 'composite' ? TIER_COLOR_OF(selCoin) : HERMES_CYAN
    const m = derivation.stageMetrics[s.id]
    return {
      ...s, color, h: ARC_H[i],
      border: isSel ? color : 'var(--color-hermes-bd)',
      bg: isSel ? 'var(--color-hermes-hover)' : 'var(--color-hermes-card)',
      pulseAnim: isSel ? 'hermes-select-pulse 1.8s ease-in-out infinite' : 'none',
      metric: m?.metric ?? '',
      unit: s.id === 'scan' ? t('scanned') : s.id === 'filter' ? t('passed') : s.id === 'crossverify' ? t('divergenceUnit') : s.id === 'manipulation' ? t('flagged') : m?.unit ?? '',
      step: `${t('stage')} ${i + 1}`,
      label: s.id === 'scan' ? t('scan') : s.id === 'filter' ? t('filter') : s.id === 'crossverify' ? t('crossverify') : s.id === 'manipulation' ? t('manipulation') : t('composite'),
    }
  })

  return (
    <div
      className="hermes-stage-bar"
      style={{
        position: 'absolute', left: 0, bottom: 0, width: 1440, height: 120, zIndex: 8,
        background: 'rgba(10,16,24,.6)', backdropFilter: 'blur(10px)', borderTop: '1px solid var(--color-hermes-bd)',
        display: 'flex', alignItems: 'flex-end', justifyContent: 'center', gap: 14, padding: '0 40px 12px',
      }}
    >
      {stages.map((st) => (
        <button
          type="button"
          aria-pressed={selectedStage === st.id}
          key={st.id}
          onClick={() => onSelectStage(st.id)}
          className="hermes-clip-sm"
          style={{
            cursor: 'pointer', width: 220, height: st.h, background: st.bg, border: `1px solid ${st.border}`,
            borderBottom: 'none', borderRadius: '8px 8px 0 0', padding: '10px 14px',
            fontFamily: 'inherit', textAlign: 'left',
            transition: 'transform .12s, border-color .15s, background .15s',
            ['--pulse-color' as string]: st.color, animation: st.pulseAnim,
          }}
          onMouseEnter={(e) => (e.currentTarget.style.transform = 'translateY(-4px)')}
          onMouseLeave={(e) => (e.currentTarget.style.transform = 'none')}
          onMouseDown={(e) => (e.currentTarget.style.transform = 'translateY(-1px) scale(.98)')}
          onMouseUp={(e) => (e.currentTarget.style.transform = 'translateY(-4px)')}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 6 }}>
            <span style={{ fontSize: 13, color: st.color, width: 16, textAlign: 'center' }}>{st.icon}</span>
            <span style={{ fontSize: 9.5, letterSpacing: 1, color: 'var(--color-hermes-tx3)' }}>{st.step}</span>
          </div>
          <div style={{ fontWeight: 700, fontSize: 11.5, color: 'var(--color-hermes-tx)', marginBottom: 4 }}>{st.label}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
            <span style={{ fontSize: 17, fontWeight: 600, color: st.color }}>{st.metric}</span>
            <span style={{ fontSize: 9.5, color: 'var(--color-hermes-tx3)' }}>{st.unit}</span>
          </div>
        </button>
      ))}
    </div>
  )
}

function TIER_COLOR_OF(coin: GalaxyCoin): string {
  return coin.tier === 'healthy' ? HERMES_CYAN : coin.tier === 'moderate' ? HERMES_AMBER : HERMES_RED
}
