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
  const [agentcoreStatus, setAgentcoreStatus] = useState<'checking' | 'connected' | 'disconnected'>('checking')
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

  // AgentCore connectivity check
  useEffect(() => {
    const checkAgentCore = async () => {
      try {
        const resp = await fetch('/api/agentcore/health', { signal: AbortSignal.timeout(3000) })
        setAgentcoreStatus(resp.ok ? 'connected' : 'disconnected')
      } catch {
        // Try direct agentcore dev endpoint
        try {
          const resp = await fetch('http://127.0.0.1:8080/invocations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: '' }),
            signal: AbortSignal.timeout(3000),
          })
          setAgentcoreStatus(resp.status !== 0 ? 'connected' : 'disconnected')
        } catch {
          setAgentcoreStatus('disconnected')
        }
      }
    }
    checkAgentCore()
    const interval = setInterval(checkAgentCore, 15000)
    return () => clearInterval(interval)
  }, [])

  return (
    <header
      className="app-header flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-tf-border bg-tf-card px-4 py-2.5 sm:px-6"
    >
      <Link to="/" className="inline-flex items-center gap-2 no-underline">
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

      <nav aria-label="主導覽" className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              `whitespace-nowrap rounded px-2 py-1 font-mono text-xs uppercase tracking-wider no-underline transition ${
                isActive ? 'font-semibold text-tf-link' : 'text-tf-muted hover:text-tf-text'
              }`
            }
            style={({ isActive }) => (isActive ? { textShadow: '0 0 8px rgba(77,216,224,.45)' } : {})}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      <span
        title="部署版本（release / git sha）"
        className="rounded border border-tf-muted/40 px-2 py-0.5 font-mono text-xs text-tf-muted"
      >{`${releaseVersion} · ${GIT_SHA}`}</span>
      <span
        title={`AgentCore: ${agentcoreStatus}`}
        className={`rounded px-2 py-0.5 font-mono text-xs ${
          agentcoreStatus === 'connected'
            ? 'border border-green-500/40 text-green-400'
            : agentcoreStatus === 'disconnected'
            ? 'border border-red-500/40 text-red-400'
            : 'border border-yellow-500/40 text-yellow-400'
        }`}
      >
        {agentcoreStatus === 'connected' ? '● AgentCore' : agentcoreStatus === 'disconnected' ? '○ AgentCore OFF' : '◐ Checking...'}
      </span>
      <button type="button" aria-label={t('language')} onClick={() => setLocale(locale === 'zh-TW' ? 'en' : 'zh-TW')} className="rounded border border-tf-border bg-transparent px-2 py-1 font-mono text-xs text-tf-muted hover:text-tf-text">
        {locale === 'zh-TW' ? 'EN' : '繁中'}
      </button>
      <ThemeToggle />
    </header>
  )
}
