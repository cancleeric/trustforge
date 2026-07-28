/**
 * Multi-angle analysis API endpoints and TypeScript interfaces (#810).
 */

export interface AngleResult {
  angle: string
  qtype: string
  direction: string
  calibrated_confidence: number
  decision_state: string
  key_basis: string[]
  evidence_sources: string[]
  evidence_count: number
  market_judgment: string
  snapshot_id: string
  job_id: string | null
}

export interface AngleConflict {
  angle_a: string
  angle_b: string
  conflict_type: string
  detail: Record<string, unknown>
  summary: string
}

export interface MultiAngleReport {
  coin: string
  snapshot_id: string
  angles: AngleResult[]
  consensus: string
  consensus_confidence: number
  conflicts: AngleConflict[]
  agreement_matrix: Record<string, Record<string, string>>
  synthesis_summary: string
  evidence_independence: number
  limits: string[]
  generated_at: string
  narration?: string
}

export interface MultiAngleSubmitResponse {
  snapshot_id: string
  job_ids: Record<string, string | null>
  coin: string
}

interface ApiEnvelope<T> {
  ok: boolean
  data?: T
  error?: { code: string; message: string }
}

export async function fetchMultiAngleReport(
  coin: string,
  snapshotId?: string,
): Promise<{ multi_angle: MultiAngleReport | null; message?: string } | null> {
  const params = new URLSearchParams({ coin })
  if (snapshotId) params.set('snapshot_id', snapshotId)
  const res = await fetch(`/api/multi-angle?${params}`)
  if (!res.ok) return null
  const envelope: ApiEnvelope<{ multi_angle: MultiAngleReport | null; message?: string }> = await res.json()
  return envelope.ok ? (envelope.data ?? null) : null
}

export async function submitMultiAngle(
  coin: string,
  question?: string,
  locale?: string,
): Promise<MultiAngleSubmitResponse | null> {
  const body: Record<string, string> = { coin }
  if (question) body.question = question
  if (locale) body.locale = locale
  const res = await fetch('/api/multi-angle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) return null
  const envelope: ApiEnvelope<MultiAngleSubmitResponse> = await res.json()
  return envelope.ok ? (envelope.data ?? null) : null
}
