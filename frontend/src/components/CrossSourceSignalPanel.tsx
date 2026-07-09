import type { CrossSourceSignal, StancePair } from '../lib/types'
import { groupByStance } from '../lib/stancePairs'
import { SingleSourceBadge } from './Badges'

function SideColumn({ label, color, items }: { label: string; color: string; items: StancePair[] }) {
  return (
    <div className="flex-1 rounded-lg border p-3" style={{ borderColor: color, backgroundColor: `color-mix(in srgb, ${color} 8%, transparent)` }}>
      <span
        className="mb-2 inline-block rounded-full border px-2 py-0.5 text-xs font-semibold"
        style={{ color, borderColor: color, backgroundColor: `color-mix(in srgb, ${color} 14%, transparent)` }}
      >
        {label}
      </span>
      {items.length === 0 ? (
        <p className="text-xs text-tf-muted">&#8212;</p>
      ) : (
        <ul className="space-y-2">
          {items.map((it, i) => (
            <li key={i}>
              <p className="text-xs font-semibold text-tf-text">{it.source}</p>
              <p className="text-xs text-tf-muted">{it.text}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function CrossSourceSignalPanel({ signal }: { signal: CrossSourceSignal | null }) {
  return (
    <div className="rounded-lg border border-tf-border bg-tf-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-tf-text">跨源分歧 / 共識</h3>
      {signal === null && (
        <p className="text-xs text-tf-muted">目前未偵測到同議題、跨源、語意矛盾的顯著訊號。</p>
      )}
      {signal !== null && signal.sentiment_source_count === 1 && (
        <div className="mb-2">
          <SingleSourceBadge
            label="單一來源主導"
            title="情緒類（news/social）訊號這一輪僅有 1 個獨立來源佐證，此類別判定可能被單一來源主導，參考性較低，建議留意其他管道佐證。"
          />
        </div>
      )}
      {signal !== null && signal.type === 'consensus' && (
        <div className="rounded-lg border border-tf-good p-3" style={{ backgroundColor: 'color-mix(in srgb, var(--color-tf-good) 8%, transparent)' }}>
          <span className="mb-1 inline-block rounded-full border border-tf-good px-2 py-0.5 text-xs font-semibold text-tf-good">
            共識
          </span>
          <p className="text-sm text-tf-text2">{signal.summary}</p>
        </div>
      )}
      {signal !== null && signal.type === 'divergence' && (
        <>
          <p className="mb-2 text-sm text-tf-text2">{signal.summary}</p>
          <div className="flex flex-col gap-3 sm:flex-row">
            <SideColumn label="&#9650; BULLISH" color="var(--color-tf-good)" items={groupByStance(signal).bullish} />
            <SideColumn label="&#9660; BEARISH" color="var(--color-tf-bad)" items={groupByStance(signal).bearish} />
          </div>
        </>
      )}
      {signal?.supporting_claim_ids?.length ? (
        <p className="mt-2 text-[0.7rem] text-tf-muted">佐證 claim_ids：{signal.supporting_claim_ids.join('、')}</p>
      ) : null}
    </div>
  )
}
