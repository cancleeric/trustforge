import type { Evidence, PriceProvenance } from '../lib/types'
import { safeHref } from '../lib/safeHref'

interface Props {
  priceProvenance: PriceProvenance
  evidence: Evidence[]
}

/** `price_provenance` 目前只收錄歷史 OHLCV 溯源；「現價」對照從
 * `evidence`（`kind === 'hoyabit'`，即時報價/深度）取得——兩者並列呈現
 * 「歷史 vs 現價」，而非後端額外新算欄位（credit-safe：零新 I/O，純既有
 * 欄位重新排版）。
 */
export default function PriceProvenancePanel({ priceProvenance, evidence }: Props) {
  const historyEntries = Object.entries(priceProvenance)
  const liveEntries = evidence.filter((e) => e.kind === 'hoyabit')

  if (historyEntries.length === 0 && liveEntries.length === 0) return null

  return (
    <div className="rounded-lg border border-tf-border bg-tf-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-tf-text">價格溯源：歷史 vs 現價</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <p className="mb-1 text-xs font-semibold text-tf-muted">歷史（OHLCV）</p>
          {historyEntries.length === 0 ? (
            <p className="text-xs text-tf-muted">&#8212;</p>
          ) : (
            <ul className="space-y-2">
              {historyEntries.map(([key, entry]) => {
                const href = safeHref(entry.source_url)
                return (
                  <li key={key} className="rounded border border-tf-border p-2 text-xs">
                    <p className="tf-num text-tf-text2">{entry.content_reference}</p>
                    <p className="mt-1 text-tf-muted">{entry.fetched_at}</p>
                    {href && (
                      <a href={href} target="_blank" rel="noreferrer noopener" className="text-tf-link underline">
                        來源連結 &#8599;
                      </a>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
        <div>
          <p className="mb-1 text-xs font-semibold text-tf-muted">即時（交易所）</p>
          {liveEntries.length === 0 ? (
            <p className="text-xs text-tf-muted">&#8212;</p>
          ) : (
            <ul className="space-y-2">
              {liveEntries.map((ev, i) => {
                const href = safeHref(ev.source_url)
                return (
                  <li key={i} className="rounded border border-tf-border p-2 text-xs">
                    <p className="tf-num text-tf-text2">{ev.content_reference}</p>
                    <p className="mt-1 text-tf-muted">{ev.fetched_at}</p>
                    {href && (
                      <a href={href} target="_blank" rel="noreferrer noopener" className="text-tf-link underline">
                        來源連結 &#8599;
                      </a>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
