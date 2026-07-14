import { useState } from 'react'
import { HERMES_CYAN, HERMES_AMBER, HERMES_RED, STAGE_DEFS, type GalaxyCoin, type SelectedDerivation } from '../lib/hermesData'

interface StageDrilldownProps {
  selCoin: GalaxyCoin
  derivation: SelectedDerivation
  selectedStage: string
  onClose: () => void
}

export default function StageDrilldown({ selCoin, derivation, selectedStage, onClose }: StageDrilldownProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const isDivergence = selectedStage === 'divergence'
  const selDef = !isDivergence ? STAGE_DEFS.find((s) => s.id === selectedStage) : null
  const label = isDivergence ? 'Cross-Source Divergence' : selDef?.label ?? ''
  const icon = isDivergence ? '⚠' : selDef?.icon ?? ''
  const color = isDivergence ? HERMES_RED : selectedStage === 'manipulation' ? HERMES_AMBER
    : selectedStage === 'crossverify' ? derivation.divColor
      : selectedStage === 'composite' ? (selCoin.tier === 'healthy' ? HERMES_CYAN : selCoin.tier === 'moderate' ? HERMES_AMBER : HERMES_RED)
        : HERMES_CYAN

  const toggle = (key: string) => setExpanded((e) => ({ ...e, [key]: !e[key] }))

  return (
    <div
      className="hermes-clip-lg"
      style={{
        position: 'absolute', left: 640, top: 70, width: 490, height: 610, zIndex: 20,
        background: 'rgba(8,14,22,.92)', backdropFilter: 'blur(6px)', border: `1px solid ${color}`,
        borderRadius: 10, boxShadow: '0 20px 60px rgba(0,0,0,.5)', display: 'flex', flexDirection: 'column',
        animation: 'hermes-panel-in .22s ease-out',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 18px', borderBottom: '1px solid var(--color-hermes-bd)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <span style={{ fontSize: 15, color }}>{icon}</span>
          <span style={{ fontWeight: 700, fontSize: 13, color: 'var(--color-hermes-tx)' }}>{selCoin.full} — {label}</span>
        </div>
        <button
          onClick={onClose}
          style={{ background: 'transparent', border: '1px solid var(--color-hermes-bd2)', borderRadius: 5, color: 'var(--color-hermes-tx2)', fontSize: 10, padding: '4px 9px', cursor: 'pointer' }}
          onMouseEnter={(e) => { e.currentTarget.style.borderColor = color; e.currentTarget.style.color = 'var(--color-hermes-tx)' }}
          onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--color-hermes-bd2)'; e.currentTarget.style.color = 'var(--color-hermes-tx2)' }}
        >CLOSE ✕</button>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '16px 18px' }}>
        {selectedStage === 'scan' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {derivation.scanItems.map((it, i) => {
              const key = `${selCoin.id}_${i}`
              const open = !!expanded[key]
              const credColor = it.credibility >= 75 ? HERMES_CYAN : it.credibility >= 50 ? HERMES_AMBER : HERMES_RED
              return (
                <div key={key} onClick={() => toggle(key)} style={{ cursor: 'pointer', background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd)', borderRadius: 6, padding: '8px 11px' }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-hermes-hover)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--color-hermes-inset)')}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-hermes-tx)', flex: 1 }}>{it.name}</span>
                    <span style={{ fontSize: 10, color: 'var(--color-hermes-tx3)' }}>{it.time}</span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: credColor, width: 34, textAlign: 'right' }}>{it.credibility}</span>
                  </div>
                  {open && <div style={{ marginTop: 7, paddingTop: 7, borderTop: '1px solid var(--color-hermes-bd)', fontSize: 11, color: 'var(--color-hermes-tx2)', lineHeight: 1.5 }}>{it.note}</div>}
                </div>
              )
            })}
          </div>
        )}

        {selectedStage === 'filter' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div>
              <div style={{ fontSize: 10, color: HERMES_CYAN, letterSpacing: 1, marginBottom: 7 }}>PASSED · {derivation.passedCount}</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {derivation.passedItems.map((p) => <span key={p} style={{ fontSize: 11, color: 'var(--color-hermes-tx)', background: 'rgba(77,216,224,.13)', border: '1px solid rgba(77,216,224,.4)', borderRadius: 5, padding: '5px 9px' }}>{p}</span>)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: HERMES_RED, letterSpacing: 1, marginBottom: 7 }}>FLAGGED / DROPPED · {derivation.flaggedCount}</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {derivation.droppedItems.map((d) => (
                  <div key={d.name} style={{ fontSize: 11.5, color: 'var(--color-hermes-tx)', background: 'rgba(255,95,95,.14)', border: '1px solid rgba(255,95,95,.45)', borderRadius: 6, padding: '7px 10px' }}>
                    <span style={{ fontWeight: 600 }}>{d.name}</span> — <span style={{ color: 'var(--color-hermes-tx2)' }}>{d.reason}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {(selectedStage === 'crossverify' || isDivergence) && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ fontSize: 10.5, color: derivation.divColor, background: derivation.divDim, border: `1px solid ${derivation.divBd}`, borderRadius: 5, padding: '4px 9px', width: 'fit-content' }}>DIVERGENCE · Δ {derivation.divergence}%</div>
            {derivation.crossItems.map((cv, i) => (
              <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 5, background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd)', borderLeft: `3px solid ${cv.color}`, borderRadius: '0 6px 6px 0', padding: '9px 12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                  <span style={{ fontSize: 10.5, fontWeight: 600, color: cv.color }}>{cv.stance}</span>
                  <span style={{ fontSize: 10, color: 'var(--color-hermes-tx3)' }}>{cv.source}</span>
                </div>
                <span style={{ fontSize: 12, color: 'var(--color-hermes-tx)', lineHeight: 1.5 }}>{cv.claim}</span>
              </div>
            ))}
          </div>
        )}

        {selectedStage === 'manipulation' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ fontSize: 11.5, color: 'var(--color-hermes-tx2)' }}>Flagged channel: <b style={{ color: 'var(--color-hermes-tx)' }}>Social Sentiment Scanner</b></div>
            {derivation.manipulationItems.map((m, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: 11.5, color: 'var(--color-hermes-tx)', background: 'rgba(255,95,95,.14)', border: '1px solid rgba(255,95,95,.45)', borderRadius: 6, padding: '8px 11px' }}>
                <span style={{ color: HERMES_RED }}>✕</span><span>{m}</span>
              </div>
            ))}
          </div>
        )}

        {selectedStage === 'composite' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {derivation.components.map((c) => (
              <div key={c.label} style={{ display: 'flex', flexDirection: 'column', gap: 4, background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd)', borderRadius: 6, padding: '9px 12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontWeight: 600, fontSize: 11.5, flex: 1, color: 'var(--color-hermes-tx)' }}>{c.label}</span>
                  <span style={{ fontSize: 9.5, color: 'var(--color-hermes-tx3)' }}>wt {c.weight}%</span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: c.barColor, width: 28, textAlign: 'right' }}>{c.score}</span>
                </div>
              </div>
            ))}
            <div style={{ fontSize: 10, letterSpacing: 1, color: 'var(--color-hermes-tx3)', marginTop: 6 }}>REASONING TRACE</div>
            {derivation.steps.map((stp, i) => (
              <div key={i} style={{ marginLeft: stp.indent, background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd)', borderLeft: `3px solid ${stp.color}`, borderRadius: '0 6px 6px 0', padding: '9px 12px' }}>
                <div style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: 1, color: stp.color, marginBottom: 5 }}>{stp.kind}</div>
                <p style={{ margin: 0, fontSize: 11.5, lineHeight: 1.5, color: 'var(--color-hermes-tx)' }}>{stp.text}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
