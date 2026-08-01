import { readFileSync } from 'node:fs'
import path from 'node:path'
import { inflateSync } from 'node:zlib'
import { describe, expect, it } from 'vitest'

const componentDir = __dirname

function decodeRgbaPng(file: string) {
  const png = readFileSync(file)
  expect(png.subarray(1, 4).toString()).toBe('PNG')
  const width = png.readUInt32BE(16)
  const height = png.readUInt32BE(20)
  expect(png[24]).toBe(8)
  expect(png[25]).toBe(6)

  const idat: Buffer[] = []
  for (let offset = 8; offset < png.length;) {
    const length = png.readUInt32BE(offset)
    const type = png.subarray(offset + 4, offset + 8).toString('ascii')
    if (type === 'IDAT') idat.push(png.subarray(offset + 8, offset + 8 + length))
    offset += length + 12
  }

  const packed = inflateSync(Buffer.concat(idat))
  const stride = width * 4
  const pixels = Buffer.alloc(stride * height)
  for (let y = 0, source = 0; y < height; y += 1) {
    const filter = packed[source++]
    for (let x = 0; x < stride; x += 1) {
      const raw = packed[source++]
      const left = x >= 4 ? pixels[y * stride + x - 4] : 0
      const up = y > 0 ? pixels[(y - 1) * stride + x] : 0
      const upperLeft = y > 0 && x >= 4 ? pixels[(y - 1) * stride + x - 4] : 0
      let value = raw
      if (filter === 1) value += left
      else if (filter === 2) value += up
      else if (filter === 3) value += Math.floor((left + up) / 2)
      else if (filter === 4) {
        const estimate = left + up - upperLeft
        const distances = [Math.abs(estimate - left), Math.abs(estimate - up), Math.abs(estimate - upperLeft)]
        value += distances[0] <= distances[1] && distances[0] <= distances[2]
          ? left
          : distances[1] <= distances[2] ? up : upperLeft
      } else expect(filter).toBe(0)
      pixels[y * stride + x] = value & 0xff
    }
  }
  return pixels
}

describe('Diandian dark-theme placement', () => {
  it('anchors the avatar below the topbar and FPS HUD and opens the bubble inward', () => {
    const css = readFileSync(path.join(componentDir, 'diandian.css'), 'utf8')

    expect(css).toMatch(/\.diandian-container\s*\{[^}]*top:\s*calc\(var\(--hermes-top\) \+ 52px\);[^}]*right:\s*12px;/s)
    expect(css).not.toMatch(/\.diandian-container\s*\{[^}]*bottom:/s)
    expect(css).toMatch(/\.diandian-bubble\s*\{[^}]*top:\s*0;[^}]*right:\s*calc\(100% \+ 8px\);/s)
    expect(css).toMatch(/\.diandian-bubble::after\s*\{[^}]*right:\s*-6px;[^}]*border-left:/s)
  })

  it('directs onboarding users to the new avatar location', () => {
    const onboarding = readFileSync(path.join(componentDir, 'DiandianOnboarding.tsx'), 'utf8')

    expect(onboarding).toContain('Hermes 欄位右上角')
    expect(onboarding).not.toContain('右下角找我')
  })

  it.each(['active', 'idle', 'thinking'])('keeps %s transparent, white-lined, and blushing', (state) => {
    const pixels = decodeRgbaPng(path.join(componentDir, '..', '..', 'public', 'diandian', `${state}.png`))
    let transparent = 0
    let white = 0
    let blush = 0
    for (let offset = 0; offset < pixels.length; offset += 4) {
      const [red, green, blue, alpha] = pixels.subarray(offset, offset + 4)
      if (alpha === 0) transparent += 1
      if (alpha > 0 && red > 220 && green > 220 && blue > 220) white += 1
      if (alpha > 40 && red - green > 10 && red - blue > 5) blush += 1
    }
    expect(transparent).toBeGreaterThan(40_000)
    expect(white).toBeGreaterThan(500)
    expect(blush).toBeGreaterThan(100)
  })
})
