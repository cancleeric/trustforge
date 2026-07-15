import { NavLink } from 'react-router-dom'
import { useHermesI18n } from './hermesI18n'

interface HermesTopBarProps {
  costLedger?: number | null
  version?: string
  systemId?: string
}

export default function HermesTopBar({
  costLedger = null,
  version = 'loading · GALAXY',
  systemId = 'SYS·HRM-01',
}: HermesTopBarProps) {
  const { locale, setLocale, t } = useHermesI18n()
  const navItems = [
    { to: '/analyze', label: t('analyze') }, { to: '/compare', label: t('compare') },
    { to: '/history', label: t('history') }, { to: '/status', label: t('sources') }, { to: '/costs', label: t('costs') },
  ]
  return (
    <div
      className="hermes-topbar"
      style={{
        position: 'absolute', left: 0, top: 0, width: 1440, height: 44, zIndex: 10,
        display: 'flex', alignItems: 'center', gap: 14, padding: '0 20px',
        background: 'rgba(10,16,24,.62)', backdropFilter: 'blur(10px)',
        borderBottom: '1px solid var(--color-hermes-bd)',
        boxShadow: '0 1px 12px rgba(77,216,224,.08)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
        <div style={{ width: 16, height: 16, position: 'relative', transform: 'rotate(45deg)', border: '1.5px solid var(--color-hermes-cyan)', borderRadius: 2 }}>
          <div style={{ position: 'absolute', inset: 3, background: 'var(--color-hermes-cyan)', opacity: 0.85 }} />
        </div>
        <span style={{ fontWeight: 700, fontSize: 13, letterSpacing: '1.6px', color: 'var(--color-hermes-tx)' }}>
          TRUSTFORGE <span style={{ color: 'var(--color-hermes-cyan)' }}>HERMES</span>
        </span>
      </div>
      <span style={{ fontSize: 9, color: 'var(--color-hermes-tx3)', letterSpacing: 1 }}>✛ {systemId}</span>
      <span style={{ fontSize: 10, color: 'var(--color-hermes-tx3)', border: '1px solid var(--color-hermes-bd2)', borderRadius: 4, padding: '2px 7px' }}>{version}</span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, color: 'var(--color-hermes-cyan)', background: 'rgba(77,216,224,.13)', border: '1px solid rgba(77,216,224,.4)', borderRadius: 4, padding: '2px 8px' }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--color-hermes-cyan)', animation: 'hermes-pulse 1.8s infinite' }} />{t('liveUplink')}
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, color: 'var(--color-hermes-amber)', background: 'rgba(232,179,77,.13)', border: '1px solid rgba(232,179,77,.4)', borderRadius: 4, padding: '2px 8px' }}>
        <span style={{ width: 6, height: 6, transform: 'rotate(45deg)', background: 'var(--color-hermes-amber)', animation: 'hermes-pulse 2.4s infinite' }} />HERMES: {t('active')}
      </span>
      <nav className="hermes-topbar-nav" aria-label={t('navigation')} style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            style={({ isActive }) => ({
              color: isActive ? 'var(--color-hermes-cyan)' : 'var(--color-hermes-tx2)',
              fontSize: 9, letterSpacing: '.7px', textDecoration: 'none', padding: '4px 6px',
              borderBottom: isActive ? '1px solid var(--color-hermes-cyan)' : '1px solid transparent',
            })}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div style={{ flex: 1 }} />
      <button type="button" aria-label={t('language')} onClick={() => setLocale(locale === 'zh-TW' ? 'en' : 'zh-TW')} style={{ background: 'transparent', border: '1px solid var(--color-hermes-bd2)', borderRadius: 4, color: 'var(--color-hermes-tx2)', fontFamily: 'inherit', fontSize: 9, padding: '3px 7px', cursor: 'pointer' }}>
        {locale === 'zh-TW' ? 'EN' : '繁中'}
      </button>
      <span style={{ fontSize: 10, color: 'var(--color-hermes-tx2)' }}>{t('costLedger')} <b style={{ color: 'var(--color-hermes-cyan)' }}>{costLedger === null ? t('syncing') : `$${costLedger.toFixed(4)}`}</b></span>
    </div>
  )
}
