/**
 * Agent OS Memory Rail — displays memory entries for a run.
 *
 * Issue: #924 | Epic: #914
 */
import type { AgosMemoryItem } from '../../lib/agosTypes'
import { AgosBadge, evidenceBadgeVariant } from './AgosBadge'

interface AgosMemoryRailProps {
  items: AgosMemoryItem[]
  loading?: boolean
  error?: string | null
}

export function AgosMemoryRail({ items, loading, error }: AgosMemoryRailProps) {
  if (loading) {
    return <div className="animate-pulse space-y-2">{[1, 2, 3].map(i => (
      <div key={i} className="h-12 bg-gray-100 rounded" />
    ))}</div>
  }
  if (error) {
    return <div className="text-red-600 p-4 border border-red-200 rounded">{error}</div>
  }
  if (items.length === 0) {
    return <p className="text-gray-500 p-4">No memory entries for this run.</p>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-gray-600">
            <th className="p-2">Kind</th>
            <th className="p-2">Provider</th>
            <th className="p-2">Eligibility</th>
            <th className="p-2">Retrieved</th>
          </tr>
        </thead>
        <tbody>
          {items.map(item => (
            <tr key={item.memory_id} className="border-b hover:bg-gray-50">
              <td className="p-2">
                <AgosBadge variant="historical" label={item.kind} />
              </td>
              <td className="p-2 font-mono text-xs">{item.provider}</td>
              <td className="p-2">
                <AgosBadge
                  variant={evidenceBadgeVariant(item.evidence_eligible)}
                  label={item.evidence_eligible ? 'Evidence' : 'Context only'}
                />
              </td>
              <td className="p-2 text-xs text-gray-500">{item.retrieved_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
