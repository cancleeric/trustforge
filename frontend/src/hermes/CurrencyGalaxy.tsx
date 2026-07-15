import { TIER_COLOR, type GalaxyCoin, type GalaxyModel, type OrbitId } from '../lib/hermesData'
import { HERMES_CYAN } from '../lib/hermesData'
import { useHermesI18n } from './hermesI18n'

interface CurrencyGalaxyProps {
  model: GalaxyModel
  selectedId: string
  hoveredId: string | null
  focusPulse: boolean
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
}

const RING_SIZE: Record<Exclude<OrbitId, 'core'>, number> = { A: 270, B: 390 }

function sizeOf(econ: number): number {
  return Math.round(10 + econ * 0.26)
}

function gradientOf(tier: GalaxyCoin['tier'], c: string): string {
  if (tier === 'healthy') return `radial-gradient(circle at 35% 30%, #eafffb 0%, ${c} 32%, #0c3f52 75%, #041018 100%)`
  if (tier === 'moderate') return `radial-gradient(circle at 35% 30%, #fff3d9 0%, ${c} 32%, #6b4a12 75%, #1a1206 100%)`
  return `radial-gradient(circle at 35% 30%, #4a1010 0%, ${c} 28%, #2a0505 65%, #0a0202 100%)`
}

function shadowOf(tier: GalaxyCoin['tier'], c: string, active: boolean): string {
  const base = active ? 20 : 8
  const spread = active ? 7 : 2
  if (tier === 'danger')
    return `0 0 ${base + 6}px ${spread + 3}px ${c}, 0 0 ${(base + 6) * 2}px ${spread + 3}px rgba(255,60,60,.25)`
  return `0 0 ${base}px ${spread}px ${c}`
}

export default function CurrencyGalaxy({
  model, selectedId, hoveredId, focusPulse, onSelect, onHover,
}: CurrencyGalaxyProps) {
  const { t } = useHermesI18n()
  const c = (id: string) => model.byId[id]

  const planetStyle = (id: string): React.CSSProperties => {
    const coin = c(id)
    const isHover = id === hoveredId
    const isSel = id === selectedId
    const active = isHover || isSel
    const dimmed = !!selectedId && !isSel && !isHover
    const size = sizeOf(coin.econ)
    const tier = coin.tier
    const color = TIER_COLOR[tier]
    return {
      position: 'absolute',
      border: 'none',
      padding: 0,
      left: '50%',
      top: coin.pos === 'top' ? 0 : '100%',
      width: size,
      height: size,
      borderRadius: '50%',
      background: gradientOf(tier, color),
      transform: `translate(-50%, -50%) scale(${isHover ? 1.9 : isSel ? 1.45 : 1})`,
      boxShadow: shadowOf(tier, color, active),
      opacity: dimmed ? 0.45 : 1,
      cursor: 'pointer',
      transition: 'transform .18s, box-shadow .18s, opacity .2s',
      animation: tier === 'danger' ? 'hermes-flicker 3.2s infinite' : tier === 'moderate' ? 'hermes-flicker 6s infinite' : 'none',
    }
  }

  const coreIsHover = hoveredId === 'btc'
  const coreIsSel = selectedId === 'btc'
  const coreSize = 122

  const ring = (orbit: Exclude<OrbitId, 'core'>, rot: string, spin: string, ids: [string, string]) => (
    <div
      className="hermes-galaxy"
      style={{
        position: 'absolute', left: 0, top: 0, width: RING_SIZE[orbit], height: RING_SIZE[orbit],
        margin: -RING_SIZE[orbit] / 2, borderRadius: '50%',
        border: `1px solid ${orbit === 'B' ? 'rgba(232,179,77,.22)' : 'rgba(77,216,224,.28)'}`,
        transformStyle: 'preserve-3d',
        transform: rot,
        boxShadow: orbit === 'B' ? '0 0 26px rgba(232,179,77,.05) inset' : '0 0 22px rgba(77,216,224,.06) inset',
      }}
    >
      <div style={{ position: 'absolute', inset: 0, transformStyle: 'preserve-3d', animation: `${spin} 40s linear infinite` }}>
        <button
          type="button"
          aria-label={`${t('focus')} ${c(ids[0]).full}`}
          aria-pressed={ids[0] === selectedId}
          onClick={() => onSelect(ids[0])}
          onMouseEnter={() => onHover(ids[0])}
          onMouseLeave={() => onHover(null)}
          style={planetStyle(ids[0])}
        >
          {c(ids[0]).tier === 'danger' && (
            <div style={{ position: 'absolute', left: '50%', top: '50%', width: '180%', height: '180%', border: '1px dashed var(--color-hermes-red)', borderRadius: '50%', transform: 'translate(-50%,-50%)', animation: 'hermes-unstable-rotate 5s linear infinite', opacity: 0.6 }} />
          )}
        </button>
        <button
          type="button"
          aria-label={`${t('focus')} ${c(ids[1]).full}`}
          aria-pressed={ids[1] === selectedId}
          onClick={() => onSelect(ids[1])}
          onMouseEnter={() => onHover(ids[1])}
          onMouseLeave={() => onHover(null)}
          style={planetStyle(ids[1])}
        />
      </div>
    </div>
  )

  const activeId = hoveredId || selectedId
  const readoutC = activeId ? c(activeId) : null

  return (
    <div
      style={{
        position: 'absolute', left: 300, top: 44, width: 840, height: 736, overflow: 'hidden',
        background: 'radial-gradient(ellipse at 50% 44%,#0d1c2a 0%,#050a12 70%)',
        filter: focusPulse ? 'brightness(1.35) saturate(1.3)' : 'brightness(1) saturate(1)',
        transition: 'filter .4s ease-out',
      }}
    >
      {/* parallax starfield: 3 layers */}
      <div style={{ position: 'absolute', inset: -100, backgroundImage: 'radial-gradient(1.5px 1.5px at 8% 22%,rgba(255,255,255,.7),transparent),radial-gradient(1px 1px at 18% 68%,rgba(255,255,255,.5),transparent),radial-gradient(1.5px 1.5px at 32% 15%,rgba(255,255,255,.6),transparent),radial-gradient(1px 1px at 46% 78%,rgba(255,255,255,.4),transparent)', animation: 'hermes-drift-1 90s linear infinite alternate', opacity: 0.8 }} />
      <div style={{ position: 'absolute', inset: -100, backgroundImage: 'radial-gradient(1.5px 1.5px at 62% 30%,rgba(255,255,255,.6),transparent),radial-gradient(1px 1px at 74% 60%,rgba(255,255,255,.5),transparent),radial-gradient(1.5px 1.5px at 88% 20%,rgba(255,255,255,.7),transparent),radial-gradient(1px 1px at 94% 72%,rgba(255,255,255,.4),transparent)', animation: 'hermes-drift-2 130s linear infinite alternate', opacity: 0.6 }} />
      <div style={{ position: 'absolute', inset: -100, backgroundImage: 'radial-gradient(1.5px 1.5px at 55% 85%,rgba(255,255,255,.5),transparent),radial-gradient(1px 1px at 12% 90%,rgba(255,255,255,.35),transparent),radial-gradient(1px 1px at 40% 45%,rgba(255,255,255,.4),transparent)', animation: 'hermes-drift-3 160s linear infinite alternate', opacity: 0.45 }} />
      {/* holo radar sweep */}
      <div style={{ position: 'absolute', left: 0, right: 0, top: 0, height: 60, background: 'linear-gradient(rgba(77,216,224,.09),transparent)', animation: 'hermes-holo-sweep 7s linear infinite', pointerEvents: 'none' }} />
      {/* orbit guide circles */}
      <div style={{ position: 'absolute', left: '50%', top: '50%', width: 660, height: 660, transform: 'translate(-50%,-50%)', border: '1px solid rgba(77,216,224,.12)', borderRadius: '50%' }} />
      <div style={{ position: 'absolute', left: '50%', top: '50%', width: 560, height: 560, transform: 'translate(-50%,-50%)', border: '1px solid rgba(77,216,224,.08)', borderRadius: '50%' }} />
      {/* perspective floor grid */}
      <div style={{ position: 'absolute', left: '-20%', right: '-20%', bottom: 0, height: 160, backgroundImage: 'repeating-linear-gradient(90deg,rgba(77,216,224,.14) 0,rgba(77,216,224,.14) 1px,transparent 1px,transparent 44px),repeating-linear-gradient(0deg,rgba(77,216,224,.1) 0,rgba(77,216,224,.1) 1px,transparent 1px,transparent 22px)', transform: 'perspective(300px) rotateX(60deg)', transformOrigin: 'bottom', pointerEvents: 'none' }} />
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 160, background: 'linear-gradient(rgba(5,10,18,0),rgba(5,10,18,.9))', pointerEvents: 'none' }} />

      <div style={{ position: 'absolute', left: 26, top: 14, display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ fontSize: 10, letterSpacing: '1.8px', color: 'rgba(220,240,245,.4)' }}>BRIDGE MAIN VIEWPORT</span>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'rgba(230,244,248,.9)' }}>Global Currency Galaxy</span>
      </div>

      {/* quick currency selector strip */}
      <div style={{ position: 'absolute', left: 26, top: 44, display: 'flex', gap: 6, zIndex: 4 }}>
        {model.coins.map((coin) => {
          const color = TIER_COLOR[coin.tier]
          const isSel = coin.id === selectedId
          return (
            <button
              type="button"
              aria-label={`${t('focus')} ${coin.full}`}
              aria-pressed={isSel}
              key={coin.id}
              onClick={() => onSelect(coin.id)}
              onMouseEnter={() => onHover(coin.id)}
              onMouseLeave={() => onHover(null)}
              style={{
                cursor: 'pointer', fontFamily: 'inherit', fontSize: 10, fontWeight: 700, letterSpacing: '.5px',
                color: isSel ? '#02121a' : color,
                background: isSel ? color : 'var(--color-hermes-inset)',
                border: `1px solid ${color}`, borderRadius: 4, padding: '4px 8px',
                transition: 'transform .1s',
              }}
              onMouseDown={(e) => (e.currentTarget.style.transform = 'translateY(-1px)')}
              onMouseUp={(e) => (e.currentTarget.style.transform = 'none')}
            >
              {coin.name}
            </button>
          )
        })}
      </div>

      {/* 3D galaxy assembly */}
      <div style={{ position: 'absolute', left: '50%', top: '54%', width: 460, height: 460, perspective: 1100, transform: 'translate(-50%,-50%)' }}>
        <div style={{ position: 'absolute', left: '50%', top: '50%', width: 0, height: 0, transformStyle: 'preserve-3d' }}>
          {ring('B', 'rotateX(66deg) rotateZ(-18deg)', 'hermes-orbit-spin-rev', ['bnb', 'xrp'])}
          {ring('A', 'rotateX(64deg) rotateZ(10deg)', 'hermes-orbit-spin', ['eth', 'sol'])}

          {/* BTC core star */}
          <div
            role="button"
            tabIndex={0}
            aria-label={`${t('focus')} Bitcoin`}
            aria-pressed={coreIsSel}
            onClick={() => onSelect('btc')}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') onSelect('btc')
            }}
            onMouseEnter={() => onHover('btc')}
            onMouseLeave={() => onHover(null)}
            style={{
              position: 'absolute', left: 0, top: 0, width: coreSize, height: coreSize, margin: -coreSize / 2,
              borderRadius: '50%', background: gradientOf('healthy', HERMES_CYAN),
              boxShadow: '0 0 46px 8px rgba(95,227,221,.55), 0 0 100px 24px rgba(232,179,77,.16)',
              transform: `scale(${coreIsHover ? 1.12 : coreIsSel ? 1.06 : 1})`,
              cursor: 'pointer', transition: 'transform .18s, box-shadow .18s',
              animation: 'hermes-core-glow 4.5s ease-in-out infinite',
            }}
          />
          <div style={{ position: 'absolute', left: 0, top: 0, width: 170, height: 170, margin: -85, borderRadius: '50%', background: 'radial-gradient(circle,rgba(95,227,221,.16) 0%,rgba(95,227,221,0) 70%)', pointerEvents: 'none' }} />
        </div>
      </div>

      {/* hover / selection readout */}
      <div style={{ position: 'absolute', left: '50%', bottom: 20, transform: 'translateX(-50%)', minWidth: 300, textAlign: 'center' }}>
        {readoutC ? (
          <div style={{ display: 'inline-flex', flexDirection: 'column', gap: 2, background: 'rgba(6,12,20,.85)', border: '1px solid rgba(255,255,255,.12)', borderRadius: 7, padding: '9px 18px', backdropFilter: 'blur(4px)' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'center', gap: 7 }}>
              <span style={{ fontSize: 10.5, letterSpacing: '.8px', color: TIER_COLOR[readoutC.tier], textTransform: 'uppercase' }}>{readoutC.full}</span>
              <span style={{ fontSize: 16, fontWeight: 600, color: '#fff' }}>{readoutC.score}</span>
              <span style={{ fontSize: 10.5, color: 'rgba(255,255,255,.45)' }}>/ 100 · {readoutC.tier === 'healthy' ? t('highTrust') : readoutC.tier === 'moderate' ? t('moderateTrust') : t('lowTrust')}</span>
            </div>
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,.55)' }}>{t('economicWeight')} {readoutC.econ} · {t('refocus')}</div>
          </div>
        ) : (
          <span style={{ fontSize: 10, letterSpacing: '.6px', color: 'rgba(255,255,255,.3)' }}>{t('focusHint')}</span>
        )}
      </div>
    </div>
  )
}
