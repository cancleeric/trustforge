import type { ComparisonReportData } from '../lib/types'
import { formatTimestamp } from '../lib/format'
import { ErrorState } from './StatusStates'

/** 單一比較面向的決策邊框顏色。 */
function decisionBorderColor(decision: string): string {
  switch (decision) {
    case 'normal': return 'var(--color-tf-good)'
    case 'insufficient': return 'var(--color-tf-warn)'
    case 'abstain': return 'var(--color-tf-muted)'
    default: return 'var(--color-tf-border)'
  }
}

/** 決策中文字面。 */
function decisionLabel(decision: string): string {
  switch (decision) {
    case 'normal': return '✅ 可判定'
    case 'insufficient': return '⚠️ 資訊不足'
    case 'abstain': return '— 棄權'
    default: return '—'
  }
}

export default function ComparisonReportView({
  data,
  isLoading,
  error,
}: {
  data?: ComparisonReportData | null
  isLoading?: boolean
  error?: { code: string; message: string } | null
}) {
  if (isLoading) {
    return (
      <div data-testid="comparison-report-view" className="flex flex-col gap-5">
        {/* skeleton hero */}
        <div className="hermes-clip rounded-lg border-2 bg-tf-card p-5 animate-pulse" style={{ borderColor: 'var(--color-tf-border)' }}>
          <div className="mb-2 h-4 w-24 rounded bg-tf-border" />
          <div className="mb-2 h-6 w-3/4 rounded bg-tf-border" />
          <div className="h-4 w-1/3 rounded bg-tf-border" />
        </div>
        {/* skeleton dimension cards */}
        <section>
          <div className="mb-3 h-4 w-20 rounded bg-tf-border animate-pulse" />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4 animate-pulse">
                <div className="mb-2 h-4 w-24 rounded bg-tf-border" />
                <div className="h-16 w-full rounded bg-tf-border" />
              </div>
            ))}
          </div>
        </section>
      </div>
    )
  }

  if (error) {
    return (
      <div data-testid="comparison-report-view">
        <ErrorState code={error.code} message={error.message} />
      </div>
    )
  }

  if (!data) return null

  const allAbstain = data.dimensions.length > 0 && data.dimensions.every((d) => d.decision === 'abstain')

  if (allAbstain) {
    return (
      <div data-testid="comparison-report-view" className="flex flex-col gap-5">
        <section
          className="hermes-clip rounded-lg border-2 bg-tf-card p-5"
          style={{ borderColor: 'var(--color-tf-border)' }}
        >
          <p className="mb-1 font-mono text-xs font-semibold uppercase text-tf-muted">
            綜合比較結論
          </p>
          <p className="text-lg font-bold text-tf-muted">{data.conclusion}</p>
          <p className="mt-2 text-xs text-tf-muted">
            {data.coin_a} vs {data.coin_b} · {formatTimestamp(data.generated_at)}
          </p>
        </section>
        <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-5 text-center">
          <p className="text-sm font-semibold text-tf-muted">所有面向均棄權</p>
          <p className="mt-1 text-xs text-tf-muted">無法進行有效比較</p>
        </div>
      </div>
    )
  }

  return (
    <div data-testid="comparison-report-view" className="flex flex-col gap-5">
      {/* Hero 卡：共同結論 */}
      <section
        className="hermes-clip rounded-lg border-2 bg-tf-card p-5"
        style={{ borderColor: 'var(--color-tf-accent)' }}
      >
        <p className="mb-1 font-mono text-xs font-semibold uppercase text-tf-link">
          綜合比較結論
        </p>
        <p className="text-lg font-bold text-tf-text">{data.conclusion}</p>
        <p className="mt-2 text-xs text-tf-muted">
          {data.coin_a} vs {data.coin_b} · {formatTimestamp(data.generated_at)}
        </p>
      </section>

      {/* 四個面向卡片 grid */}
      <section>
        <h2 className="mb-3 text-sm font-semibold text-tf-text">面向分析</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {data.dimensions.map((dim, i) => (
            <article
              key={i}
              className="hermes-clip rounded-lg border bg-tf-card p-4 transition-colors"
              style={{ borderColor: decisionBorderColor(dim.decision) }}
            >
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-tf-text">{dim.dimension}</h3>
                <span className="rounded-full px-2 py-0.5 text-[0.65rem] font-semibold" style={{
                  backgroundColor: decisionBorderColor(dim.decision),
                  color: 'var(--color-tf-bg)',
                }}>
                  {decisionLabel(dim.decision)}
                </span>
              </div>
              <p className="mb-3 text-sm leading-relaxed text-tf-text2">{dim.reasoning}</p>
              <div className="flex items-center gap-2">
                <span className="text-xs text-tf-muted">完整度</span>
                <div className="h-1.5 flex-1 rounded-full bg-tf-border">
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${Math.round(dim.confidence * 100)}%`,
                      backgroundColor: 'var(--color-tf-accent)',
                    }}
                  />
                </div>
                <span className="tf-num text-xs text-tf-muted">
                  {Math.round(dim.confidence * 100)}%
                </span>
              </div>
              {dim.abstain_reason && (
                <p className="mt-2 text-xs italic text-tf-muted">{dim.abstain_reason}</p>
              )}
            </article>
          ))}
        </div>
      </section>

      {/* Known Limits 摺疊區 */}
      {data.limits.length > 0 && (
        <details className="hermes-clip rounded-lg border border-tf-border bg-tf-card" open>
          <summary className="cursor-pointer p-4 text-sm font-semibold text-tf-text">
            已知限制（{data.limits.length}）
          </summary>
          <ul className="list-disc space-y-1 px-8 pb-4 text-sm text-tf-text2">
            {data.limits.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </details>
      )}

      {/* Could Flip 摺疊區 */}
      {data.could_flip.length > 0 && (
        <details className="hermes-clip rounded-lg border border-tf-border bg-tf-card">
          <summary className="cursor-pointer p-4 text-sm font-semibold text-tf-text">
            可能推翻結論的條件（{data.could_flip.length}）
          </summary>
          <ul className="list-disc space-y-1 px-8 pb-4 text-sm text-tf-text2">
            {data.could_flip.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
