import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

describe('#1271 Right rail layout CSS', () => {
  const css = readFileSync(resolve(__dirname, './diandian.css'), 'utf-8')

  it('has right-rail fixed top styles', () => {
    expect(css).toContain('.hermes-right-rail-fixed')
    expect(css).toContain('position: sticky')
    expect(css).toContain('border: none')
  })

  it('has right-rail scroll styles', () => {
    expect(css).toContain('overflow-y: auto')
    expect(css).toContain('scrollbar-width: thin')
  })

  it('has left-rail narrow styles', () => {
    expect(css).toContain('.hermes-left-rail')
    expect(css).toContain('text-overflow: ellipsis')
    expect(css).toContain('--hermes-rail: clamp(190px, 16vw, 240px)')
    expect(css).toContain('--hermes-right-rail: clamp(220px, 22vw, 320px)')
  })
})

describe('#1270 Collapsible + data viz CSS', () => {
  const css = readFileSync(resolve(__dirname, './diandian.css'), 'utf-8')

  it('has collapsible section styles', () => {
    expect(css).toContain('.trustforge-collapse')
    expect(css).toContain('.trustforge-collapse summary')
    expect(css).toContain('.trustforge-collapse[open]')
  })

  it('has progress bar styles', () => {
    expect(css).toContain('.trustforge-progress')
    expect(css).toContain('.trustforge-progress-fill.high')
    expect(css).toContain('.trustforge-progress-fill.mid')
    expect(css).toContain('.trustforge-progress-fill.low')
  })

  it('has bar chart styles', () => {
    expect(css).toContain('.trustforge-bar-chart')
    expect(css).toContain('.trustforge-bar-fill')
  })

  it('has evidence table styles', () => {
    expect(css).toContain('.trustforge-evidence-table')
  })

  it('has kind tags styles', () => {
    expect(css).toContain('.trustforge-kind-tags')
  })
})

describe('CollapsibleSection component', () => {
  it('exports a function', async () => {
    const mod = await import('./CollapsibleSection')
    expect(typeof mod.default).toBe('function')
  })
})

describe('TrustProgressBar component', () => {
  it('exports a function', async () => {
    const mod = await import('./TrustProgressBar')
    expect(typeof mod.default).toBe('function')
  })
})

describe('TrustBarChart component', () => {
  it('exports a function', async () => {
    const mod = await import('./TrustBarChart')
    expect(typeof mod.default).toBe('function')
  })
})
