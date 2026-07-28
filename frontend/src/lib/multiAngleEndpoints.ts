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
  question: string
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
  decision_state: 'normal' | 'partial_abstain' | 'full_abstain'
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
  job_ids: Record<string, string>
  coin: string
}

interface ApiEnvelope<T> {
  ok: boolean
  data?: T
  error?: { code: string; message: string }
}

export class MultiAngleApiError extends Error {
  code: string
  status: number

  constructor(code: string, message: string, status: number) {
    super(message)
    this.name = 'MultiAngleApiError'
    this.code = code
    this.status = status
  }
}

async function decodeEnvelope<T>(res: Response): Promise<T> {
  let envelope: ApiEnvelope<T>
  try {
    envelope = await res.json() as ApiEnvelope<T>
  } catch {
    throw new MultiAngleApiError('invalid_response', '伺服器回應格式錯誤', res.status)
  }
  if (!res.ok || !envelope.ok || envelope.data === undefined) {
    throw new MultiAngleApiError(
      envelope.error?.code ?? 'request_failed',
      envelope.error?.message ?? `請求失敗 (${res.status})`,
      res.status,
    )
  }
  return envelope.data
}

export async function fetchMultiAngleReport(
  coin: string,
  snapshotId?: string,
  signal?: AbortSignal,
): Promise<{ multi_angle: MultiAngleReport | null; message?: string }> {
  const params = new URLSearchParams({ coin })
  if (snapshotId) params.set('snapshot_id', snapshotId)
  const res = await fetch(`/api/multi-angle?${params}`, { signal })
  return decodeEnvelope<{ multi_angle: MultiAngleReport | null; message?: string }>(res)
}

export async function submitMultiAngle(
  coin: string,
  question?: string,
  locale?: string,
  signal?: AbortSignal,
): Promise<MultiAngleSubmitResponse> {
  const body: Record<string, string> = { coin }
  if (question) body.question = question
  if (locale) body.locale = locale
  const res = await fetch('/api/multi-angle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  return decodeEnvelope<MultiAngleSubmitResponse>(res)
}
