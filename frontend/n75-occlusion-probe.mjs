// N75：通用遮蔽審計。N74 的頂欄 stacking context 是一整類問題（層級寫在
// 錯的節點上），不會只有一處。這支走過每個模組畫面，對每個可見按鈕做
// elementsFromPoint，看它的點擊點是不是被別的元素蓋住＝使用者按不到。
import { chromium } from 'playwright'

const browser = await chromium.launch()
const page = await browser.newPage()
const problems = []

for (const [w, h] of [[900, 760], [1280, 800], [1440, 900]]) {
  await page.setViewportSize({ width: w, height: h })
  await page.goto('http://localhost:4175/?qa=1&reducedMotion=1', { waitUntil: 'domcontentloaded' })
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2300)

  const navCount = await page.locator('.hermes-nav-item').count()
  for (let i = 0; i < navCount; i++) {
    const nav = page.locator('.hermes-nav-item').nth(i)
    const name = (await nav.innerText()).trim().slice(0, 12)
    await nav.click()
    await page.waitForTimeout(650)
    const covered = await page.evaluate(() => {
      const out = []
      for (const btn of document.querySelectorAll('button, a[href], input, select')) {
        const b = btn.getBoundingClientRect()
        if (b.width < 6 || b.height < 6) continue
        if (b.top < 0 || b.left < 0 || b.bottom > window.innerHeight || b.right > window.innerWidth) continue
        const cs = getComputedStyle(btn)
        if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) < 0.05) continue
        if (cs.pointerEvents === 'none') continue
        const stack = document.elementsFromPoint(b.left + b.width / 2, b.top + b.height / 2)
        const idx = stack.indexOf(btn)
        if (idx <= 0) continue
        const above = stack.slice(0, idx).filter((e) => !btn.contains(e) && !e.contains(btn))
        if (above.length) {
          out.push(`${(btn.textContent || btn.className || btn.tagName).toString().trim().slice(0, 20)} ← ${above[0].className?.toString().slice(0, 30) || above[0].tagName}`)
        }
      }
      return out
    })
    if (covered.length) problems.push(`${w}x${h} [${name}]: ${[...new Set(covered)].slice(0, 4).join(' ; ')}`)
  }
  console.log(`${w}x${h} 掃過 ${navCount} 個模組`)
}

await browser.close()
if (problems.length) { console.log('\n遮蔽問題：'); problems.forEach((p) => console.log('  ✗ ' + p)); process.exit(1) }
console.log('\nN75 OK')
