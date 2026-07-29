/**
 * Agent OS Skill Rail — displays frozen skill manifest for a run.
 *
 * Issue: #924 | Epic: #914
 */
import type { AgosSkillItem } from '../../lib/agosTypes'
import { AgosBadge } from './AgosBadge'
import { AgosRailState } from './AgosRailState'

interface AgosSkillRailProps {
  items: AgosSkillItem[]
  loading?: boolean
  error?: string | null
}

export function AgosSkillRail({ items, loading, error }: AgosSkillRailProps) {
  if (loading) {
    return <AgosRailState kind="loading" />
  }
  if (error) {
    return <AgosRailState kind={error === 'unauthorized' ? 'unauthorized' : 'error'} message={error === 'unauthorized' ? undefined : error} />
  }
  if (items.length === 0) {
    return <AgosRailState kind="empty" message="No skill manifest for this run." />
  }

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {items.map(item => (
        <article key={item.skill_id} className="min-w-0 rounded-lg border border-gray-200 p-4 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="break-all font-mono text-xs">{item.skill_id}</strong>
            {item.lifecycle && <AgosBadge variant={item.lifecycle === 'active' ? 'trusted' : 'candidate'} label={item.lifecycle} />}
            {item.risk_class && <AgosBadge variant={item.risk_class === 'read_only' ? 'risk-read' : item.risk_class === 'local_write' ? 'risk-local' : item.risk_class === 'external_write' ? 'risk-external' : 'risk-deploy'} label={item.risk_class} />}
          </div>
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-gray-500">Revision</dt><dd className="break-all font-mono">{item.revision_hash}</dd>
            <dt className="text-gray-500">Frozen at</dt><dd>{item.frozen_at}</dd>
            <dt className="text-gray-500">Family</dt><dd>{item.family || 'Unknown'}</dd>
            <dt className="text-gray-500">Side effect</dt><dd>{item.side_effect_class || 'Unknown'}</dd>
            <dt className="text-gray-500">Selection</dt><dd>{item.reason}</dd>
            <dt className="text-gray-500">Dependencies</dt>
            <dd>{item.dependencies?.length ? item.dependencies.map(dep => `${dep.relation}: ${dep.to}`).join(', ') : 'None'}</dd>
          </dl>
        </article>
      ))}
    </div>
  )
}
