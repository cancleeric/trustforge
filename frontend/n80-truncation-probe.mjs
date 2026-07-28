// N80：掃「文字被截斷到看不懂」——元素的內容寬度超過可視寬度，被 ellipsis 或
// overflow:hidden 吃掉，且沒有 title / aria-label 可以補救。
// 一般使用者按不下去的最大原因之一：按鈕上寫的字只剩一半。
// 與 n79（元素超出視窗）不同：這裡元素本身在畫面內，是它「裡面的字」放不下。
import { chromium } from 'playwright'
const VIEWPORTS = [[320,568],[375,667],[430,932],[561,700],[768,1024],[1024,900],[1280,800],[1440,900]]
const browser = await chromium.launch()
const problems = []
for (const [w, h] of VIEWPORTS) {
  // 每個尺寸開一支新的 page。共用同一支跑完八個尺寸時，第三次切換之後 goto 會
  // 固定 60s 逾時兩次然後整支崩掉——同時間 curl 打 dev server 只要 11ms，
  // 所以卡住的是這支 page 不是伺服器。開新的最省事，也順便清掉殘留狀態。
  const page = await browser.newPage()
  await page.setViewportSize({ width: w, height: h })
  // dev server 連續被 reload 幾十次之後偶發 30s 逾時（實測固定卡在第二個尺寸
  // 切換）。這是量測環境的問題不是產品問題，逾時拉長並重試一次，別讓探針自己
  // 崩掉把已經抓到的結果一起丟了。
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
    if (process.env.TF_INJECT === '1') {
      await page.evaluate(() => {
        if (document.getElementById('tf-inject')) return
        const d = document.createElement('button')
        d.id = 'tf-inject'
        d.textContent = '這是一個一定會被截斷的很長的按鈕文字'
        d.style.cssText = 'position:absolute;left:10px;top:200px;width:60px;height:28px;'
          + 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;z-index:9999'
        document.body.appendChild(d)
      })
      await page.waitForTimeout(120)
    }
    const hits = await page.evaluate(() => {
      const out = []
      for (const el of document.querySelectorAll('body *')) {
        const cs = getComputedStyle(el)
        if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) < 0.35) continue
        // 只看自己有直接文字的葉節點：容器的 scrollWidth 大於 clientWidth 通常是
        // 內部橫捲設計，不是「字被吃掉」。
        const own = Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim())
        if (!own) continue
        if (el.closest('[aria-hidden="true"]')) continue
        // 只有真的會裁切才算：overflow 是 visible 的話字會露出來，屬於 n78 的疊字面向。
        if (!/hidden|clip|ellipsis/.test(cs.overflowX + cs.textOverflow)) continue
        const b = el.getBoundingClientRect()
        if (b.width < 8 || b.height < 8) continue
        if (b.bottom < 0 || b.top > innerHeight) continue
        const cut = el.scrollWidth - el.clientWidth
        if (cut <= 2) continue
        // 有 title / aria-label 可以看到全文，算有補救；純數字或單位也不算資訊損失。
        const full = (el.textContent || '').trim().replace(/\s+/g, ' ')
        const alt = el.getAttribute('title') || el.getAttribute('aria-label')
          || el.closest('[title]')?.getAttribute('title')
        if (alt && alt.length >= full.length) continue
        // 被吃掉不到 15% 且剩餘超過 6 個字，通常還讀得懂，先不報以免淹沒真缺陷。
        const ratio = cut / el.scrollWidth
        if (ratio < 0.15 && full.length > 6) continue
        out.push(`${el.className || el.tagName}`.slice(0, 26)
          + `[${Math.round(b.left)},${Math.round(b.top)} ${Math.round(b.width)}x${Math.round(b.height)}]`
          + ` 少 ${cut}px(${Math.round(ratio * 100)}%) 無 title「${full.slice(0, 28)}」`)
      }
      return [...new Set(out)].slice(0, 6)
    })
    const tag = `${w}x${h} ${mod}`
    console.log(`${tag} cut=${hits.length}`)
    // 邊跑邊印：跑到一半掛掉時，已經抓到的證據不會跟著消失。
    for (const x of hits) { problems.push(`${tag}: ${x}`); console.log('  ✗ ' + tag + ': ' + x) }
  }
  await page.close()
}
await browser.close()
if (problems.length) { console.log('\nN80 問題：'); for (const p of problems) console.log('  ✗ ' + p); process.exit(1) }
console.log('\nN80 OK')
