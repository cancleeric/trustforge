/**
 * Agent OS Admin UI type definitions.
 * Maps to backend agos_admin_api.py response shapes.
 *
 * Issue: #924 | Epic: #914
 */

// ─── Memory ─────────────────────────────────────────────────────────────────

export interface AgosMemoryItem {
  memory_id: string
  kind: 'episodic' | 'semantic' | 'procedural' | 'dialogue'
  provider: string
  evidence_eligible: boolean
  content_ref: string
  retrieved_at: string
}

// ─── Skill ──────────────────────────────────────────────────────────────────

export interface AgosSkillItem {
  skill_id: string
  revision_hash: string
  reason: string
}

// ─── Tool ───────────────────────────────────────────────────────────────────

export interface AgosToolItem {
  invocation_id: string
  tool_id: string
  input_hash: string
  output_hash: string | null
  status: 'pending' | 'success' | 'failed' | 'timeout' | 'rejected'
  error: string | null
  started_at: string
  completed_at: string | null
}

// ─── Context ────────────────────────────────────────────────────────────────

export interface AgosContextManifest {
  manifest_id: string
  run_id: string
  content_hash: string
  token_budget: number
  token_used: number
  created_at: string
  included_count: number
  excluded_count: number
  exclusion_reasons: Record<string, number>
  included_refs: {
    snapshot_ref: string | null
    question_ref: string | null
    memory_refs: Array<{
      memory_id: string
      kind: string
      rank: number
      reason: string
      evidence_eligible: boolean
    }>
    skill_refs: Array<{ skill_id: string; revision_hash: string; reason: string }>
    tool_refs: Array<{ tool_id: string }>
    policy_refs: Array<Record<string, string>>
  }
  excluded_refs: Array<{ ref_id: string; ref_type: string; reason: string }>
}

// ─── Paginated Response ─────────────────────────────────────────────────────

export interface AgosPaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

// ─── Admin API Envelope ─────────────────────────────────────────────────────

export interface AgosAdminSuccess<T> {
  status: 'ok'
  data: T
  timestamp: string
}

export interface AgosAdminError {
  status: 'error'
  error: { code: string; message: string }
  timestamp: string
}

export type AgosAdminResponse<T> = AgosAdminSuccess<T> | AgosAdminError

// ─── Badge Variants ─────────────────────────────────────────────────────────

export type AgosBadgeVariant =
  | 'historical'
  | 'candidate'
  | 'trusted'
  | 'proposal'
  | 'risk-read'
  | 'risk-local'
  | 'risk-external'
  | 'risk-deploy'
  | 'status-success'
  | 'status-failed'
  | 'status-pending'
  | 'status-timeout'
