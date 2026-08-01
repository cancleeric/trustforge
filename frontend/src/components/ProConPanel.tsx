import type { CrossSourceSignal, Evidence, Insight } from '../lib/types'
import AnnotatedText from './AnnotatedText'
import CrossSourceSignalPanel from './CrossSourceSignalPanel'

function averageTrust(evidence: Evidence[], direction: 'bullish' | 'bearish'): string {
  const values = evidence
    .filter((item) => item.direction === direction)
    .map((item) => item.trust)
    .filter(Number.isFinite)
  if (values.length === 0) return '—'
  return (values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(2)
}

function ArgumentColumn({
  title,
  tone,
  items,
  trust,
  empty,
}: {
  title: string
  tone: string
  items: string[]
  trust: string
  empty: string
}) {
  return (
    <section className="hermes-clip rounded-lg border bg-tf-card p-4" style={{ borderColor: tone }}>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h4 className="text-sm font-semibold" style={{ color: tone }}>{title}</h4>
        <span className="tf-num rounded-full border border-tf-border px-2 py-0.5 text-xs text-tf-muted">
          Trust Avg {trust}
        </span>
      </div>
      {items.length ? (
        <ul className="space-y-2 text-sm text-tf-text2">
          {items.map((item, index) => (
            <li key={`${item}-${index}`} className="flex gap-2">
              <span aria-hidden="true" style={{ color: tone }}>●</span>
              <span><AnnotatedText text={item} /></span>
            </li>
          ))}
        </ul>
      ) : <p className="text-xs text-tf-muted">{empty}</p>}
    </section>
  )
}

export default function ProConPanel({
  facts,
  contrarian,
  evidence,
  signal,
  insights,
  compact = false,
}: {
  facts: string[]
  contrarian: string[]
  evidence: Evidence[]
  signal: CrossSourceSignal | null
  insights?: Insight[]
  compact?: boolean
}) {
  const unresolved = (insights ?? []).filter((item) => item.coverage === 'insufficient')
  const hasDivergence = signal?.type === 'divergence'

  return (
    <section aria-labelledby="pro-con-title" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-tf-link">L2 · 正反證據對照</p>
          <h3 id="pro-con-title" className="mt-1 text-base font-semibold text-tf-text">支持、反方與未決訊號</h3>
        </div>
        <span className="text-xs text-tf-muted">先看分歧，再決定是否深入</span>
      </div>
      <div className={`grid grid-cols-1 gap-3 ${compact ? '' : 'lg:grid-cols-2'}`}>
        <ArgumentColumn
          title="▲ 支持／偏多"
          tone="var(--color-tf-good)"
          items={facts}
          trust={averageTrust(evidence, 'bullish')}
          empty="目前沒有可列出的支持事實。"
        />
        <ArgumentColumn
          title="▼ 反方／偏空"
          tone="var(--color-tf-bad)"
          items={contrarian}
          trust={averageTrust(evidence, 'bearish')}
          empty="目前沒有可列出的反方訊號。"
        />
      </div>
      {(hasDivergence || unresolved.length > 0) && (
        <section className="hermes-clip rounded-lg border border-tf-warn bg-tf-card p-4" aria-label="矛盾與未決">
          <h4 className="mb-2 text-sm font-semibold text-tf-warn">矛盾／未決</h4>
          {hasDivergence && <CrossSourceSignalPanel signal={signal} />}
          {unresolved.length > 0 && (
            <ul className={`${hasDivergence ? 'mt-3' : ''} list-disc space-y-1 pl-5 text-xs text-tf-text2`}>
              {unresolved.map((item, index) => <li key={`${item.title}-${index}`}>{item.title}：{item.summary}</li>)}
            </ul>
          )}
        </section>
      )}
    </section>
  )
}
