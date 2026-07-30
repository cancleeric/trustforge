export const FORMAL_RUN_RECEIPT_SCHEMA = 'formal-run-receipt/v1' as const
export const FORMAL_RUN_FINGERPRINT_VERSION = 'analysis-question/v1' as const

export type FormalRunDisposition = 'created' | 'reused' | 'relocalized' | 'fresh-created'

export interface FormalRunReceipt {
  schema_version: typeof FORMAL_RUN_RECEIPT_SCHEMA
  receipt_id: string
  question_id: string
  job_id: string
  result_id: string | null
  state: 'accepted' | 'execution_uncertain'
  origin: 'manual'
  disposition: FormalRunDisposition
  locale: 'zh-Hant' | 'en'
  created_at: string
  expires_at: null
  fingerprint_version: typeof FORMAL_RUN_FINGERPRINT_VERSION
}

const DISPOSITIONS = new Set<FormalRunDisposition>([
  'created',
  'reused',
  'relocalized',
  'fresh-created',
])
const LOCALES = new Set<FormalRunReceipt['locale']>(['zh-Hant', 'en'])
const ISO_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/

export function isFormalRunReceipt(value: unknown): value is FormalRunReceipt {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const receipt = value as Record<string, unknown>
  return (
    receipt.schema_version === FORMAL_RUN_RECEIPT_SCHEMA
    && typeof receipt.receipt_id === 'string' && ID.test(receipt.receipt_id)
    && typeof receipt.question_id === 'string' && ID.test(receipt.question_id)
    && typeof receipt.job_id === 'string' && ID.test(receipt.job_id)
    && (receipt.result_id === null || (typeof receipt.result_id === 'string' && ID.test(receipt.result_id)))
    && (receipt.state === 'accepted' || receipt.state === 'execution_uncertain')
    && receipt.origin === 'manual'
    && typeof receipt.disposition === 'string'
    && DISPOSITIONS.has(receipt.disposition as FormalRunDisposition)
    && typeof receipt.locale === 'string'
    && LOCALES.has(receipt.locale as FormalRunReceipt['locale'])
    && typeof receipt.created_at === 'string'
    && ISO_UTC.test(receipt.created_at)
    && !Number.isNaN(Date.parse(receipt.created_at))
    && receipt.expires_at === null
    && receipt.fingerprint_version === FORMAL_RUN_FINGERPRINT_VERSION
  )
}

function base64Url(bytes: Uint8Array): string {
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/u, '')
}

export function generateFormalRunKey(now = new Date()): string {
  if (!globalThis.crypto?.getRandomValues) {
    throw new Error('Secure random generation is unavailable')
  }
  const bytes = new Uint8Array(16)
  globalThis.crypto.getRandomValues(bytes)
  const epoch = `${now.getUTCFullYear()}${String(now.getUTCMonth() + 1).padStart(2, '0')}`
  return `tf1.${epoch}.${base64Url(bytes)}`
}

interface StoredIntent {
  version: 1
  visible_intent: string
  client_intent_id: string
  fresh: boolean
  key: string
  unresolved: true
}

const STORAGE_PREFIX = 'trustforge:formal-run-intent:'
const KEY_PATTERN = /^tf1\.\d{6}\.[A-Za-z0-9_-]{22}$/

function storageKey(intentSlot: string): string {
  return `${STORAGE_PREFIX}${intentSlot}`
}

export interface ActiveFormalRunIntent {
  key: string
  fresh: boolean
}

/**
 * Begin or resume a browser intent. `resume=true` is reserved for the initial
 * mount after a reload: if the same visible fields have an unresolved record,
 * its original fresh flag and key win over reset React state.
 */
export function beginFormalRunIntent(
  intentSlot: string,
  visibleIntent: string,
  clientIntentId: string,
  requestedFresh: boolean,
  resume: boolean,
): ActiveFormalRunIntent {
  const key = storageKey(intentSlot)
  try {
    const raw = window.sessionStorage.getItem(key)
    if (raw) {
      const stored = JSON.parse(raw) as Partial<StoredIntent>
      const valid = stored.version === 1
        && stored.unresolved === true
        && stored.visible_intent === visibleIntent
        && typeof stored.client_intent_id === 'string'
        && typeof stored.fresh === 'boolean'
        && typeof stored.key === 'string'
        && KEY_PATTERN.test(stored.key)
      if (valid && (resume || stored.client_intent_id === clientIntentId)) {
        return { key: stored.key as string, fresh: stored.fresh as boolean }
      }
    }
  } catch {
    // A blocked/corrupt session store must not weaken key generation.
  }
  const generated = generateFormalRunKey()
  try {
    window.sessionStorage.setItem(key, JSON.stringify({
      version: 1,
      visible_intent: visibleIntent,
      client_intent_id: clientIntentId,
      fresh: requestedFresh,
      key: generated,
      unresolved: true,
    } satisfies StoredIntent))
  } catch {
    // The in-memory caller still retains this value for its current attempt.
  }
  return { key: generated, fresh: requestedFresh }
}

export function completeFormalRunIntent(intentSlot: string, completedKey: string): void {
  try {
    const key = storageKey(intentSlot)
    const raw = window.sessionStorage.getItem(key)
    if (!raw) return
    const stored = JSON.parse(raw) as Partial<StoredIntent>
    if (stored.key === completedKey) window.sessionStorage.removeItem(key)
  } catch {
    // Completion is already durable server-side; storage cleanup is best effort.
  }
}

export function formalRunIntent(
  coin: string,
  mode: string,
  question: string,
  locale: FormalRunReceipt['locale'],
): string {
  return JSON.stringify({
    coin: coin.trim().toUpperCase(),
    mode: mode.trim(),
    question: question.trim(),
    locale,
  })
}
