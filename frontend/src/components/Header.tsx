import { useEffect, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import ThemeToggle from './ThemeToggle'
import { getHealth } from '../lib/endpoints'
import { useHermesI18n } from '../hermes/hermesI18n'

// build 時由 CD workflow 注入（VITE_GIT_SHA，見 .github/workflows/deploy-frontend.yml），
// 讓「線上 bundle 對應哪個 commit」可在畫面上直接確認；本機開發未設時 fallback 'dev'。
const GIT_SHA = (import.meta.env.VITE_GIT_SHA || 'dev').slice(0, 7)
const BUILD_VERSION = import.meta.env.VITE_RELEASE_VERSION || 'build'

export default function Header() {
  const [releaseVersion, setReleaseVersion] = useState(BUILD_VERSION)
  const { locale, setLocale, t } = useHermesI18n()
  const navItems = [
    { to: '/', label: 'HERMES' }, { to: '/analyze', label: t('analyze') },
    { to: '/compare', label: t('compare') }, { to: '/history', label: t('history') },
    { to: '/status', label: t('sources') }, { to: '/costs', label: t('costs') },
  ]

  useEffect(() => {
    const controller = new AbortController()
    void getHealth(controller.signal).then((response) => {
      if (response.ok) setReleaseVersion(response.data.version)
    }).catch(() => {
      // Keep the build-time value visible if the health endpoint is briefly unavailable.
    })
    return () => controller.abort()
  }, [])

  return (
    <header
      className="app-header flex min-h-[52px] flex-wrap items-stretch gap-x-4 gap-y-2 border-b border-tf-border bg-tf-card px-4 py-2 sm:px-6"
    >
      <Link to="/" className="inline-flex items-center gap-2 self-center no-underline">
        <span
          style={{
            width: 16, height: 16, position: 'relative', transform: 'rotate(45deg)',
            border: '1.5px solid var(--color-tf-link)', borderRadius: 2, display: 'inline-block',
          }}
        >
          <span style={{ position: 'absolute', inset: 3, background: 'var(--color-tf-link)', opacity: 0.85 }} />
        </span>
        <span className="font-mono text-sm font-bold tracking-[1.6px] text-tf-text">
          TRUSTFORGE <span style={{ color: 'var(--color-tf-link)' }}>HERMES</span>
        </span>
      </Link>

      <nav aria-label="主導覽" className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              `relative flex h-full items-center whitespace-nowrap rounded px-2 font-mono text-xs uppercase tracking-wider no-underline transition ${
                isActive ? 'font-semibold text-tf-link' : 'text-tf-muted hover:text-tf-text'
              }`
            }
            style={({ isActive }) => (isActive ? { textShadow: '0 0 8px rgba(77,216,224,.45)' } : {})}
          >
            {({ isActive }) => (
              <>
                {item.label}
                {/* 錨在 nav 的底部（自身 h-full 撐滿 header 高度），不是靠
                    padding-bottom 撐出間距——設計稿要求的 5 個核心頁籤才有
                    這條線，Admin/Settings 不在 navItems 裡故天生沒有。 */}
                {isActive && <span className="absolute inset-x-1 bottom-0 h-[2px] bg-tf-link" aria-hidden="true" />}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <span
        title="部署版本（release / git sha）"
        className="self-center rounded border border-tf-muted/40 px-2 py-0.5 font-mono text-xs text-tf-muted"
      >{`${releaseVersion} · ${GIT_SHA}`}</span>
      <button type="button" aria-label={t('language')} onClick={() => setLocale(locale === 'zh-TW' ? 'en' : 'zh-TW')} className="self-center rounded border border-tf-border bg-transparent px-2 py-1 font-mono text-xs text-tf-muted hover:text-tf-text">
        {locale === 'zh-TW' ? 'EN' : '繁中'}
      </button>
      <Link to="/asset-context" className="self-center font-mono text-xs text-tf-muted no-underline hover:text-tf-text">
        資產脈絡查詢
      </Link>
      <Link to="/eco-link" className="self-center font-mono text-xs text-tf-muted no-underline hover:text-tf-text">
        EcoLink
      </Link>
      <Link to="/peer-metrics" className="self-center font-mono text-xs text-tf-muted no-underline hover:text-tf-text">
        Peer 比較
      </Link>
      <Link to="/help" className="self-center font-mono text-xs text-tf-muted no-underline hover:text-tf-text">
        ? {t('help')}
      </Link>
      <span className="self-center">
        <ThemeToggle />
      </span>
    </header>
  )
}
