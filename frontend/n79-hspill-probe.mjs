// N79：掃「橫向溢出」——元素右緣超出視窗寬度，逼出水平捲軸或把內容推到畫面外。
// 與 n75（被蓋住）、n77（被裁切）、n78（文字疊文字）都不同：這是「根本不在畫面上」。
// 判準分兩層：
//   (a) documentElement.scrollWidth > innerWidth ＝ 整頁真的可以左右捲，一定是缺陷；
//   (b) 個別元素的可視矩形右緣超出視窗，且它沒有被「會橫向捲動的祖先」正當地裁切
//       （橫捲容器裡的內容超出是設計，例如 chip 列；頁面層級的超出才是缺陷）。
import { chromium } from 'playwright'
const VIEWPORTS = [[320,568],[375,667],[430,932],[561,700],[768,1024],[1024,900],[1280,800],[1440,900]]
const browser = await chromium.launch()
const problems = []
for (const [w, h] of VIEWPORTS) {
  // 每個尺寸開一支新的 page。共用同一支跑完所有尺寸時，切換幾次之後 goto 會固定
  // 逾時然後整支崩掉——同時間 curl 打 dev server 只要幾 ms，卡住的是這支 page
  // 不是伺服器。（與 N77/N80 探針同一處理。）
  const page = await browser.newPage()
  await page.setViewportSize({ width: w, height: h })
  const boot = async () => {
    await page.goto('http://localhost:4175/?qa=1&reducedMotion=1', { waitUntil: 'domcontentloaded', timeout: 60000 })
    await page.context().addCookies([{ name: 'trustforge_hermes_locale', value: 'zh-TW', url: 'http://localhost:4175' }])
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 60000 })
  }
  try { await boot() } catch { await page.waitForTimeout(3000); await boot() }
  await page.waitForTimeout(2300)
  const navs = await page.locator('.hermes-nav-item').all()
  for (const idx of [null, ...navs.keys()]) {
    if (idx !== null) {
      if (!(await navs[idx].isVisible().catch(() => false))) continue
      await navs[idx].click().catch(() => {})
      await page.waitForTimeout(700)
    }
    const mod = idx === null ? '首頁' : ((await navs[idx].textContent().catch(() => '')) || '').trim().slice(0, 8)
    // 自我驗證：注入一塊必然橫向溢出的元素，證明探針抓得到。
    if (process.env.TF_INJECT === '1') {
      await page.evaluate(() => {
        if (document.getElementById('tf-inject')) return
        const d = document.createElement('div')
        d.id = 'tf-inject'
        d.textContent = '合成橫向溢出'
        d.style.cssText = 'position:absolute;left:0;top:120px;width:' + (innerWidth + 240) + 'px;height:24px;background:#333;color:#fff'
        document.body.appendChild(d)
      })
      await page.waitForTimeout(120)
    }
    const hits = await page.evaluate(() => {
      const out = []
      const de = document.documentElement
      // 1px 容差：sub-pixel 排版常有 0.x 的殘差，不是缺陷。
      if (de.scrollWidth > innerWidth + 1) out.push(`整頁可左右捲：scrollWidth ${de.scrollWidth} > 視窗 ${innerWidth}`)
      for (const el of document.querySelectorAll('body *')) {
        const cs = getComputedStyle(el)
        if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) < 0.35) continue
        if (cs.position === 'fixed') continue // 固定層自己貼邊是常態
        const b = el.getBoundingClientRect()
        if (b.width < 8 || b.height < 8) continue
        if (b.right <= innerWidth + 2) continue
        // 祖先若橫向可捲，超出的部分是那個容器的內部捲動範圍，屬於設計。
        let scrollableParent = false
        for (let p = el.parentElement; p; p = p.parentElement) {
          const pcs = getComputedStyle(p)
          if (/auto|scroll/.test(pcs.overflowX)) { scrollableParent = true; break }
          if (/hidden|clip/.test(pcs.overflowX)) {
            // 被硬裁：畫面上看不到超出的部分，不會逼出捲軸，但內容確實不見了。
            // 這一類交給 n77 判，這裡不重複報。
            scrollableParent = true; break
          }
        }
        if (scrollableParent) continue
        // 只報有文字或可互動的元素——純裝飾的漸層塊超出邊界不影響使用。
        const t = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 24)
        const interactive = /^(BUTTON|A|INPUT|SELECT|TEXTAREA)$/.test(el.tagName)
        if (!t && !interactive) continue
        if (el.closest('[aria-hidden="true"]')) continue
        out.push(`${el.className || el.tagName}`.slice(0, 26)
          + `[${Math.round(b.left)},${Math.round(b.top)} ${Math.round(b.width)}x${Math.round(b.height)}]`
          + ` 右緣 ${Math.round(b.right)} 超出 ${Math.round(b.right - innerWidth)}px「${t}」`)
      }
      return [...new Set(out)].slice(0, 6)
    })
    const tag = `${w}x${h} ${mod}`
    console.log(`${tag} spill=${hits.length}`)
    for (const x of hits) problems.push(`${tag}: ${x}`)
  }
  await page.close()
}
await browser.close()
if (problems.length) { console.log('\nN79 問題：'); for (const p of problems) console.log('  ✗ ' + p); process.exit(1) }
console.log('\nN79 OK')
