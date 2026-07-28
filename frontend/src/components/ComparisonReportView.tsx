import type { ComparisonReportData } from '../lib/types'
import { formatTimestamp } from '../lib/format'

/** 單一比較面向的決策邊框顏色。 */
function decisionBorderColor(decision: string): string {
  switch (decision) {
    case 'normal': return 'var(--color-tf-good)'
    case 'insufficient': return 'var(--color-tf-warn)'
    default: return 'var(--color-tf-border)'
  }
}

/** 決策中文字面。 */
function decisionLabel(decision: string): string {
  switch (decision) {
    case 'normal': return '✅ 可判定'
    case 'insufficient': return '⚠️ 資訊不足'
    default: return '—'
  }
}

export default function ComparisonReportView({ data }: { data: ComparisonReportData }) {
  return (
    <div className="flex flex-col gap-5">
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
