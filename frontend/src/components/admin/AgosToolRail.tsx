/**
 * Agent OS Tool Rail — displays tool invocation audit for a run.
 *
 * Issue: #924 | Epic: #914
 */
import type { AgosToolItem } from '../../lib/agosTypes'
import { AgosBadge, statusBadgeVariant } from './AgosBadge'
import { AgosRailState } from './AgosRailState'

interface AgosToolRailProps {
  items: AgosToolItem[]
  loading?: boolean
  error?: string | null
}

export function AgosToolRail({ items, loading, error }: AgosToolRailProps) {
  if (loading) {
    return <AgosRailState kind="loading" />
  }
  if (error) {
    return <AgosRailState kind={error === 'unauthorized' ? 'unauthorized' : 'error'} message={error === 'unauthorized' ? undefined : error} />
  }
  if (items.length === 0) {
    return <AgosRailState kind="empty" message="No tool invocations for this run." />
  }

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {items.map(item => (
        <article key={item.invocation_id} className="min-w-0 rounded-lg border border-gray-200 p-4 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="break-all font-mono text-xs">{item.tool_id}</strong>
            <AgosBadge variant={statusBadgeVariant(item.status)} label={item.status} />
            {item.side_effect_class && <AgosBadge variant={item.side_effect_class === 'read_only' ? 'risk-read' : item.side_effect_class === 'local_write' ? 'risk-local' : item.side_effect_class === 'external_write' ? 'risk-external' : item.side_effect_class === 'deploy_or_release' ? 'risk-deploy' : 'historical'} label={item.side_effect_class} />}
          </div>
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-gray-500">Invocation</dt><dd className="break-all font-mono">{item.invocation_id}</dd>
            <dt className="text-gray-500">Approval</dt><dd>{item.approval_requirement || 'Unknown'}</dd>
            <dt className="text-gray-500">Evidence class</dt><dd>{item.evidence_class || 'Unknown'}</dd>
            <dt className="text-gray-500">Evidence refs</dt><dd className="break-all">{item.evidence_refs?.join(', ') || 'None'}</dd>
            <dt className="text-gray-500">Input hash</dt><dd className="break-all font-mono">{item.input_hash}</dd>
            <dt className="text-gray-500">Output hash</dt><dd className="break-all font-mono">{item.output_hash || '—'}</dd>
            <dt className="text-gray-500">Started</dt><dd>{item.started_at}</dd>
            <dt className="text-gray-500">Completed</dt><dd>{item.completed_at || '—'}</dd>
            <dt className="text-gray-500">Error</dt><dd className="text-red-600">{item.error || '—'}</dd>
          </dl>
        </article>
      ))}
    </div>
  )
}
