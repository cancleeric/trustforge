/**
 * Agent OS Skill Rail — displays frozen skill manifest for a run.
 *
 * Issue: #924 | Epic: #914
 */
import type { AgosSkillItem } from '../../lib/agosTypes'
import { AgosBadge } from './AgosBadge'

interface AgosSkillRailProps {
  items: AgosSkillItem[]
  loading?: boolean
  error?: string | null
}

export function AgosSkillRail({ items, loading, error }: AgosSkillRailProps) {
  if (loading) {
    return <div className="animate-pulse space-y-2">{[1, 2, 3].map(i => (
      <div key={i} className="h-12 bg-gray-100 rounded" />
    ))}</div>
  }
  if (error) {
    return <div className="text-red-600 p-4 border border-red-200 rounded">{error}</div>
  }
  if (items.length === 0) {
    return <p className="text-gray-500 p-4">No skill manifest for this run.</p>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-gray-600">
            <th className="p-2">Skill ID</th>
            <th className="p-2">Revision</th>
            <th className="p-2">Reason</th>
          </tr>
        </thead>
        <tbody>
          {items.map(item => (
            <tr key={item.skill_id} className="border-b hover:bg-gray-50">
              <td className="p-2 font-mono text-xs">{item.skill_id}</td>
              <td className="p-2">
                <code className="text-xs bg-gray-100 px-1 rounded">
                  {item.revision_hash.slice(0, 12)}...
                </code>
              </td>
              <td className="p-2 text-xs text-gray-600">{item.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
