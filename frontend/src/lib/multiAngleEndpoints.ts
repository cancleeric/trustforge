/**
 * Multi-angle analysis API endpoints and TypeScript interfaces (#810).
 */

import { secureRandomUuid } from './uuid'

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
  completeness?: number
  missing_fields?: string[]
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
  /** Legacy aggregate; use the three independent fields for presentation. */
  direction_divergences?: AngleConflict[]
  completeness_gaps?: AngleConflict[]
  evidence_overlaps?: AngleConflict[]
  agreement_matrix: Record<string, Record<string, string>>
  synthesis_summary: string
  evidence_independence: number
  limits: string[]
  generated_at: string
  narration?: string
}

/**
 * Normalise pre-separated API payloads without duplicating categories supplied
 * by a current backend. This keeps persisted reports renderable across deploys.
 */
export function normalizeMultiAngleReport(report: MultiAngleReport): MultiAngleReport {
  const conflicts = Array.isArray(report.conflicts) ? report.conflicts : []
  const classified = (type: string) => conflicts.filter((item) => item.conflict_type === type)
  return {
    ...report,
    conflicts,
    direction_divergences: report.direction_divergences ?? classified('direction_divergence'),
    completeness_gaps: report.completeness_gaps ?? classified('completeness_gap'),
    evidence_overlaps: report.evidence_overlaps ?? classified('evidence_overlap'),
  }
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
  const data = await decodeEnvelope<{ multi_angle: MultiAngleReport | null; message?: string }>(res)
  return {
    ...data,
    multi_angle: data.multi_angle ? normalizeMultiAngleReport(data.multi_angle) : null,
  }
}

export async function submitMultiAngle(
  coin: string,
  question?: string,
  locale?: string,
  signal?: AbortSignal,
  idempotencyKey?: string,
): Promise<MultiAngleSubmitResponse> {
  const body: Record<string, string> = { coin }
  if (question) body.question = question
  if (locale) body.locale = locale
  const bodyJson = JSON.stringify(body)
  const stableKey = idempotencyKey ?? secureRandomUuid()
  let res: Response | undefined
  for (let attempt = 0; attempt < 10; attempt += 1) {
    res = await fetch('/api/multi-angle', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': stableKey,
      },
      body: bodyJson,
      signal,
    })
    if (res.status !== 202) break
    await new Promise<void>((resolve, reject) => {
      if (signal?.aborted) {
        reject(new DOMException('Aborted', 'AbortError'))
        return
      }
      let timer: ReturnType<typeof setTimeout>
      const onAbort = () => {
        clearTimeout(timer)
        reject(new DOMException('Aborted', 'AbortError'))
      }
      timer = setTimeout(() => {
        signal?.removeEventListener('abort', onAbort)
        resolve()
      }, Math.min(250 * (2 ** attempt), 2000))
      signal?.addEventListener('abort', onAbort, { once: true })
    })
  }
  if (!res || res.status === 202) {
    throw new MultiAngleApiError(
      'idempotency_request_timeout',
      '五角度請求仍在建立中，請稍後重試',
      202,
    )
  }
  const result = await decodeEnvelope<MultiAngleSubmitResponse>(res)
  return result
}
