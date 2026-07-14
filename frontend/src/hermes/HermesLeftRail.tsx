import { TIER_COLOR, type GalaxyModel } from '../lib/hermesData'

interface HermesLeftRailProps {
  model: GalaxyModel
  uplinkLatency?: string
  hermesMessage: string
  hasOrder: boolean
  qtype: string
  qtypes: string[]
  query: string
  submitLabel: string
  disabled?: boolean
  onType: (v: string) => void
  onQuery: (v: string) => void
  onSubmit: () => void
}

export default function HermesLeftRail({
  model, uplinkLatency = '2.4s', hermesMessage, hasOrder, qtype, qtypes, query, submitLabel,
  onType, onQuery, onSubmit, disabled = false,
}: HermesLeftRailProps) {
  const { tierCounts, coins } = model
  return (
    <div
      className="hermes-glass"
      style={{
        position: 'absolute', left: 0, top: 44, width: 300, height: 736, zIndex: 5,
        borderRight: '1px solid var(--color-hermes-bd)', padding: '14px 16px',
        display: 'flex', flexDirection: 'column', gap: 12,
      }}
    >
      <div>
        <div style={{ fontSize: 10, letterSpacing: '1.6px', color: 'var(--color-hermes-tx3)', marginBottom: 9 }}>GALAXY TELEMETRY</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 7, background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd)', borderRadius: 6, padding: '10px 12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}><span style={{ color: 'var(--color-hermes-tx2)' }}>Assets tracked</span><span style={{ color: 'var(--color-hermes-tx)' }}>{coins.length}</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}><span style={{ color: 'var(--color-hermes-tx2)' }}>Healthy</span><span style={{ color: TIER_COLOR.healthy }}>{tierCounts.healthy}</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}><span style={{ color: 'var(--color-hermes-tx2)' }}>Moderate</span><span style={{ color: TIER_COLOR.moderate }}>{tierCounts.moderate}</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}><span style={{ color: 'var(--color-hermes-tx2)' }}>Danger</span><span style={{ color: TIER_COLOR.danger }}>{tierCounts.danger}</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}><span style={{ color: 'var(--color-hermes-tx2)' }}>Uplink latency</span><span style={{ color: 'var(--color-hermes-tx)' }}>{uplinkLatency}</span></div>
        </div>
      </div>

      {/* HERMES CONSOLE */}
      <div
        className="hermes-clip"
        style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, background: 'rgba(13,20,30,.6)', border: '1px solid var(--color-hermes-bd)', borderRadius: 8, padding: 14, boxShadow: 'inset 0 0 24px rgba(77,216,224,.04)' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
          <div style={{ position: 'relative', width: 24, height: 24, flexShrink: 0, animation: 'hermes-hermes-breathe 3.2s ease-in-out infinite' }}>
            <div style={{ position: 'absolute', inset: 0, clipPath: 'polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%)', border: '1.5px solid var(--color-hermes-amber)', animation: 'hermes-orbit-spin 9s linear infinite' }} />
            <div style={{ position: 'absolute', inset: 5, clipPath: 'polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%)', background: 'var(--color-hermes-amber)', opacity: 0.35, animation: 'hermes-orbit-spin-rev 5s linear infinite' }} />
          </div>
          <span style={{ fontSize: 11, letterSpacing: '1.2px', color: 'var(--color-hermes-tx)' }}>HERMES</span>
          <span style={{ fontSize: 9, color: 'var(--color-hermes-cyan)', background: 'rgba(77,216,224,.13)', border: '1px solid rgba(77,216,224,.4)', borderRadius: 3, padding: '1px 6px' }}>ONLINE</span>
        </div>

        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 10 }}>
          <div style={{ background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd)', borderLeft: '2px solid var(--color-hermes-amber)', borderRadius: '0 6px 6px 0', padding: '9px 11px' }}>
            <div style={{ fontSize: 9, color: 'var(--color-hermes-amber)', letterSpacing: 1, marginBottom: 4 }}>HERMES</div>
            <div style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--color-hermes-tx)' }}>{hermesMessage}</div>
          </div>
          {hasOrder && (
            <div style={{ background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd2)', borderRadius: 6, padding: '8px 11px', alignSelf: 'flex-end', maxWidth: '92%' }}>
              <div style={{ fontSize: 9, color: 'var(--color-hermes-tx3)', letterSpacing: 1, marginBottom: 3 }}>ORDER TRANSMITTED</div>
              <div style={{ fontSize: 11, lineHeight: 1.4, color: 'var(--color-hermes-tx2)' }}>&gt; {qtype}: {query}</div>
            </div>
          )}
        </div>

        <label style={{ display: 'block', fontSize: 10, color: 'var(--color-hermes-tx2)', marginBottom: 5 }}>ANALYSIS MODE</label>
        <div style={{ position: 'relative', marginBottom: 10 }}>
          <select
            value={qtype}
            onChange={(e) => onType(e.target.value)}
            style={{ width: '100%', appearance: 'none', background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd2)', borderRadius: 5, color: 'var(--color-hermes-tx)', fontFamily: 'var(--font-hermes-mono)', fontSize: 12, padding: '8px 10px', cursor: 'pointer' }}
          >
            {qtypes.map((q) => <option key={q} value={q}>{q}</option>)}
          </select>
          <span style={{ position: 'absolute', right: 10, top: 10, color: 'var(--color-hermes-tx3)', pointerEvents: 'none', fontSize: 10 }}>▼</span>
        </div>

        <label style={{ display: 'block', fontSize: 10, color: 'var(--color-hermes-tx2)', marginBottom: 5 }}>ORDER TO HERMES</label>
        <textarea
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          rows={2}
          style={{ width: '100%', resize: 'none', background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd2)', borderRadius: 5, color: 'var(--color-hermes-tx)', fontFamily: 'var(--font-hermes-mono)', fontSize: 11.5, lineHeight: 1.5, padding: '8px 10px', marginBottom: 10 }}
        />

        <button
          onClick={onSubmit}
          disabled={disabled}
          style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, background: 'var(--color-hermes-amber)', border: 'none', borderRadius: 5, color: '#1a1206', fontWeight: 700, fontSize: 12, padding: 9, cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? .55 : 1, transition: 'filter .15s, transform .08s' }}
          onMouseEnter={(e) => (e.currentTarget.style.filter = 'brightness(1.12)')}
          onMouseLeave={(e) => (e.currentTarget.style.filter = 'none')}
          onMouseDown={(e) => (e.currentTarget.style.transform = 'scale(.96)')}
          onMouseUp={(e) => (e.currentTarget.style.transform = 'none')}
        >
          <span>{submitLabel}</span><span>⤴</span>
        </button>
      </div>
    </div>
  )
}
