import { useState } from 'react'
import type { Evidence } from '../lib/types'
import { sourceDisplayName } from '../lib/sourceBrand'
import { safeHref } from '../lib/safeHref'
import { FlagBadge, InfoFlagBadge, LowTrustBadge, TierBadge } from './Badges'
import SnapshotModal from './SnapshotModal'

function trustColor(trust: number): string {
  if (trust < 0.3) return 'var(--color-tf-bad)'
  if (trust < 0.6) return 'var(--color-tf-warn)'
  return 'var(--color-tf-good)'
}

function EvidenceRow({ ev, idx }: { ev: Evidence; idx: number }) {
  const isLow = ev.trust < 0.3
  const href = safeHref(ev.source_url)
  const [showSnapshot, setShowSnapshot] = useState(false)
  return (
    <tr id={`evidence-${idx}`} className={`hermes-row-hover ${isLow ? 'bg-tf-bad/5' : ''}`}>
      <td className="tf-num whitespace-nowrap px-3 py-2 align-top text-xs text-tf-muted">E{idx}</td>
      <td className="px-3 py-2 align-top">
        <details>
          <summary className="flex cursor-pointer flex-wrap items-center gap-1.5 text-sm">
            <span className="font-semibold text-tf-text">{sourceDisplayName(ev.source)}</span>
            <TierBadge kind={ev.kind} />
            <span className="text-[0.68rem] text-tf-muted">{ev.fetched_at}</span>
            {isLow && <LowTrustBadge />}
            <FlagBadge flags={ev.flags} />
            <InfoFlagBadge infoFlags={ev.info_flags} />
          </summary>
          <div className="mt-2 space-y-1 text-xs text-tf-text2">
            <p>{ev.content_reference}</p>
            <p className="text-tf-muted">關聯主張：{ev.related_claim}</p>
            {ev.data_lineage && (
              <p className="break-all text-tf-muted">
                資料血緣：{ev.data_lineage.file}｜{ev.data_lineage.coverage.start_date}~{ev.data_lineage.coverage.end_date}｜SHA-256 {ev.data_lineage.sha256}
              </p>
            )}
            {href ? (
              <a href={href} target="_blank" rel="noreferrer noopener" className="text-tf-link underline">
                來源連結 &#8599;
              </a>
            ) : (
              <span className="text-tf-muted">&#8212; 無有效來源連結</span>
            )}
            {' '}
            <button
              type="button"
              onClick={(e) => { e.preventDefault(); setShowSnapshot(true) }}
              className="text-tf-link underline"
            >
              原始快照 &#8599;
            </button>
            {showSnapshot && <SnapshotModal ev={ev} onClose={() => setShowSnapshot(false)} />}
            {Object.keys(ev.trust_components).length > 0 && (
              <dl className="tf-num grid grid-cols-2 gap-x-3 gap-y-0.5 pt-1 text-[0.68rem] text-tf-muted sm:grid-cols-3">
                {Object.entries(ev.trust_components).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-2">
                    <dt>{k}</dt>
                    <dd>{typeof v === 'number' ? v.toFixed(2) : String(v)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </div>
        </details>
      </td>
      <td className="px-3 py-2 align-top">
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-16 overflow-hidden rounded-full bg-tf-border">
            <div
              className="h-full rounded-full"
              style={{ width: `${Math.max(0, Math.min(1, ev.trust)) * 100}%`, backgroundColor: trustColor(ev.trust) }}
            />
          </div>
          <span className="tf-num text-xs" style={{ color: trustColor(ev.trust) }}>
            {ev.trust.toFixed(2)}
          </span>
        </div>
      </td>
    </tr>
  )
}

export default function EvidenceTable({ evidence }: { evidence: Evidence[] }) {
  return (
    <div className="overflow-x-auto hermes-clip rounded-lg border border-tf-border bg-tf-card">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-tf-border text-xs text-tf-muted">
            <th className="px-3 py-2 font-medium">#</th>
            <th className="px-3 py-2 font-medium">來源（點展開）</th>
            <th className="px-3 py-2 font-medium">信任分</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-tf-border">
          {evidence.map((ev, i) => (
            <EvidenceRow key={i} ev={ev} idx={i} />
          ))}
        </tbody>
      </table>
    </div>
  )
}
