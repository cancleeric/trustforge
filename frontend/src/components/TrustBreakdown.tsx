import type { TrustComponentsAggregate } from '../lib/types'

const DIMENSION_META: {
  key: keyof TrustComponentsAggregate
  label: string
  why: string
}[] = [
  { key: 'reputation', label: '信譽', why: '來源歷史 track record（含跨源互證後的信譽調整）' },
  { key: 'corroboration', label: '佐證', why: '有多少獨立來源支持同一主張，越多越可信' },
  { key: 'recency', label: '時效', why: '資料新鮮度，越接近現在權重越高' },
  { key: 'manipulation', label: '操縱風險', why: '命中操縱關鍵詞（喊單/割肉/穩賺等）比例，越低越好' },
]

export default function TrustBreakdown({ data }: { data: TrustComponentsAggregate }) {
  return (
    <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-tf-text">信任拆解（逐項 WHY）</h3>
      <ul className="flex flex-col gap-3">
        {DIMENSION_META.map(({ key, label, why }) => {
          const raw = data[key]
          const isUnscored = raw === null || raw === undefined
          const value = isUnscored ? 0 : raw
          const clamped = Math.max(0, Math.min(1, value))
          const isRisk = key === 'manipulation'
          const barColor = isRisk
            ? clamped > 0.3
              ? 'var(--color-tf-bad)'
              : 'var(--color-tf-good)'
            : clamped >= 0.6
              ? 'var(--color-tf-good)'
              : clamped >= 0.35
                ? 'var(--color-tf-warn)'
                : 'var(--color-tf-bad)'
          return (
            <li key={key}>
              <div className="mb-1 flex items-baseline justify-between text-xs">
                <span className="font-semibold text-tf-text">{label}</span>
                {isUnscored ? (
                  <span className="text-tf-muted">暫無評分</span>
                ) : (
                  <span className="tf-num text-tf-muted">{(clamped * 100).toFixed(0)}%</span>
                )}
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-tf-border">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${clamped * 100}%`, backgroundColor: barColor }}
                />
              </div>
              <p className="mt-1 text-[0.72rem] leading-snug text-tf-muted">{why}</p>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
