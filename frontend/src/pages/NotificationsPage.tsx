import { useMemo, useState } from 'react'
import GlossaryTerm from '../components/GlossaryTerm'

// 通知中心（/notifications，R2 深色艦橋設計稿新頁）：目前後端無通知 API，
// 故沿用設計稿的示範資料作為靜態展示、僅本機互動（已讀/篩選），
// 不宣稱是即時資料——比照 ponytail 原則，先求「頁面存在且可用」，
// 真正接上後端事件流是之後的獨立任務（ponytail: 之後接後端 SSE/polling）。

type Severity = 'red' | 'gold' | 'green' | 'grey'

const SEV: Record<Severity, { accent: string; iconBg: string; iconBorder: string }> = {
  red: { accent: 'var(--color-tf-bad)', iconBg: 'color-mix(in srgb, var(--color-tf-bad) 12%, transparent)', iconBorder: 'color-mix(in srgb, var(--color-tf-bad) 40%, transparent)' },
  gold: { accent: 'var(--color-tf-warn)', iconBg: 'color-mix(in srgb, var(--color-tf-warn) 12%, transparent)', iconBorder: 'color-mix(in srgb, var(--color-tf-warn) 40%, transparent)' },
  green: { accent: 'var(--color-tf-green)', iconBg: 'color-mix(in srgb, var(--color-tf-green) 12%, transparent)', iconBorder: 'color-mix(in srgb, var(--color-tf-green) 40%, transparent)' },
  grey: { accent: 'var(--color-tf-muted)', iconBg: 'var(--color-tf-well)', iconBorder: 'var(--color-tf-border)' },
}

type Notif = { id: string; sev: Severity; icon: string; title: React.ReactNode; detail: string; time: string; unread: boolean; category: '告警' | '系統' }

const INITIAL_NOTIFS: Notif[] = [
  { id: 'n1', sev: 'red', icon: '▼', title: 'BTC 信任分下降 62 → 59', detail: '跨來源分歧升高，情緒類來源不一致。', time: '2 分鐘前', unread: true, category: '告警' },
  { id: 'n2', sev: 'gold', icon: '⚠', title: '來源 cryptopanic (BTC) 已缺席 8 分鐘', detail: '連續引擎將暫時降低該來源加權。', time: '8 分鐘前', unread: true, category: '告警' },
  { id: 'n3', sev: 'red', icon: '◈', title: <>偵測到 ETH <GlossaryTerm term="washTrading" label="wash-trading" compact /> 疑慮訊號</>, detail: '2 個交易所出現異常對敲量能。', time: '15 分鐘前', unread: true, category: '告警' },
  { id: 'n4', sev: 'green', icon: '✓', title: 'SOL 分析完成 · 信任分 51', detail: '低信任 · 建議交叉確認來源。', time: '21 分鐘前', unread: true, category: '系統' },
  { id: 'n5', sev: 'gold', icon: '△', title: '跨來源分歧 35% 超過門檻', detail: 'BTC 成交量在交易所間差異擴大。', time: '30 分鐘前', unread: false, category: '告警' },
  { id: 'n6', sev: 'grey', icon: '↻', title: '連續引擎重啟 · 佇列已清空', detail: '系統維運事件，無需處理。', time: '1 小時前', unread: false, category: '系統' },
]

const FILTERS: Array<'全部' | '告警' | '系統'> = ['全部', '告警', '系統']

export default function NotificationsPage() {
  const [notifs, setNotifs] = useState(INITIAL_NOTIFS)
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('全部')

  const visible = useMemo(
    () => (filter === '全部' ? notifs : notifs.filter((n) => n.category === filter)),
    [notifs, filter],
  )
  const unreadCount = notifs.filter((n) => n.unread).length

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-5 px-4 py-6 sm:px-6" style={{ background: 'radial-gradient(ellipse at 50% 0%,var(--color-tf-bg-hero) 0%,var(--color-tf-bg) 72%)', minHeight: 'calc(100vh - 57px)' }}>
      <div className="border-b border-tf-border pb-4">
        <p className="font-mono text-xs font-semibold uppercase text-tf-link">Continuous engine · live</p>
        <h1 className="mt-1 text-2xl font-bold text-tf-text">通知中心</h1>
        <p className="mt-1 text-sm text-tf-text2">信任分變化、來源異常與系統事件；{unreadCount} 則未讀。</p>
      </div>

      <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card">
        <div className="flex items-center justify-between border-b border-tf-border px-4 py-3">
          <div className="flex gap-2">
            {FILTERS.map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className="rounded-[6px] border px-3.5 py-1 text-xs"
                style={filter === f
                  ? { color: 'var(--color-tf-accent)', background: 'color-mix(in srgb, var(--color-tf-accent) 10%, transparent)', borderColor: 'var(--color-tf-accent)' }
                  : { color: 'var(--color-tf-muted)', background: 'var(--color-tf-well)', borderColor: 'var(--color-tf-border)' }}
              >
                {f}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => setNotifs((prev) => prev.map((n) => ({ ...n, unread: false })))}
            className="text-xs text-tf-muted hover:text-tf-text"
          >
            全部標記已讀
          </button>
        </div>

        <ul>
          {visible.map((n) => {
            const tone = SEV[n.sev]
            return (
              <li
                key={n.id}
                className="flex gap-3 border-b border-tf-border px-4 py-3.5 last:border-b-0"
                style={{ background: n.unread ? 'color-mix(in srgb, var(--color-tf-hover) 55%, transparent)' : 'transparent' }}
              >
                <span
                  className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-[7px] border text-xs"
                  style={{ background: tone.iconBg, borderColor: tone.iconBorder, color: tone.accent }}
                >
                  {n.icon}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex items-start justify-between gap-2">
                    <span className={`text-[13px] leading-snug text-tf-text ${n.unread ? 'font-semibold' : ''}`}>{n.title}</span>
                    {n.unread && <span className="mt-1 h-1.5 w-1.5 flex-shrink-0 rounded-full" style={{ background: tone.accent, boxShadow: `0 0 6px ${tone.accent}` }} />}
                  </div>
                  <p className="mb-1.5 text-xs leading-relaxed text-tf-muted">{n.detail}</p>
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] text-tf-muted/70">{n.time}</span>
                    {n.unread && (
                      <button
                        type="button"
                        onClick={() => setNotifs((prev) => prev.map((x) => (x.id === n.id ? { ...x, unread: false } : x)))}
                        className="text-[10.5px] text-tf-link"
                      >
                        標記已讀
                      </button>
                    )}
                  </div>
                </div>
              </li>
            )
          })}
          {visible.length === 0 && <li className="px-4 py-8 text-center text-sm text-tf-muted">此分類目前沒有通知。</li>}
        </ul>
      </div>
    </main>
  )
}
