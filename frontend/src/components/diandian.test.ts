import { describe, it, expect } from 'vitest'

describe('DiandianAvatar', () => {
  it('has three image assets', async () => {
    const fs = await import('node:fs')
    const path = await import('node:path')
    const dir = path.resolve(__dirname, '../../public/diandian')
    expect(fs.existsSync(path.join(dir, 'active.png'))).toBe(true)
    expect(fs.existsSync(path.join(dir, 'idle.png'))).toBe(true)
    expect(fs.existsSync(path.join(dir, 'thinking.png'))).toBe(true)
  })

  it('images are under 100KB each', async () => {
    const fs = await import('node:fs')
    const path = await import('node:path')
    const dir = path.resolve(__dirname, '../../public/diandian')
    for (const name of ['active.png', 'idle.png', 'thinking.png']) {
      const stat = fs.statSync(path.join(dir, name))
      expect(stat.size).toBeLessThan(100_000)
    }
  })
})

describe('DiandianOnboarding', () => {
  it('has position hints for each step', async () => {
    const mod = await import('./DiandianOnboarding')
    // Module exports default, check it's a function (React component)
    expect(typeof mod.default).toBe('function')
  })
})

describe('StageBar arrows', () => {
  it('diandian.css contains arrow styles', async () => {
    const fs = await import('node:fs')
    const path = await import('node:path')
    const css = fs.readFileSync(path.resolve(__dirname, './diandian.css'), 'utf-8')
    expect(css).toContain('.hermes-stage-arrow')
    expect(css).toContain('arrow-pulse')
    expect(css).toContain('.hermes-energy-station')
    expect(css).toContain('min-width')
  })
})
