import { useHermesI18n } from './hermesI18n'
import type { HermesWorkspaceModule } from './HermesModuleDeck'

interface HermesTopBarProps {
  costLedger?: number | null
  version?: string
  systemId?: string
  activeModule?: HermesWorkspaceModule | null
  onModuleSelect?: (module: HermesWorkspaceModule) => void
  onHome?: () => void
  degradedMessage?: string | null
  onToggleShip?: () => void
}

export default function HermesTopBar({
  costLedger = null,
  version = 'snapshot · GALAXY',
  systemId = 'SYS·HRM-01',
  activeModule = null,
  onModuleSelect,
  onHome,
  degradedMessage = null,
  onToggleShip,
}: HermesTopBarProps) {
  const { locale, setLocale, t } = useHermesI18n()
  const navItems = [
    { id: 'analyze' as const, label: t('analyze') }, { id: 'compare' as const, label: t('compare') },
    { id: 'history' as const, label: t('history') }, { id: 'status' as const, label: t('sources') }, { id: 'costs' as const, label: t('costs') },
  ]
  return (
    <div
      className="hermes-topbar"
      style={{
        position: 'absolute', left: 0, right: 0, top: 0, height: 'var(--hermes-top)', zIndex: 10,
        display: 'flex', alignItems: 'center', gap: 14, padding: '0 20px',
        background: 'rgba(10,16,24,.62)', backdropFilter: 'blur(10px)',
        borderBottom: '1px solid var(--color-hermes-bd)',
        boxShadow: '0 1px 12px rgba(77,216,224,.08)',
      }}
    >
      <button type="button" onClick={onHome} aria-label="HERMES 主頁" style={{ display: 'flex', alignItems: 'center', gap: 9, border: 0, padding: 0, background: 'transparent', fontFamily: 'inherit', cursor: 'pointer' }}>
        <div style={{ width: 16, height: 16, position: 'relative', transform: 'rotate(45deg)', border: '1.5px solid var(--color-hermes-cyan)', borderRadius: 2 }}>
          <div style={{ position: 'absolute', inset: 3, background: 'var(--color-hermes-cyan)', opacity: 0.85 }} />
        </div>
        <span style={{ fontWeight: 700, fontSize: 13, letterSpacing: '1.6px', color: 'var(--color-hermes-tx)' }}>
          TRUSTFORGE <span style={{ color: 'var(--color-hermes-cyan)' }}>HERMES</span>
        </span>
      </button>
      <span style={{ fontSize: 9, color: 'var(--color-hermes-tx3)', letterSpacing: 1 }}>✛ {systemId}</span>
      <span style={{ fontSize: 10, color: 'var(--color-hermes-tx3)', border: '1px solid var(--color-hermes-bd2)', borderRadius: 4, padding: '2px 7px' }}>{version}</span>
      <span className="hermes-uplink-status" title={degradedMessage || undefined} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, color: degradedMessage ? 'var(--color-hermes-amber)' : 'var(--color-hermes-cyan)', background: degradedMessage ? 'rgba(232,179,77,.13)' : 'rgba(77,216,224,.13)', border: `1px solid ${degradedMessage ? 'rgba(232,179,77,.4)' : 'rgba(77,216,224,.4)'}`, borderRadius: 4, padding: '2px 8px' }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: degradedMessage ? 'var(--color-hermes-amber)' : 'var(--color-hermes-cyan)', animation: 'hermes-pulse 1.8s infinite' }} />{degradedMessage ? t('degradedState') : t('liveUplink')}
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, color: 'var(--color-hermes-amber)', background: 'rgba(232,179,77,.13)', border: '1px solid rgba(232,179,77,.4)', borderRadius: 4, padding: '2px 8px' }}>
        <span style={{ width: 6, height: 6, transform: 'rotate(45deg)', background: 'var(--color-hermes-amber)', animation: 'hermes-pulse 2.4s infinite' }} />HERMES: {t('active')}
      </span>
      <nav className="hermes-topbar-nav" aria-label={t('navigation')} style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        {navItems.map((item) => (
          <button
            type="button"
            key={item.id}
            onClick={() => onModuleSelect?.(item.id)}
            aria-pressed={activeModule === item.id}
            style={{
              color: activeModule === item.id ? 'var(--color-hermes-cyan)' : 'var(--color-hermes-tx2)',
              fontSize: 9, letterSpacing: '.7px', textDecoration: 'none', padding: '4px 6px',
              border: 0, borderBottom: activeModule === item.id ? '1px solid var(--color-hermes-cyan)' : '1px solid transparent',
              background: 'transparent', fontFamily: 'inherit', cursor: 'pointer',
            }}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <div style={{ flex: 1 }} />
      <button type="button" className="hermes-ship-toggle" onClick={onToggleShip}>⬡ 艦體升級</button>
      <button type="button" aria-label={t('language')} onClick={() => setLocale(locale === 'zh-TW' ? 'en' : 'zh-TW')} style={{ background: 'transparent', border: '1px solid var(--color-hermes-bd2)', borderRadius: 4, color: 'var(--color-hermes-tx2)', fontFamily: 'inherit', fontSize: 9, padding: '3px 7px', cursor: 'pointer' }}>
        {locale === 'zh-TW' ? 'EN' : '繁中'}
      </button>
      <span style={{ fontSize: 10, color: 'var(--color-hermes-tx2)' }}>{t('costLedger')} <b style={{ color: 'var(--color-hermes-cyan)' }}>{costLedger === null ? '--' : `$${costLedger.toFixed(4)}`}</b></span>
    </div>
  )
}
