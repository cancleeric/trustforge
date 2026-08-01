import { describe, expect, it, vi } from 'vitest'

import { secureRandomUuid } from './uuid'

describe('secureRandomUuid', () => {
  it('uses native randomUUID when available', () => {
    const native = vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue('00000000-0000-4000-8000-000000000001')
    expect(secureRandomUuid()).toBe('00000000-0000-4000-8000-000000000001')
    native.mockRestore()
  })

  it('falls back to getRandomValues with RFC 4122 version and variant bits', () => {
    const original = globalThis.crypto.randomUUID
    Object.defineProperty(globalThis.crypto, 'randomUUID', { configurable: true, value: undefined })
    const random = vi.spyOn(globalThis.crypto, 'getRandomValues').mockImplementation(array => {
      ;(array as Uint8Array).fill(0)
      return array
    })
    expect(secureRandomUuid()).toBe('00000000-0000-4000-8000-000000000000')
    expect(random).toHaveBeenCalledOnce()
    random.mockRestore()
    Object.defineProperty(globalThis.crypto, 'randomUUID', { configurable: true, value: original })
  })
})
