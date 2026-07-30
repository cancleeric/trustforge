// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  beginFormalRunIntent,
  completeFormalRunIntent,
  formalRunIntent,
  generateFormalRunKey,
  isFormalRunReceipt,
} from './formalRun'

describe('formal-run transport contract', () => {
  beforeEach(() => window.sessionStorage.clear())

  it('generates a canonical UTC-month tf1 key from 128 CSPRNG bits', () => {
    const spy = vi.spyOn(globalThis.crypto, 'getRandomValues').mockImplementation((array) => {
      const bytes = array as Uint8Array
      bytes.forEach((_, index) => { bytes[index] = index })
      return array
    })
    expect(generateFormalRunKey(new Date('2026-07-31T23:59:59Z')))
      .toBe('tf1.202607.AAECAwQFBgcICQoLDA0ODw')
    expect(spy).toHaveBeenCalledOnce()
    spy.mockRestore()
  })

  it('keeps one key and fresh=true across retry and reload until a receipt arrives', () => {
    const visible = formalRunIntent('btc', 'risk', ' question ', 'zh-Hant')
    const first = beginFormalRunIntent('analyze', visible, '7', true, false)
    expect(beginFormalRunIntent('analyze', visible, '7', true, false)).toEqual(first)
    expect(beginFormalRunIntent('analyze', visible, '0', false, true)).toEqual(first)
    expect(first.fresh).toBe(true)
  })

  it('clears a received intent so a later identical intent gets a new key', () => {
    const visible = formalRunIntent('BTC', 'risk', 'question', 'zh-Hant')
    const first = beginFormalRunIntent('analyze', visible, '1', false, false)
    completeFormalRunIntent('analyze', first.key)
    const next = beginFormalRunIntent('analyze', visible, '2', false, false)
    expect(next.key).not.toBe(first.key)
  })

  it('strictly accepts the complete immutable receipt', () => {
    const receipt = {
      schema_version: 'formal-run-receipt/v1',
      receipt_id: 'frc_1',
      question_id: 'q_1',
      job_id: 'job_1',
      result_id: 'result_1',
      state: 'accepted',
      origin: 'manual',
      disposition: 'relocalized',
      locale: 'en',
      created_at: '2026-07-30T08:00:00Z',
      expires_at: null,
      fingerprint_version: 'analysis-question/v1',
    }
    expect(isFormalRunReceipt(receipt)).toBe(true)
    expect(isFormalRunReceipt({ ...receipt, job_id: null })).toBe(false)
    expect(isFormalRunReceipt({ ...receipt, expires_at: '2026-07-31T00:00:00Z' })).toBe(false)
    expect(isFormalRunReceipt({ ...receipt, disposition: 'unknown' })).toBe(false)
    expect(isFormalRunReceipt({ ...receipt, state: 'execution_uncertain', result_id: null })).toBe(true)
  })
})
