import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const componentDir = __dirname

describe('Diandian dark-theme placement', () => {
  it('anchors the avatar at the top right and opens the hover bubble downward', () => {
    const css = readFileSync(path.join(componentDir, 'diandian.css'), 'utf8')

    expect(css).toMatch(/\.diandian-container\s*\{[^}]*top:\s*12px;[^}]*right:\s*12px;/s)
    expect(css).not.toMatch(/\.diandian-container\s*\{[^}]*bottom:/s)
    expect(css).toMatch(/\.diandian-bubble\s*\{[^}]*top:\s*calc\(100% \+ 8px\);/s)
    expect(css).toMatch(/\.diandian-bubble::after\s*\{[^}]*top:\s*-6px;[^}]*border-bottom:/s)
  })

  it('directs onboarding users to the new avatar location', () => {
    const onboarding = readFileSync(path.join(componentDir, 'DiandianOnboarding.tsx'), 'utf8')

    expect(onboarding).toContain('Hermes 欄位右上角')
    expect(onboarding).not.toContain('右下角找我')
  })
})
