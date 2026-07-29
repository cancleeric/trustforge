import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fetchMultiAngleReport,
  submitMultiAngle,
} from './multiAngleEndpoints'

beforeEach(() => {
  vi.stubGlobal('crypto', {
    randomUUID: () => '00000000-0000-4000-8000-000000000001',
  })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

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

  it('retries a 202 with the same stable key and resolves the original batch', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        ok: true, data: { request_id: 'ma-request-1', state: 'processing' },
      }), { status: 202, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        ok: true,
        data: { coin: 'BTC', snapshot_id: 'snap-1', job_ids: { risk: 'job-1' } },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const pending = submitMultiAngle('BTC')
    await vi.advanceTimersByTimeAsync(250)
    await expect(pending).resolves.toMatchObject({ snapshot_id: 'snap-1' })

    expect(fetchMock).toHaveBeenCalledTimes(2)
    const firstHeaders = fetchMock.mock.calls[0][1].headers
    const secondHeaders = fetchMock.mock.calls[1][1].headers
    expect(secondHeaders['Idempotency-Key']).toBe(firstHeaders['Idempotency-Key'])
  })

  it('bounds perpetual 202 polling', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true, data: { request_id: 'ma-request-stuck', state: 'processing' },
    }), { status: 202, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const pending = submitMultiAngle('BTC')
    const rejection = expect(pending).rejects.toMatchObject({
      code: 'idempotency_request_timeout',
      status: 202,
    })
    await vi.advanceTimersByTimeAsync(20_000)
    await rejection

    expect(fetchMock).toHaveBeenCalledTimes(10)
  })
})
