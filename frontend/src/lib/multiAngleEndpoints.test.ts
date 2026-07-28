import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  fetchMultiAngleReport,
  submitMultiAngle,
} from './multiAngleEndpoints'

afterEach(() => vi.unstubAllGlobals())

describe('multi-angle API errors', () => {
  it('preserves a structured non-2xx error instead of returning null', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: false,
      error: { code: 'multi_angle_queue_unavailable', message: 'full' },
    }), { status: 503, headers: { 'Content-Type': 'application/json' } })))

    await expect(submitMultiAngle('BTC')).rejects.toMatchObject({
      code: 'multi_angle_queue_unavailable',
      message: 'full',
      status: 503,
    })
  })

  it('returns a typed successful report envelope', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      data: { multi_angle: null, message: 'pending' },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))

    await expect(fetchMultiAngleReport('BTC', 'snap-1')).resolves.toEqual({
      multi_angle: null,
      message: 'pending',
    })
  })

  it('rejects with AbortError when signal is aborted', async () => {
    const controller = new AbortController()
    controller.abort()
    vi.stubGlobal('fetch', vi.fn((_url: string, opts?: RequestInit) => {
      if (opts?.signal?.aborted) throw new DOMException('Aborted', 'AbortError')
      return Promise.resolve(new Response('{}', { status: 200 }))
    }))
    await expect(submitMultiAngle('BTC', undefined, undefined, controller.signal)).rejects.toThrow()
  })

  it('preserves status and code in error for different HTTP errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: false,
      error: { code: 'budget_exhausted', message: 'No budget' },
    }), { status: 429, headers: { 'Content-Type': 'application/json' } })))
    await expect(submitMultiAngle('BTC')).rejects.toMatchObject({
      code: 'budget_exhausted', message: 'No budget', status: 429,
    })
  })
})
