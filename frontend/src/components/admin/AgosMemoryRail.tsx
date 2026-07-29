/**
 * Agent OS Memory Rail — displays memory entries for a run.
 *
 * Issue: #924 | Epic: #914
 */
import type { AgosMemoryItem } from '../../lib/agosTypes'
import { AgosBadge, evidenceBadgeVariant } from './AgosBadge'
import { AgosRailState } from './AgosRailState'

interface AgosMemoryRailProps {
  items: AgosMemoryItem[]
  loading?: boolean
  error?: string | null
}

export function AgosMemoryRail({ items, loading, error }: AgosMemoryRailProps) {
  if (loading) {
    return <AgosRailState kind="loading" />
  }
  if (error) {
    return <AgosRailState kind={error === 'unauthorized' ? 'unauthorized' : 'error'} message={error === 'unauthorized' ? undefined : error} />
  }
  if (items.length === 0) {
    return <AgosRailState kind="empty" message="No memory entries for this run." />
  }

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {items.map(item => (
        <article key={item.memory_id} className="min-w-0 rounded-lg border border-gray-200 p-4 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <AgosBadge variant="historical" label={item.kind} />
            <AgosBadge variant={item.inclusion_status.startsWith('included') ? 'trusted' : 'candidate'} label={item.inclusion_status} />
                <AgosBadge
              variant={evidenceBadgeVariant(item.evidence_eligible_verified)}
              label={item.evidence_eligible_verified ? 'Evidence verified' : 'Context only'}
                />
          </div>
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-gray-500">Memory</dt><dd className="break-all font-mono">{item.memory_id}</dd>
            <dt className="text-gray-500">Provider</dt><dd className="break-all font-mono">{item.provider}</dd>
            <dt className="text-gray-500">Rank</dt><dd>{item.lineage_rank ?? '—'}</dd>
            <dt className="text-gray-500">Selection</dt><dd>{item.selection_reason}</dd>
            <dt className="text-gray-500">Content hash</dt><dd className="break-all font-mono">{item.content_hash}</dd>
            <dt className="text-gray-500">Content ref</dt><dd className="break-all font-mono">{item.content_ref}</dd>
            <dt className="text-gray-500">Published</dt><dd>{item.published_at || '—'}</dd>
            <dt className="text-gray-500">Retrieved</dt><dd>{item.retrieved_at}</dd>
            <dt className="text-gray-500">Expires</dt><dd>{item.expires_at || 'Never'}</dd>
          </dl>
        </article>
      ))}
    </div>
  )
}
