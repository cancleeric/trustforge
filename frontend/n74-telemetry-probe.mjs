// N74（CEO：「這疊到」）：市場遙測膠囊展開的面板被右邊的工作區面板蓋住。
import { chromium } from 'playwright'
const browser = await chromium.launch()
const page = await browser.newPage()
const problems = []
for (const [w, h] of [[900, 760], [1024, 700], [1280, 800], [1440, 900]]) {
  await page.setViewportSize({ width: w, height: h })
  await page.goto('http://localhost:4175/?qa=1&reducedMotion=1', { waitUntil: 'domcontentloaded' })
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2300)
  // 截圖是在「分析工作區」開著的狀態下發生的，先進 module 再開膠囊。
  const nav = page.locator('.hermes-nav-item', { hasText: '分析' }).first()
  if (await nav.count()) { await nav.click(); await page.waitForTimeout(700) }
  await page.locator('.hermes-telemetry-chip').click()
  await page.waitForTimeout(250)
  const r = await page.evaluate(() => {
    const p = document.getElementById('hermes-telemetry-panel')
    if (!p) return { missing: true }
    const b = p.getBoundingClientRect()
    const pts = [[b.left + 8, b.top + 8], [b.left + b.width / 2, b.top + b.height / 2], [b.right - 8, b.bottom - 8]]
    const covered = pts.map(([x, y]) => {
      const stack = document.elementsFromPoint(x, y)
      const idx = stack.indexOf(p)
      const above = idx > 0 ? stack.slice(0, idx).filter((e) => !p.contains(e)) : []
      return above.length ? (above[0].className?.toString().slice(0, 44) || above[0].tagName) : null
    })
    return { z: getComputedStyle(p).zIndex, rect: [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)], covered }
  })
  console.log(`${w}x${h} z=${r.z} rect=${r.rect} 被蓋=${JSON.stringify(r.covered)}`)
  const bad = (r.covered || []).filter(Boolean)
  if (bad.length) problems.push(`${w}x${h}: 遙測面板被 ${[...new Set(bad)].join(' / ')} 蓋住`)
}
await browser.close()
if (problems.length) { console.log('\n問題：'); problems.forEach((p) => console.log('  ✗ ' + p)); process.exit(1) }
console.log('\nN74 OK')
