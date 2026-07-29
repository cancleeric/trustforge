// N81：掃「鍵盤使用者看不到自己在哪」——兩種缺陷：
//   (a) 焦點落在視窗外或被收起來的控制項上（Tab 過去畫面沒反應）
//   (b) 元素取得焦點時外觀完全沒變（沒有 focus 指示器，:focus-visible 沒樣式）
// 與 N77（被自己的框裁掉）、N79（橫向溢出）不同：那兩個看的是靜態版面，
// 這個看的是「互動當下」的狀態。
//
// 自我驗證：TF_INJECT=1 會塞一顆沒有任何 focus 樣式、且推到視窗外的按鈕進去，
// 探針必須把它抓出來；抓不到代表這支探針沒有鑑別力，綠燈不算數。
import { chromium } from 'playwright'

const VIEWPORTS = [[375, 667], [768, 1024], [1280, 800], [1440, 900]]
const INJECT = process.env.TF_INJECT === '1'
const browser = await chromium.launch()
const problems = []

for (const [w, h] of VIEWPORTS) {
  // 每個尺寸開一支新的 page（見 N77/N80 同註解：共用會固定逾時崩掉）。
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
  const states = [null, ...navs.keys()]
  for (const idx of states) {
    if (idx !== null) {
      const n = navs[idx]
      if (!(await n.isVisible().catch(() => false))) continue
      await n.click().catch(() => {})
      await page.waitForTimeout(700)
    }
    const modName = idx === null ? '首頁' : ((await navs[idx].textContent().catch(() => '')) || '').trim().slice(0, 8)
    const tag = `${w}x${h} ${modName}`

    const r = await page.evaluate((inject) => {
      if (inject) {
        const b = document.createElement('button')
        b.textContent = 'TF_INJECT'
        b.style.cssText = 'position:fixed;left:-400px;top:10px;outline:none!important;box-shadow:none!important'
        b.className = 'tf-inject-probe'
        document.body.appendChild(b)
        // 第二顆在視野內、但完全沒有 focus 樣式——用來驗證 (b) 那一半也有鑑別力
        // （只有一顆的話它會先被 (a) 抓走，(b) 等於沒被測過）。
        const c = document.createElement('button')
        c.textContent = 'TF_INJECT_NOIND'
        c.style.cssText = 'position:fixed;left:10px;top:200px;z-index:99999;outline:none!important;box-shadow:none!important'
        c.className = 'tf-inject-probe2'
        document.body.appendChild(c)
      }
      const SEL = 'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
      const offscreen = []
      const noIndicator = []
      const snap = (el) => {
        const cs = getComputedStyle(el)
        return [cs.outlineWidth, cs.outlineColor, cs.outlineStyle, cs.boxShadow, cs.backgroundColor, cs.borderColor, cs.color].join('|')
      }
      for (const el of document.querySelectorAll(SEL)) {
        if (el.disabled) continue
        const cs = getComputedStyle(el)
        // display:none / visibility:hidden 本來就不可聚焦，不是缺陷。
        if (cs.display === 'none' || cs.visibility === 'hidden') continue
        const before = snap(el)
        // 用真正的 focus（不加 preventScroll）：瀏覽器會把控制項捲進視野，
        // 這才是使用者按 Tab 的實際行為。加了 preventScroll 會把所有放在
        // 可橫向捲動容器裡的控制項（例如左軌那排）全部誤判成「在視窗外」。
        el.focus()
        if (document.activeElement !== el) continue  // 聚焦不上就不是鍵盤動線的一環
        const b = el.getBoundingClientRect()
        const label = (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 24)
        // (a) 焦點在視窗外：Tab 到這裡畫面上什麼都不會發生。
        if (b.right <= 0 || b.bottom <= 0 || b.left >= innerWidth || b.top >= innerHeight) {
          offscreen.push(`${el.tagName}.${el.className.toString().slice(0, 20)}「${label}」@${Math.round(b.left)},${Math.round(b.top)}`)
        } else if (b.width < 1 || b.height < 1) {
          continue  // 零尺寸的隱藏輸入框（例如檔案上傳）不列
        } else if (snap(el) === before) {
          // (b) 聚焦前後電腦算出來的樣式完全一樣＝沒有任何視覺回饋。
          noIndicator.push(`${el.tagName}.${el.className.toString().slice(0, 20)}「${label}」`)
        }
        el.blur()
      }
      return { offscreen, noIndicator }
    }, INJECT)

    console.log(`${tag} offscreen=${r.offscreen.length} noIndicator=${r.noIndicator.length}`)
    // 邊跑邊印：跑到一半掛掉時，已經抓到的證據不會跟著消失。
    for (const x of r.offscreen) { problems.push(`${tag}: 焦點在視窗外 ${x}`); console.log('  ✗ 焦點在視窗外 ' + x) }
    for (const x of r.noIndicator) { problems.push(`${tag}: 無焦點指示器 ${x}`); console.log('  ✗ 無焦點指示器 ' + x) }
  }
  await page.close()
}
await browser.close()

if (problems.length) {
  console.log('\nN81 發現 ' + problems.length + ' 個問題')
  process.exit(1)
}
console.log('\nN81 OK')
