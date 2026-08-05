import { useMemo, useState } from 'react'
import type { Evidence, EvidenceGroup } from '../lib/types'
import { getRenderGroups, trendLabel } from '../lib/evidenceGrouping'
import { sourceDisplayName } from '../lib/sourceBrand'
import { safeHref } from '../lib/safeHref'
import { FlagBadge, InfoFlagBadge, LowTrustBadge, TierBadge } from './Badges'
import SnapshotModal from './SnapshotModal'

function trustColor(trust: number): string {
  if (trust < 0.3) return 'var(--color-tf-bad)'
  if (trust < 0.6) return 'var(--color-tf-warn)'
  return 'var(--color-tf-good)'
}

function compareDeterministicText(a: string, b: string): number {
  const aCjkFirst = /^[\u3400-\u9fff]/u.test(a) ? 0 : 1
  const bCjkFirst = /^[\u3400-\u9fff]/u.test(b) ? 0 : 1
  if (aCjkFirst !== bCjkFirst) return aCjkFirst - bCjkFirst

  const max = Math.max(a.length, b.length)
  for (let i = 0; i < max; i += 1) {
    const aCode = a.codePointAt(i)
    const bCode = b.codePointAt(i)
    if (aCode === undefined) return -1
    if (bCode === undefined) return 1
    if (aCode !== bCode) return aCode - bCode
  }
  return 0
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
      <td className="max-w-md px-3 py-2 align-top text-xs text-tf-text2">{ev.content_reference}</td>
      <td className="whitespace-nowrap px-3 py-2 align-top text-xs text-tf-muted">{ev.fetched_at}</td>
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

/** #862 趨勢方向 badge。 */
function TrendBadge({ trend }: { trend: EvidenceGroup['trend'] }) {
  if (!trend) return null
  const config = {
    rising: { icon: '\u25B2', color: 'var(--color-tf-good)', label: trendLabel(trend) },
    falling: { icon: '\u25BC', color: 'var(--color-tf-bad)', label: trendLabel(trend) },
    stable: { icon: '\u2014', color: 'var(--color-tf-muted)', label: trendLabel(trend) },
  }
  const { icon, color, label } = config[trend]
  return (
    <span
      className="inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[0.65rem] font-medium"
      style={{ color, backgroundColor: `color-mix(in srgb, ${color} 12%, transparent)` }}
      aria-label={label}
    >
      {icon} {label}
    </span>
  )
}

/** #862 聚合群組列：折疊態顯示代表摘要 + 趨勢 + 成員數；展開顯示所有成員。 */
function EvidenceGroupRow({ group, evidence }: { group: EvidenceGroup; evidence: Evidence[] }) {
  const [expanded, setExpanded] = useState(false)
  const rep = evidence[group.representative_idx]
  const memberCount = group.member_indices.length

  // Bounds safety: stale snapshot could have out-of-range index
  if (!rep) {
    return null
  }

  if (memberCount < 2) {
    // 單筆群組：直接渲染為普通 EvidenceRow
    return <EvidenceRow ev={rep} idx={group.representative_idx} />
  }

  return (
    <>
      <tr
        className="hermes-row-hover cursor-pointer bg-tf-card/50"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-label={`證據群組：${sourceDisplayName(rep.source)}，${memberCount} 筆觀測`}
      >
        <td className="tf-num whitespace-nowrap px-3 py-2 align-top text-xs text-tf-muted">
          <span className="inline-block w-4 text-center">{expanded ? '\u25BC' : '\u25B6'}</span>
        </td>
        <td className="px-3 py-2 align-top">
          <div className="flex flex-wrap items-center gap-1.5 text-sm">
            <span className="font-semibold text-tf-text">{sourceDisplayName(rep.source)}</span>
            <TierBadge kind={rep.kind} />
            <TrendBadge trend={group.trend} />
            {group.value_range && (
              <span className="text-[0.72rem] font-medium text-tf-text2">{group.value_range}</span>
            )}
            <span className="rounded-full bg-tf-border px-1.5 py-0.5 text-[0.65rem] text-tf-muted">
              {memberCount} 筆觀測
            </span>
          </div>
          {!expanded && group.latest_value && (
            <p className="mt-0.5 text-xs text-tf-muted">最新：{group.latest_value}</p>
          )}
        </td>
        <td className="max-w-md px-3 py-2 align-top text-xs text-tf-text2">{rep.content_reference}</td>
        <td className="whitespace-nowrap px-3 py-2 align-top text-xs text-tf-muted">{rep.fetched_at}</td>
        <td className="px-3 py-2 align-top">
          <div className="flex items-center gap-2">
            <div className="h-1.5 w-16 overflow-hidden rounded-full bg-tf-border">
              <div
                className="h-full rounded-full"
                style={{ width: `${Math.max(0, Math.min(1, rep.trust)) * 100}%`, backgroundColor: trustColor(rep.trust) }}
              />
            </div>
            <span className="tf-num text-xs" style={{ color: trustColor(rep.trust) }}>
              {rep.trust.toFixed(2)}
            </span>
          </div>
        </td>
      </tr>
      {expanded && group.member_indices.map((idx) => {
        const ev = evidence[idx]
        return ev ? <EvidenceRow key={idx} ev={ev} idx={idx} /> : null
      })}
    </>
  )
}

type EvidenceSort = 'default' | 'source' | 'summary' | 'trust' | 'time'

export default function EvidenceTable({
  evidence,
  evidenceGroups,
}: {
  evidence: Evidence[]
  evidenceGroups?: EvidenceGroup[] | null
}) {
  const [sortState, setSortState] = useState<{ key: EvidenceSort; descending: boolean }>({
    key: 'default',
    descending: true,
  })
  const { key: sort, descending } = sortState
  const groups = getRenderGroups(evidenceGroups)
  const useGrouped = groups.length > 0 && sort === 'default'
  const sortedEvidence = useMemo(() => evidence.map((ev, idx) => ({ ev, idx })).sort((a, b) => {
    if (sort === 'default') return a.idx - b.idx
    const direction = descending ? -1 : 1
    if (sort === 'source') return direction * a.ev.source.localeCompare(b.ev.source)
    if (sort === 'summary') {
      const summaryOrder = compareDeterministicText(a.ev.content_reference, b.ev.content_reference)
      return summaryOrder === 0 ? a.idx - b.idx : direction * summaryOrder
    }
    if (sort === 'trust') return direction * (a.ev.trust - b.ev.trust)
    return direction * (Date.parse(a.ev.fetched_at) - Date.parse(b.ev.fetched_at))
  }), [descending, evidence, sort])
  const chooseSort = (next: EvidenceSort) => {
    setSortState((current) => {
      if (current.key === next) return { ...current, descending: !current.descending }
      return { key: next, descending: next === 'trust' || next === 'time' }
    })
  }
  const restoreGroupedView = () => {
    setSortState({ key: 'default', descending: true })
  }
  const sortLabel = (key: EvidenceSort) => sort === key ? (descending ? ' ▼' : ' ▲') : ''

  return (
    <div className="overflow-x-auto hermes-clip rounded-lg border border-tf-border bg-tf-card">
      <table className="min-w-[760px] w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-tf-border text-xs text-tf-muted">
            <th className="px-3 py-2 font-medium">#</th>
            <th className="px-3 py-2 font-medium"><button type="button" onClick={() => chooseSort('source')}>來源{sortLabel('source')}</button></th>
            <th className="px-3 py-2 font-medium"><button type="button" onClick={() => chooseSort('summary')}>摘要{sortLabel('summary')}</button></th>
            <th className="px-3 py-2 font-medium"><button type="button" onClick={() => chooseSort('time')}>時間{sortLabel('time')}</button></th>
            <th className="px-3 py-2 font-medium">
              <div className="flex items-center gap-2">
                <button type="button" onClick={() => chooseSort('trust')}>信任分{sortLabel('trust')}</button>
                {groups.length > 0 && sort !== 'default' && (
                  <button
                    type="button"
                    onClick={restoreGroupedView}
                    className="rounded border border-tf-border px-1.5 py-0.5 text-[0.68rem] text-tf-muted hover:text-tf-text"
                    aria-label="清除排序並回到分組檢視"
                  >
                    回分組
                  </button>
                )}
              </div>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-tf-border">
          {useGrouped
            ? groups.map((group) => (
              <EvidenceGroupRow
                key={group.representative_idx}
                group={group}
                evidence={evidence}
              />
            ))
            : sortedEvidence.map(({ ev, idx }) => (
              <EvidenceRow key={idx} ev={ev} idx={idx} />
            ))}
        </tbody>
      </table>
    </div>
  )
}
