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
})
