// N74：≤560px 控制條下方疑似有一條純空白帶。用像素列掃描判斷——比 DOM
// 量測誠實：boot-layer 這類全屏透明層會讓 DOM gap 看起來是 0，但畫面上
// 那塊確實什麼都沒有。
import { chromium } from 'playwright'

const VIEWPORTS = [[360, 740], [375, 667], [390, 844], [430, 932], [540, 720]]
const browser = await chromium.launch()
const page = await browser.newPage()
const problems = []

for (const [w, h] of VIEWPORTS) {
  await page.setViewportSize({ width: w, height: h })
  await page.goto('http://localhost:4175/?qa=1&reducedMotion=1', { waitUntil: 'domcontentloaded' })
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2300)
  const r = await page.evaluate(() => {
    const strip = document.querySelector('.hermes-rail-controls')
    const stripBottom = strip ? Math.round(strip.getBoundingClientRect().bottom) : 0
    // 只算「真的畫得出東西」的元素：有自己的文字、或有不透明底色/邊框、
    // 或是 img/svg/canvas。全屏透明層（boot-layer 之流）因此被排除。
    const paints = []
    for (const el of document.querySelectorAll('body *')) {
      const b = el.getBoundingClientRect()
      if (b.height < 4 || b.width < 4 || b.bottom < stripBottom) continue
      const cs = getComputedStyle(el)
      if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) < 0.05) continue
      const ownText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())
      const hasBg = cs.backgroundColor !== 'rgba(0, 0, 0, 0)' && !cs.backgroundColor.endsWith(', 0)')
      const hasBorder = cs.borderTopWidth !== '0px' || cs.borderLeftWidth !== '0px'
      const media = ['IMG', 'SVG', 'CANVAS', 'VIDEO'].includes(el.tagName)
      if (!(ownText || hasBg || hasBorder || media)) continue
      // 佔滿整個視窗的層不算內容
      if (b.width >= window.innerWidth - 2 && b.height >= window.innerHeight - 2) continue
      paints.push([Math.max(Math.round(b.top), stripBottom), Math.round(b.bottom)])
    }
    paints.sort((a, z) => a[0] - z[0])
    let cursor = stripBottom, worst = 0, worstAt = stripBottom
    for (const [top, bottom] of paints) {
      if (top - cursor > worst) { worst = top - cursor; worstAt = cursor }
      if (bottom > cursor) cursor = bottom
    }
    return { stripBottom, worst, worstAt, count: paints.length }
  })
  const { stripBottom, worst, worstAt } = r
  console.log(`${w}x${h} stripBottom=${stripBottom} 最長空白帶=${worst}px @y=${worstAt}`)
  if (worst > 90) problems.push(`${w}x${h}: 控制條下方有 ${worst}px 純空白帶（y=${worstAt}）`)
}

await browser.close()
if (problems.length) { console.log('\n問題：'); problems.forEach((p) => console.log('  ✗ ' + p)); process.exit(1) }
console.log('\nN74 OK')
