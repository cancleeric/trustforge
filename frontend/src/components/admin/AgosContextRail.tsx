/**
 * Agent OS Context Rail — displays context manifest for a run.
 *
 * Issue: #924 | Epic: #914
 */
import type { AgosContextManifest } from '../../lib/agosTypes'
import { AgosBadge } from './AgosBadge'
import { AgosTokenBudgetBar } from './AgosTokenBudgetBar'

interface AgosContextRailProps {
  manifest: AgosContextManifest | null
  loading?: boolean
  error?: string | null
}

export function AgosContextRail({ manifest, loading, error }: AgosContextRailProps) {
  if (loading) {
    return <div className="animate-pulse space-y-2">{[1, 2, 3].map(i => (
      <div key={i} className="h-8 bg-gray-100 rounded" />
    ))}</div>
  }
  if (error) {
    return <div className="text-red-600 p-4 border border-red-200 rounded">{error}</div>
  }
  if (!manifest) {
    return <p className="text-gray-500 p-4">No context manifest for this run.</p>
  }

  return (
    <div className="space-y-4">
      {/* Token Budget */}
      <div>
        <h4 className="text-sm font-medium text-gray-700 mb-2">Token Budget</h4>
        <AgosTokenBudgetBar used={manifest.token_used} total={manifest.token_budget} />
      </div>

      {/* Content Hash */}
      <div>
        <h4 className="text-sm font-medium text-gray-700 mb-1">Content Hash</h4>
        <code className="text-xs bg-gray-100 px-2 py-1 rounded block break-all">
          {manifest.content_hash}
        </code>
      </div>

      {/* Included Summary */}
      <div>
        <h4 className="text-sm font-medium text-gray-700 mb-2">
          Included References ({manifest.included_count})
        </h4>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
          <div className="bg-blue-50 p-2 rounded">
            <span className="text-blue-600 font-medium">Memory</span>
            <p className="text-blue-900">{manifest.included_refs.memory_refs.length}</p>
          </div>
          <div className="bg-purple-50 p-2 rounded">
            <span className="text-purple-600 font-medium">Skills</span>
            <p className="text-purple-900">{manifest.included_refs.skill_refs.length}</p>
          </div>
          <div className="bg-teal-50 p-2 rounded">
            <span className="text-teal-600 font-medium">Tools</span>
            <p className="text-teal-900">{manifest.included_refs.tool_refs.length}</p>
          </div>
          <div className="bg-indigo-50 p-2 rounded">
            <span className="text-indigo-600 font-medium">Policies</span>
            <p className="text-indigo-900">{manifest.included_refs.policy_refs.length}</p>
          </div>
        </div>
      </div>

      {/* Excluded */}
      {manifest.excluded_refs.length > 0 && (
        <div>
          <h4 className="text-sm font-medium text-gray-700 mb-2">
            Excluded References ({manifest.excluded_count})
          </h4>
          <div className="space-y-1">
            {manifest.excluded_refs.map((ref, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <AgosBadge variant="historical" label={ref.ref_type} />
                <span className="font-mono">{ref.ref_id}</span>
                <AgosBadge variant="candidate" label={ref.reason} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Exclusion Reason Summary */}
      {Object.keys(manifest.exclusion_reasons).length > 0 && (
        <div>
          <h4 className="text-sm font-medium text-gray-700 mb-1">Exclusion Reasons</h4>
          <div className="flex gap-2 flex-wrap">
            {Object.entries(manifest.exclusion_reasons).map(([reason, count]) => (
              <span key={reason} className="text-xs bg-gray-100 px-2 py-1 rounded">
                {reason}: {count}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
