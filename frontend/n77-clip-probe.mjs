// N77：掃「內容被自己的框裁掉」——按鈕/標籤文字被 overflow:hidden 切掉、
// 元素跑出視窗、整頁橫向溢出。與 N74/N75 的「被別人蓋住」是不同機制。
import { chromium } from 'playwright'
const VIEWPORTS = [[375,667],[430,932],[561,700],[768,1024],[900,760],[1024,900],[1280,800],[1440,900]]
const browser = await chromium.launch()
const problems = []
for (const [w, h] of VIEWPORTS) {
  // 每個尺寸開一支新的 page。共用同一支跑完八個尺寸時，切換幾次之後 goto 會固定
  // 逾時然後整支崩掉——同時間 curl 打 dev server 只要幾 ms，卡住的是這支 page
  // 不是伺服器。開新的最省事，也順便清掉殘留狀態。（與 N80 探針同一處理。）
  const page = await browser.newPage()
  await page.setViewportSize({ width: w, height: h })
  const boot = async () => {
    await page.goto('http://localhost:4175/?qa=1&reducedMotion=1', { waitUntil: 'domcontentloaded', timeout: 60000 })
    await page.context().addCookies([{ name: 'trustforge_hermes_locale', value: 'zh-TW', url: 'http://localhost:4175' }])
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 60000 })
  }
  try { await boot() } catch { await page.waitForTimeout(3000); await boot() }
  await page.waitForTimeout(2300)
  // 缺陷多半在工作區模組裡，首頁掃不到——走過每個模組各掃一次。
  const navs = await page.locator('.hermes-nav-item').all()
  const states = [null, ...navs.keys()]
  for (const idx of states) {
   if (idx !== null) {
     const n = navs[idx]
     if (!(await n.isVisible().catch(() => false))) continue
     await n.click().catch(() => {})
     await page.waitForTimeout(700)
   }
   const modName = idx === null ? '首頁' : ((await navs[idx].textContent().catch(() => '')) || '').trim().slice(0, 8)
  const r = await page.evaluate(() => {
    const out = { clipped: [], offscreen: [], docOverflow: document.documentElement.scrollWidth - window.innerWidth }
    for (const el of document.querySelectorAll('button, a[href], [role=button], label, h1, h2, h3')) {
      const cs = getComputedStyle(el)
      const b = el.getBoundingClientRect()
      if (!b.width || !b.height || cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) < 0.05) continue
      const label = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 26)
      if (!label) continue
      // 文字被自己的框裁掉：容器不捲（hidden/clip）且內容比框大。
      const ovx = cs.overflowX, ovy = cs.overflowY
      const cutX = (ovx === 'hidden' || ovx === 'clip') && el.scrollWidth > el.clientWidth + 1
      const cutY = (ovy === 'hidden' || ovy === 'clip') && el.scrollHeight > el.clientHeight + 1
      // text-overflow:ellipsis 是刻意的截斷，不算缺陷。
      const intentional = cs.textOverflow === 'ellipsis'
      if ((cutX || cutY) && !intentional) out.clipped.push(`「${label}」${cutX ? `橫切 ${el.scrollWidth - el.clientWidth}px` : ''}${cutY ? `直切 ${el.scrollHeight - el.clientHeight}px` : ''}`)
      // 跑出視窗，且沒有被會捲的祖先收容。
      if (b.right > window.innerWidth + 1 || b.left < -1) {
        let scrollable = false
        for (let p = el.parentElement; p; p = p.parentElement) {
          const o = getComputedStyle(p).overflowX
          if (o === 'auto' || o === 'scroll') { scrollable = true; break }
        }
        if (!scrollable) out.offscreen.push(`「${label}」${b.left < -1 ? `左出 ${Math.round(-b.left)}px` : `右出 ${Math.round(b.right - window.innerWidth)}px`}`)
      }
    }
    out.clipped = [...new Set(out.clipped)].slice(0, 8)
    out.offscreen = [...new Set(out.offscreen)].slice(0, 8)
    return out
  })
  const tag = `${w}x${h} ${modName}`
  console.log(`${tag} clipped=${r.clipped.length} offscreen=${r.offscreen.length} docOverflow=${r.docOverflow}`)
  for (const c of r.clipped) problems.push(`${tag}: 文字被自己的框裁掉 → ${c}`)
  for (const o of r.offscreen) problems.push(`${tag}: 元素跑出視窗 → ${o}`)
  if (r.docOverflow > 0) problems.push(`${tag}: 整頁橫向溢出 ${r.docOverflow}px`)
  }
  await page.close()
}
await browser.close()
if (problems.length) { console.log('\nN77 問題：'); for (const p of problems) console.log('  ✗ ' + p); process.exit(1) }
console.log('\nN77 OK')
