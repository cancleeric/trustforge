import { useState } from 'react'
import { applyTheme, browserThemeEnv, persistTheme, resolveInitialTheme, type Theme } from '../lib/theme'
import { useHermesI18n, type MessageKey } from '../hermes/hermesI18n'

// 設定頁（/settings，R2 深色艦橋設計稿新頁）：目前後端沒有「使用者偏好」
// API，此頁刻意只做兩件誠實的事——(1) 主題切換是真的，串接既有
// `lib/theme.ts`，跟 Header 的 ThemeToggle 走同一套持久化邏輯；
// (2) 其餘通知/來源加權/連續引擎/API 連線僅為本頁 session 內互動展示
// （reload 即重置），不寫入任何後端，避免假裝有一個不存在的偏好儲存服務。
// 之後真要接後端，就是幫這些欄位補一個 PATCH /settings（ponytail: 之後接）。

type Category = 'general' | 'notifications' | 'sources' | 'continuous' | 'appearance' | 'api'
const CATEGORIES: Array<{ id: Category; icon: string; labelKey: MessageKey }> = [
  { id: 'general', icon: '⚙', labelKey: 'setCatGeneral' },
  { id: 'notifications', icon: '◔', labelKey: 'setCatNotifications' },
  { id: 'sources', icon: '⊞', labelKey: 'setCatSources' },
  { id: 'continuous', icon: '↻', labelKey: 'setCatContinuous' },
  { id: 'appearance', icon: '◐', labelKey: 'setCatAppearance' },
  { id: 'api', icon: '⚿', labelKey: 'setCatApi' },
]

function Toggle({ on, onClick, accent = 'var(--color-tf-accent)' }: { on: boolean; onClick: () => void; accent?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={onClick}
      className="relative inline-block h-[22px] w-[42px] rounded-full transition-colors"
      style={{ background: on ? accent : 'var(--color-tf-border)' }}
    >
      <span
        className="absolute top-[3px] h-4 w-4 rounded-full transition-all"
        style={{ [on ? 'right' : 'left']: '3px', background: on ? 'var(--color-tf-bg)' : 'var(--color-tf-muted)' } as React.CSSProperties}
      />
    </button>
  )
}

function Card({ title, sub, children }: { title: string; sub: string; children: React.ReactNode }) {
  return (
    <div className="hermes-clip rounded-xl border border-tf-border bg-tf-card p-5">
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-[0.2em] text-tf-link">{title}</p>
      <p className="mb-3 text-[10.5px] text-tf-muted">{sub}</p>
      {children}
    </div>
  )
}

function Row({ label, help, children }: { label: string; help: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-5 border-b border-tf-border/60 py-3.5 last:border-b-0">
      <div className="min-w-0">
        <p className="mb-0.5 text-[12.5px] text-tf-text">{label}</p>
        <p className="text-[10.5px] text-tf-muted">{help}</p>
      </div>
      <div className="flex-shrink-0">{children}</div>
    </div>
  )
}

export default function SettingsPage() {
  const { t } = useHermesI18n()
  const [active, setActive] = useState<Category>('notifications')
  const [theme, setTheme] = useState<Theme>(() => resolveInitialTheme(browserThemeEnv()))

  const [dropAlert, setDropAlert] = useState(true)
  const [absenceAlert, setAbsenceAlert] = useState(true)
  const [manipAlert, setManipAlert] = useState(false)
  const [threshold, setThreshold] = useState(60)

  const [sources, setSources] = useState([
    { name: 'coingecko', weight: 92, on: true },
    { name: 'coingecko-sentiment', weight: 58, on: true },
    { name: 'cryptopanic', weight: 64, on: true },
    { name: 'binance', weight: 88, on: true },
    { name: 'kaiko', weight: 41, on: false },
  ])
  const [divergenceThreshold, setDivergenceThreshold] = useState(35)

  const [continuousScan, setContinuousScan] = useState(true)
  const [interval, setInterval_] = useState<'1m' | '5m' | '15m'>('5m')
  const [concurrency, setConcurrency] = useState(4)

  const weightColor = (w: number) => (w >= 80 ? 'var(--color-tf-green)' : w >= 55 ? 'var(--color-tf-warn)' : 'var(--color-tf-bad)')

  function chooseTheme(t: Theme) {
    setTheme(t)
    applyTheme(t)
    persistTheme(t)
  }

  return (
    <main
      className="mx-auto max-w-5xl px-4 py-6 sm:px-6"
      style={{ background: 'radial-gradient(ellipse at 50% 0%,var(--color-tf-bg-hero) 0%,var(--color-tf-bg) 72%)', minHeight: 'calc(100vh - 57px)' }}
    >
      <div className="mb-5 border-b border-tf-border pb-4">
        <p className="font-mono text-xs font-semibold uppercase tracking-[0.16em] text-tf-link">{t('setKicker')}</p>
        <h1 className="mt-1 text-2xl font-bold text-tf-text">{t('setTitle')}</h1>
        <p className="mt-1 text-sm text-tf-text2">{t('setDesc')}</p>
      </div>

      <div className="grid grid-cols-1 gap-5 md:grid-cols-[200px_1fr]">
        <aside className="flex flex-row gap-1.5 overflow-x-auto md:flex-col md:gap-1.5">
          {CATEGORIES.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => setActive(c.id)}
              className="flex flex-shrink-0 items-center gap-2.5 rounded-lg border px-3.5 py-2.5 text-left text-xs"
              style={active === c.id
                ? { background: 'color-mix(in srgb, var(--color-tf-accent) 10%, transparent)', borderColor: 'var(--color-tf-accent)', color: 'var(--color-tf-text)' }
                : { background: 'var(--color-tf-panel)', borderColor: 'var(--color-tf-border)', color: 'var(--color-tf-muted)' }}
            >
              <span
                className="flex h-[22px] w-[22px] flex-shrink-0 items-center justify-center rounded-md border text-xs"
                style={{ background: 'var(--color-tf-well)', borderColor: active === c.id ? 'var(--color-tf-accent)' : 'var(--color-tf-border)', color: active === c.id ? 'var(--color-tf-accent)' : 'var(--color-tf-muted)' }}
              >
                {c.icon}
              </span>
              {t(c.labelKey)}
            </button>
          ))}
          <div className="mt-2 hidden rounded-lg border border-tf-border bg-tf-card p-3 md:block">
            <p className="mb-2 text-[9.5px] uppercase tracking-[0.14em] text-tf-muted">{t('setStatusLabel')}</p>
            <p className="flex items-center gap-1.5 text-[11px]" style={{ color: 'var(--color-tf-green)' }}>
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: 'var(--color-tf-green)' }} />
              {t('setStatusValue')}
            </p>
          </div>
        </aside>

        <section className="flex flex-col gap-4">
          {active === 'general' && (
            <Card title={t('setGeneralTitle')} sub={t('setGeneralSub')}>
              <p className="text-xs text-tf-muted">{t('setGeneralBody')}</p>
            </Card>
          )}

          {active === 'notifications' && (
            <Card title={t('setNotifTitle')} sub={t('setNotifSub')}>
              <Row label={t('setDropAlertLabel')} help={t('setDropAlertHelp')}>
                <Toggle on={dropAlert} onClick={() => setDropAlert((v) => !v)} />
              </Row>
              <Row label={t('setThresholdLabel')} help={t('setThresholdHelpTemplate', { threshold })}>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={threshold}
                  onChange={(e) => setThreshold(Number(e.target.value))}
                  className="w-40 accent-tf-warn"
                  aria-label={t('setThresholdLabel')}
                />
              </Row>
              <Row label={t('setAbsenceAlertLabel')} help={t('setAbsenceAlertHelp')}>
                <Toggle on={absenceAlert} onClick={() => setAbsenceAlert((v) => !v)} />
              </Row>
              <Row label={t('setManipAlertLabel')} help={t('setManipAlertHelp')}>
                <Toggle on={manipAlert} onClick={() => setManipAlert((v) => !v)} />
              </Row>
            </Card>
          )}

          {active === 'sources' && (
            <Card title={t('setSourcesTitle')} sub={t('setSourcesSub')}>
              <div className="mb-4 flex flex-col gap-2.5">
                {sources.map((s, i) => (
                  <div key={s.name} className="grid grid-cols-[1fr_auto_auto] items-center gap-4 rounded-lg border border-tf-border bg-tf-well px-3.5 py-2.5">
                    <span className="text-xs" style={{ color: s.on ? 'var(--color-tf-text)' : 'var(--color-tf-muted)' }}>{s.name}</span>
                    <div className="flex w-[150px] items-center gap-2.5">
                      <span className="text-[9.5px] text-tf-muted">{t('setWeightLabel')}</span>
                      <div className="h-1 flex-1 overflow-hidden rounded-full bg-tf-border">
                        <div className="h-full" style={{ width: `${s.weight}%`, background: weightColor(s.weight) }} />
                      </div>
                      <span className="tf-num w-6 text-right text-[11px] text-tf-text">{s.weight}</span>
                    </div>
                    <Toggle on={s.on} onClick={() => setSources((prev) => prev.map((x, j) => (j === i ? { ...x, on: !x.on } : x)))} />
                  </div>
                ))}
              </div>
              <Row label={t('setDivergenceLabel')} help={t('setDivergenceHelp')}>
                <div className="flex w-[220px] items-center gap-3">
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={divergenceThreshold}
                    onChange={(e) => setDivergenceThreshold(Number(e.target.value))}
                    className="flex-1 accent-tf-warn"
                    aria-label={t('setDivergenceLabel')}
                  />
                  <span className="tf-num w-10 text-xs font-semibold" style={{ color: 'var(--color-tf-warn)' }}>{divergenceThreshold}%</span>
                </div>
              </Row>
            </Card>
          )}

          {active === 'continuous' && (
            <Card title={t('setContinuousTitle')} sub={t('setContinuousSub')}>
              <Row label={t('setScanLabel')} help={t('setScanHelp')}>
                <div className="flex items-center gap-2.5">
                  {continuousScan && <span className="text-[11px]" style={{ color: 'var(--color-tf-green)' }}>{t('setRunning')}</span>}
                  <Toggle on={continuousScan} onClick={() => setContinuousScan((v) => !v)} accent="var(--color-tf-green)" />
                </div>
              </Row>
              <Row label={t('setIntervalLabel')} help={t('setIntervalHelp')}>
                <div className="flex overflow-hidden rounded-md border border-tf-border">
                  {(['1m', '5m', '15m'] as const).map((opt) => (
                    <button
                      key={opt}
                      type="button"
                      onClick={() => setInterval_(opt)}
                      className="border-r border-tf-border px-3 py-1.5 text-[11.5px] last:border-r-0"
                      style={interval === opt ? { background: 'var(--color-tf-accent)', color: 'var(--color-tf-bg)', fontWeight: 600 } : { background: 'var(--color-tf-well)', color: 'var(--color-tf-muted)' }}
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              </Row>
              <Row label={t('setConcurrencyLabel')} help={t('setConcurrencyHelp')}>
                <div className="flex items-center gap-2.5">
                  <button type="button" onClick={() => setConcurrency((v) => Math.max(1, v - 1))} className="flex h-[26px] w-[26px] items-center justify-center rounded-md border border-tf-border text-tf-link">−</button>
                  <span className="tf-num w-5 text-center text-sm font-semibold text-tf-text">{concurrency}</span>
                  <button type="button" onClick={() => setConcurrency((v) => Math.min(16, v + 1))} className="flex h-[26px] w-[26px] items-center justify-center rounded-md border border-tf-border text-tf-link">+</button>
                </div>
              </Row>
            </Card>
          )}

          {active === 'appearance' && (
            <Card title={t('setAppearanceTitle')} sub={t('setAppearanceSub')}>
              <Row label={t('setThemeLabel')} help={t('setThemeHelp')}>
                <div className="flex overflow-hidden rounded-md border border-tf-border">
                  {([[t('setThemeDark'), 'dark'], [t('setThemeLight'), 'light']] as const).map(([label, th]) => (
                    <button
                      key={th}
                      type="button"
                      onClick={() => chooseTheme(th)}
                      className="border-r border-tf-border px-3 py-1.5 text-[11.5px] last:border-r-0"
                      style={theme === th ? { background: 'var(--color-tf-accent)', color: 'var(--color-tf-bg)', fontWeight: 600 } : { background: 'var(--color-tf-well)', color: 'var(--color-tf-muted)' }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </Row>
              <Row label={t('setLanguageLabel')} help={t('setLanguageHelp')}>
                <span className="rounded-md border border-tf-border bg-tf-well px-3 py-1.5 text-xs text-tf-text2">{t('setLanguageValue')}</span>
              </Row>
            </Card>
          )}

          {active === 'api' && (
            <Card title={t('setApiTitle')} sub={t('setApiSub')}>
              <Row label={t('setTokenLabel')} help={t('setTokenHelp')}>
                <div className="flex items-center gap-2.5">
                  <span className="rounded-md border border-tf-border bg-tf-well px-3 py-2 text-[13px] tracking-[0.24em] text-tf-muted">⚿ ••••••••</span>
                  <button type="button" className="rounded-md border border-tf-border px-3.5 py-2 text-xs text-tf-link" style={{ background: 'color-mix(in srgb, var(--color-tf-accent) 8%, transparent)' }}>{t('setTokenUpdate')}</button>
                </div>
              </Row>
              <Row label={t('setCacheLabel')} help={t('setCacheHelp')}>
                <span className="inline-flex items-center gap-2 text-xs text-tf-text">
                  DynamoDBCache
                  <span className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[10.5px]" style={{ color: 'var(--color-tf-green)', borderColor: 'color-mix(in srgb, var(--color-tf-green) 40%, transparent)', background: 'color-mix(in srgb, var(--color-tf-green) 12%, transparent)' }}>
                    <span className="h-1 w-1 rounded-full" style={{ background: 'var(--color-tf-green)' }} />{t('setCacheNormal')}
                  </span>
                </span>
              </Row>
              <Row label={t('setLlmLabel')} help={t('setLlmHelp')}>
                <span className="inline-flex items-center gap-2 text-xs text-tf-muted">
                  Bedrock
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-tf-border bg-tf-well px-2.5 py-0.5 text-[10.5px] text-tf-muted">
                    <span className="h-1 w-1 rounded-full bg-tf-muted" />{t('setLlmDisabled')}
                  </span>
                </span>
              </Row>
            </Card>
          )}
        </section>
      </div>

      <div className="mt-4 flex items-center justify-between rounded-lg border border-tf-border bg-tf-panel px-6 py-3.5">
        <span className="inline-flex items-center gap-2 text-xs" style={{ color: 'var(--color-tf-green)' }}>
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: 'var(--color-tf-green)' }} />
          {t('setFooter')}
        </span>
      </div>
    </main>
  )
}
