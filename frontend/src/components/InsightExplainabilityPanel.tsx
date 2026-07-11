import { useState } from 'react'
import type { Insight, InsightContribution } from '../lib/types'

function directionColor(direction: string): string {
  if (direction === 'bullish') return 'var(--color-tf-good)'
  if (direction === 'bearish') return 'var(--color-tf-bad)'
  return 'var(--color-tf-muted)'
}

function directionLabel(direction: string): string {
  if (direction === 'bullish') return '▲ 偏多'
  if (direction === 'bearish') return '▼ 偏空'
  if (direction === 'ambiguous') return '⇄ 矛盾/無法判定'
  return '— 中性'
}

function ContributionRow({ c }: { c: InsightContribution }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="rounded border border-tf-border p-2" style={{ borderLeft: `3px solid ${directionColor(c.direction)}` }}>
      <button
        type="button"
        className="flex w-full items-center justify-between text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="text-xs font-semibold text-tf-text">
          {c.source} <span className="text-tf-muted">（{c.kind}）</span>
        </span>
        <span className="text-[0.7rem]" style={{ color: directionColor(c.direction) }}>
          {directionLabel(c.direction)} · 信任 {c.trust.toFixed(2)}
        </span>
      </button>
      {c.claim_id && (
        <p className="mt-0.5 text-[0.65rem] text-tf-muted">claim_id：{c.claim_id}</p>
      )}
      {open && (
        <p className="mt-1 text-xs text-tf-text2">{c.text}</p>
      )}
    </li>
  )
}

function InsightCard({ ins }: { ins: Insight }) {
  const insufficient = ins.coverage === 'insufficient'
  const strengthPct = Math.round((ins.strength || 0) * 100)
  return (
    <div className="rounded-lg border border-tf-border bg-tf-card p-3">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-tf-text">{ins.title}</span>
        {ins.insight_type === 'source_self_contradiction' ? (
          <span className="rounded-full border border-tf-warn px-2 py-0.5 text-[0.7rem] font-semibold text-tf-warn">
            來源自我矛盾
          </span>
        ) : insufficient ? (
          <span className="rounded-full border border-tf-warn px-2 py-0.5 text-[0.7rem] font-semibold text-tf-warn">
            無法判定（樣本不足）
          </span>
        ) : (
          <span className="rounded-full border border-tf-accent px-2 py-0.5 text-[0.7rem] font-semibold text-tf-link">
            {directionLabel(ins.direction)}
          </span>
        )}
      </div>

      {!insufficient && (
        <div className="mb-2">
          <div className="mb-0.5 flex justify-between text-[0.65rem] text-tf-muted">
            <span>洞察強度</span>
            <span>{strengthPct}%</span>
          </div>
          <div className="h-1.5 w-full rounded-full bg-tf-border">
            <div
              className="h-1.5 rounded-full"
              style={{ width: `${strengthPct}%`, backgroundColor: 'var(--color-tf-accent)' }}
            />
          </div>
        </div>
      )}

      <p className="mb-2 text-xs text-tf-text2">{ins.summary}</p>

      {insufficient && ins.coverage_reason && (
        <p className="mb-2 text-[0.7rem] text-tf-warn">誠實閘：{ins.coverage_reason}</p>
      )}

      <p className="mb-1 text-[0.7rem] font-semibold text-tf-muted">貢獻來源對照（點開回溯原值）</p>
      <ul className="space-y-1.5">
        {ins.contributions.map((c, i) => (
          <ContributionRow key={i} c={c} />
        ))}
      </ul>

      {ins.meta && Object.keys(ins.meta).length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[0.7rem] text-tf-link">
            數值溯源（深層回溯原始數值）
          </summary>
          <ul className="mt-1 space-y-0.5 text-[0.7rem] text-tf-muted">
            {Object.entries(ins.meta).map(([k, v]) => (
              <li key={k}>
                {k}：{typeof v === 'number' ? String(v) : String(v)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

export default function InsightExplainabilityPanel({ insights }: { insights: Insight[] | undefined }) {
  if (!insights || insights.length === 0) {
    return (
      <div className="rounded-lg border border-tf-border bg-tf-card p-4">
        <h3 className="mb-2 text-sm font-semibold text-tf-text">獨特洞察層（可解釋溯源）</h3>
        <p className="text-xs text-tf-muted">目前未偵測到非顯而易見、可驗證的獨特洞察。</p>
      </div>
    )
  }
  return (
    <div className="rounded-lg border border-tf-border bg-tf-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-tf-text">獨特洞察層（可解釋溯源）</h3>
      <div className="flex flex-col gap-3">
        {insights.map((ins, i) => (
          <InsightCard key={i} ins={ins} />
        ))}
      </div>
    </div>
  )
}
