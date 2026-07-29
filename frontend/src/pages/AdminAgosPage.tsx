/**
 * Agent OS Admin Page — Admin-only view for Memory/Skill/Tool/Context rails.
 *
 * Route: /admin/agos (not in public navigation)
 * Issue: #924 | Epic: #914
 */
import { useState } from 'react'
import { loadSessionToken } from '../lib/adminConsole'
import type {
  AgosContextManifest,
  AgosMemoryItem,
  AgosSkillItem,
  AgosToolItem,
} from '../lib/agosTypes'
import { AgosContextRail } from '../components/admin/AgosContextRail'
import { AgosMemoryRail } from '../components/admin/AgosMemoryRail'
import { AgosSkillRail } from '../components/admin/AgosSkillRail'
import { AgosToolRail } from '../components/admin/AgosToolRail'

type TabId = 'memory' | 'skill' | 'tool' | 'context'

const TABS: { id: TabId; label: string }[] = [
  { id: 'memory', label: 'Memory' },
  { id: 'skill', label: 'Skills' },
  { id: 'tool', label: 'Tools' },
  { id: 'context', label: 'Context' },
]

interface FetchState<T> {
  data: T | null
  loading: boolean
  error: string | null
}

export default function AdminAgosPage() {
  const [activeTab, setActiveTab] = useState<TabId>('memory')
  const [runId, setRunId] = useState('')
  const [searchRunId, setSearchRunId] = useState('')

  // Fetch states
  const [memories, setMemories] = useState<FetchState<AgosMemoryItem[]>>({
    data: null, loading: false, error: null,
  })
  const [skills, setSkills] = useState<FetchState<AgosSkillItem[]>>({
    data: null, loading: false, error: null,
  })
  const [tools, setTools] = useState<FetchState<AgosToolItem[]>>({
    data: null, loading: false, error: null,
  })
  const [context, setContext] = useState<FetchState<AgosContextManifest>>({
    data: null, loading: false, error: null,
  })

  const token = loadSessionToken() || ''

  const fetchData = async (rid: string) => {
    if (!rid.trim()) return
    setSearchRunId(rid)

    const headers: Record<string, string> = {
      Authorization: `Bearer ${token}`,
    }
    const base = `/api/admin/agos`

    // Fetch all in parallel
    setMemories({ data: null, loading: true, error: null })
    setSkills({ data: null, loading: true, error: null })
    setTools({ data: null, loading: true, error: null })
    setContext({ data: null, loading: true, error: null })

    try {
      const [memRes, skillRes, toolRes, ctxRes] = await Promise.allSettled([
        fetch(`${base}/memories?run_id=${encodeURIComponent(rid)}`, { headers }),
        fetch(`${base}/skills?run_id=${encodeURIComponent(rid)}`, { headers }),
        fetch(`${base}/tools?run_id=${encodeURIComponent(rid)}`, { headers }),
        fetch(`${base}/context?run_id=${encodeURIComponent(rid)}`, { headers }),
      ])

      // Process memories
      if (memRes.status === 'fulfilled' && memRes.value.ok) {
        const json = await memRes.value.json()
        setMemories({ data: json.data?.items || [], loading: false, error: null })
      } else {
        setMemories({ data: [], loading: false, error: 'Failed to fetch memories' })
      }

      // Process skills
      if (skillRes.status === 'fulfilled' && skillRes.value.ok) {
        const json = await skillRes.value.json()
        setSkills({ data: json.data?.items || [], loading: false, error: null })
      } else {
        setSkills({ data: [], loading: false, error: 'Failed to fetch skills' })
      }

      // Process tools
      if (toolRes.status === 'fulfilled' && toolRes.value.ok) {
        const json = await toolRes.value.json()
        setTools({ data: json.data?.items || [], loading: false, error: null })
      } else {
        setTools({ data: [], loading: false, error: 'Failed to fetch tools' })
      }

      // Process context
      if (ctxRes.status === 'fulfilled' && ctxRes.value.ok) {
        const json = await ctxRes.value.json()
        if (json.status === 'ok') {
          setContext({ data: json.data, loading: false, error: null })
        } else {
          setContext({ data: null, loading: false, error: json.error?.message || 'Not found' })
        }
      } else {
        setContext({ data: null, loading: false, error: 'Failed to fetch context' })
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Network error'
      setMemories({ data: [], loading: false, error: msg })
      setSkills({ data: [], loading: false, error: msg })
      setTools({ data: [], loading: false, error: msg })
      setContext({ data: null, loading: false, error: msg })
    }
  }

  return (
    <div className="max-w-6xl mx-auto p-4 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Agent OS Admin</h1>
        <p className="text-sm text-gray-500 mt-1">
          Read-only view of memory, skill, tool, and context lineage data.
        </p>
      </div>

      {/* Run ID Input */}
      <div className="flex gap-2">
        <input
          type="text"
          value={runId}
          onChange={e => setRunId(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && fetchData(runId)}
          placeholder="Enter run_id..."
          className="flex-1 px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
          aria-label="Run ID"
        />
        <button
          onClick={() => fetchData(runId)}
          disabled={!runId.trim()}
          className="px-4 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Query
        </button>
      </div>

      {searchRunId && (
        <p className="text-xs text-gray-500">Showing data for run: <code>{searchRunId}</code></p>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex -mb-px" aria-label="Agent OS tabs">
          {TABS.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-4 py-2 text-sm font-medium border-b-2 ${activeTab === tab.id
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
              aria-selected={activeTab === tab.id}
              role="tab"
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab Content */}
      <div role="tabpanel">
        {activeTab === 'memory' && (
          <AgosMemoryRail
            items={memories.data || []}
            loading={memories.loading}
            error={memories.error}
          />
        )}
        {activeTab === 'skill' && (
          <AgosSkillRail
            items={skills.data || []}
            loading={skills.loading}
            error={skills.error}
          />
        )}
        {activeTab === 'tool' && (
          <AgosToolRail
            items={tools.data || []}
            loading={tools.loading}
            error={tools.error}
          />
        )}
        {activeTab === 'context' && (
          <AgosContextRail
            manifest={context.data}
            loading={context.loading}
            error={context.error}
          />
        )}
      </div>
    </div>
  )
}
