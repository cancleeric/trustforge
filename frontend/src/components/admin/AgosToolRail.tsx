/**
 * Agent OS Tool Rail — displays tool invocation audit for a run.
 *
 * Issue: #924 | Epic: #914
 */
import type { AgosToolItem } from '../../lib/agosTypes'
import { AgosBadge, statusBadgeVariant } from './AgosBadge'

interface AgosToolRailProps {
  items: AgosToolItem[]
  loading?: boolean
  error?: string | null
}

export function AgosToolRail({ items, loading, error }: AgosToolRailProps) {
  if (loading) {
    return <div className="animate-pulse space-y-2">{[1, 2, 3].map(i => (
      <div key={i} className="h-12 bg-gray-100 rounded" />
    ))}</div>
  }
  if (error) {
    return <div className="text-red-600 p-4 border border-red-200 rounded">{error}</div>
  }
  if (items.length === 0) {
    return <p className="text-gray-500 p-4">No tool invocations for this run.</p>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-gray-600">
            <th className="p-2">Tool</th>
            <th className="p-2">Status</th>
            <th className="p-2">Input Hash</th>
            <th className="p-2">Started</th>
            <th className="p-2">Error</th>
          </tr>
        </thead>
        <tbody>
          {items.map(item => (
            <tr key={item.invocation_id} className="border-b hover:bg-gray-50">
              <td className="p-2 font-mono text-xs">{item.tool_id}</td>
              <td className="p-2">
                <AgosBadge variant={statusBadgeVariant(item.status)} label={item.status} />
              </td>
              <td className="p-2">
                <code className="text-xs bg-gray-100 px-1 rounded">
                  {item.input_hash.slice(0, 12)}...
                </code>
              </td>
              <td className="p-2 text-xs text-gray-500">{item.started_at}</td>
              <td className="p-2 text-xs text-red-500">{item.error || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
