import type { ReactNode } from 'react'
import type { TrustComponentsAggregate } from '../lib/types'
import GlossaryTerm, { type GlossaryKey } from './GlossaryTerm'

const DIMENSION_META: {
  key: keyof TrustComponentsAggregate
  label: string
  why: ReactNode
  glossary: GlossaryKey
}[] = [
  { key: 'reputation', label: '信譽', why: '來源過去的可靠表現與跨來源核對結果', glossary: 'reputation' },
  { key: 'corroboration', label: '交叉佐證', why: '彼此獨立的來源是否支持同一主張', glossary: 'corroboration' },
  { key: 'recency', label: '資料時效', why: '資料距離現在的時間與更新狀態', glossary: 'recency' },
  {
    key: 'manipulation', label: '操縱風險', glossary: 'manipulation',
    why: (
      <>命中<GlossaryTerm term="washTrading" label="wash-trading" compact />、<GlossaryTerm term="spoofing" label="spoofing" compact /> 等操縱訊號的比例，越低越好</>
    ),
  },
]

export default function TrustBreakdown({ data }: { data: TrustComponentsAggregate }) {
  return (
    <div className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-tf-text" title="此為原始分數，非加權後最終信任分">信任拆解（逐項 WHY）</h3>
      <ul className="flex flex-col gap-3">
        {DIMENSION_META.map(({ key, label, why, glossary }) => {
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
                <GlossaryTerm term={glossary} label={label} compact />
                {isUnscored ? (
                  <span className="text-tf-muted">暫無評分</span>
                ) : (
                  <span className="tf-num text-tf-muted" title="此為原始分數，非加權後最終信任分">{(clamped * 100).toFixed(0)}%</span>
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
