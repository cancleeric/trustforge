import { useState } from 'react'
import { applyTheme, browserThemeEnv, persistTheme, resolveInitialTheme, type Theme } from '../lib/theme'

// 設定頁（/settings，R2 深色艦橋設計稿新頁）：目前後端沒有「使用者偏好」
// API，此頁刻意只做兩件誠實的事——(1) 主題切換是真的，串接既有
// `lib/theme.ts`，跟 Header 的 ThemeToggle 走同一套持久化邏輯；
// (2) 其餘通知/來源加權/連續引擎/API 連線僅為本頁 session 內互動展示
// （reload 即重置），不寫入任何後端，避免假裝有一個不存在的偏好儲存服務。
// 之後真要接後端，就是幫這些欄位補一個 PATCH /settings（ponytail: 之後接）。

type Category = '一般' | '通知' | '來源' | '連續引擎' | '外觀' | 'API 連線'
const CATEGORIES: Array<{ id: Category; icon: string }> = [
  { id: '一般', icon: '⚙' },
  { id: '通知', icon: '◔' },
  { id: '來源', icon: '⊞' },
  { id: '連續引擎', icon: '↻' },
  { id: '外觀', icon: '◐' },
  { id: 'API 連線', icon: '⚿' },
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
  const [active, setActive] = useState<Category>('通知')
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
        <p className="font-mono text-xs font-semibold uppercase tracking-[0.16em] text-tf-link">Control panel</p>
        <h1 className="mt-1 text-2xl font-bold text-tf-text">設定</h1>
        <p className="mt-1 text-sm text-tf-text2">操作者層級偏好；敏感金鑰不以明文顯示，僅存於本分頁。</p>
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
              {c.id}
            </button>
          ))}
          <div className="mt-2 hidden rounded-lg border border-tf-border bg-tf-card p-3 md:block">
            <p className="mb-2 text-[9.5px] uppercase tracking-[0.14em] text-tf-muted">狀態</p>
            <p className="flex items-center gap-1.5 text-[11px]" style={{ color: 'var(--color-tf-green)' }}>
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: 'var(--color-tf-green)' }} />
              本分頁 session 內有效
            </p>
          </div>
        </aside>

        <section className="flex flex-col gap-4">
          {active === '一般' && (
            <Card title="一般 · GENERAL" sub="TrustForge HERMES 操作台">
              <p className="text-xs text-tf-muted">目前無額外一般設定，其他分類請見左側清單。</p>
            </Card>
          )}

          {active === '通知' && (
            <Card title="通知 · NOTIFICATIONS" sub="CONTINUOUS ENGINE 事件推播">
              <Row label="信任分下降告警" help="分數跌破門檻時推播">
                <Toggle on={dropAlert} onClick={() => setDropAlert((v) => !v)} />
              </Row>
              <Row label="告警門檻" help={`信任分 < ${threshold}`}>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={threshold}
                  onChange={(e) => setThreshold(Number(e.target.value))}
                  className="w-40 accent-tf-warn"
                  aria-label="告警門檻"
                />
              </Row>
              <Row label="來源缺席提醒" help="連接器超過 5 分鐘無回應">
                <Toggle on={absenceAlert} onClick={() => setAbsenceAlert((v) => !v)} />
              </Row>
              <Row label="操縱偵測推播" help="wash-trading / spoofing 訊號">
                <Toggle on={manipAlert} onClick={() => setManipAlert((v) => !v)} />
              </Row>
            </Card>
          )}

          {active === '來源' && (
            <Card title="來源 · SOURCES" sub="啟用來源與加權">
              <div className="mb-4 flex flex-col gap-2.5">
                {sources.map((s, i) => (
                  <div key={s.name} className="grid grid-cols-[1fr_auto_auto] items-center gap-4 rounded-lg border border-tf-border bg-tf-well px-3.5 py-2.5">
                    <span className="text-xs" style={{ color: s.on ? 'var(--color-tf-text)' : 'var(--color-tf-muted)' }}>{s.name}</span>
                    <div className="flex w-[150px] items-center gap-2.5">
                      <span className="text-[9.5px] text-tf-muted">權重</span>
                      <div className="h-1 flex-1 overflow-hidden rounded-full bg-tf-border">
                        <div className="h-full" style={{ width: `${s.weight}%`, background: weightColor(s.weight) }} />
                      </div>
                      <span className="tf-num w-6 text-right text-[11px] text-tf-text">{s.weight}</span>
                    </div>
                    <Toggle on={s.on} onClick={() => setSources((prev) => prev.map((x, j) => (j === i ? { ...x, on: !x.on } : x)))} />
                  </div>
                ))}
              </div>
              <Row label="分歧門檻" help="超過此值即觸發分歧告警">
                <div className="flex w-[220px] items-center gap-3">
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={divergenceThreshold}
                    onChange={(e) => setDivergenceThreshold(Number(e.target.value))}
                    className="flex-1 accent-tf-warn"
                    aria-label="分歧門檻"
                  />
                  <span className="tf-num w-10 text-xs font-semibold" style={{ color: 'var(--color-tf-warn)' }}>{divergenceThreshold}%</span>
                </div>
              </Row>
            </Card>
          )}

          {active === '連續引擎' && (
            <Card title="連續引擎 · CONTINUOUS ENGINE" sub="背景重新評分排程">
              <Row label="連續掃描" help="背景持續重新評分">
                <div className="flex items-center gap-2.5">
                  {continuousScan && <span className="text-[11px]" style={{ color: 'var(--color-tf-green)' }}>running</span>}
                  <Toggle on={continuousScan} onClick={() => setContinuousScan((v) => !v)} accent="var(--color-tf-green)" />
                </div>
              </Row>
              <Row label="掃描間隔" help="兩輪掃描之間隔">
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
              <Row label="並行度" help="同時掃描的來源數">
                <div className="flex items-center gap-2.5">
                  <button type="button" onClick={() => setConcurrency((v) => Math.max(1, v - 1))} className="flex h-[26px] w-[26px] items-center justify-center rounded-md border border-tf-border text-tf-link">−</button>
                  <span className="tf-num w-5 text-center text-sm font-semibold text-tf-text">{concurrency}</span>
                  <button type="button" onClick={() => setConcurrency((v) => Math.min(16, v + 1))} className="flex h-[26px] w-[26px] items-center justify-center rounded-md border border-tf-border text-tf-link">+</button>
                </div>
              </Row>
            </Card>
          )}

          {active === '外觀' && (
            <Card title="外觀 · APPEARANCE" sub="介面主題與密度">
              <Row label="主題" help="介面配色（與右上角主題切換同步）">
                <div className="flex overflow-hidden rounded-md border border-tf-border">
                  {([['深色', 'dark'], ['淺色', 'light']] as const).map(([label, t]) => (
                    <button
                      key={t}
                      type="button"
                      onClick={() => chooseTheme(t)}
                      className="border-r border-tf-border px-3 py-1.5 text-[11.5px] last:border-r-0"
                      style={theme === t ? { background: 'var(--color-tf-accent)', color: 'var(--color-tf-bg)', fontWeight: 600 } : { background: 'var(--color-tf-well)', color: 'var(--color-tf-muted)' }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </Row>
              <Row label="語言" help="介面語言（切換見右上角 EN/繁中）">
                <span className="rounded-md border border-tf-border bg-tf-well px-3 py-1.5 text-xs text-tf-text2">繁體中文</span>
              </Row>
            </Card>
          )}

          {active === 'API 連線' && (
            <Card title="API 連線 · CONNECTIONS" sub="金鑰不以明文顯示">
              <Row label="TRUSTFORGE_ADMIN_TOKEN" help="只存本分頁 · 關閉即清除">
                <div className="flex items-center gap-2.5">
                  <span className="rounded-md border border-tf-border bg-tf-well px-3 py-2 text-[13px] tracking-[0.24em] text-tf-muted">⚿ ••••••••</span>
                  <button type="button" className="rounded-md border border-tf-border px-3.5 py-2 text-xs text-tf-link" style={{ background: 'color-mix(in srgb, var(--color-tf-accent) 8%, transparent)' }}>更新</button>
                </div>
              </Row>
              <Row label="Cache 後端" help="readonly">
                <span className="inline-flex items-center gap-2 text-xs text-tf-text">
                  DynamoDBCache
                  <span className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[10.5px]" style={{ color: 'var(--color-tf-green)', borderColor: 'color-mix(in srgb, var(--color-tf-green) 40%, transparent)', background: 'color-mix(in srgb, var(--color-tf-green) 12%, transparent)' }}>
                    <span className="h-1 w-1 rounded-full" style={{ background: 'var(--color-tf-green)' }} />正常
                  </span>
                </span>
              </Row>
              <Row label="LLM runtime" help="readonly">
                <span className="inline-flex items-center gap-2 text-xs text-tf-muted">
                  Bedrock
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-tf-border bg-tf-well px-2.5 py-0.5 text-[10.5px] text-tf-muted">
                    <span className="h-1 w-1 rounded-full bg-tf-muted" />未啟用
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
          變更僅保留於本分頁（尚無後端偏好儲存）
        </span>
      </div>
    </main>
  )
}
