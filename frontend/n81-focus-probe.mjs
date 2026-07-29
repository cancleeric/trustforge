// N81：掃「鍵盤使用者看不到自己在哪」——兩種缺陷：
//   (a) Tab 過去焦點落在視窗外（畫面上什麼都沒發生）
//   (b) 那一站沒有任何視覺回饋（沒有 focus 指示器）
// 與 N77（被自己的框裁掉）、N79（橫向溢出）不同：那兩個看的是靜態版面，
// 這個看的是「按 Tab 的當下」。
//
// ⚠️ 這支第一版是用 `el.focus()` 逐個聚焦後比對 computed style，跑出 566 筆
// 「無焦點指示器」——全部是假的。Chromium 的 `:focus-visible` 是靠「上一個輸入
// 是不是鍵盤」的啟發式判定，程式呼叫 focus() 不算鍵盤，所以規則永遠不套用，
// 每個元素看起來都沒有指示器。實際按 Tab 驗證：每一站都有 2px outline、
// `matches(':focus-visible')` 為 true，全域規則本來就在 index.css:115。
// 所以這支改成真的按 Tab，讓瀏覽器自己決定要不要套 :focus-visible。
//
// 自我驗證：TF_INJECT=1 會在 Tab 動線最前面塞一顆把 outline 蓋掉的按鈕，
// 探針必須抓到它；抓不到代表這支沒有鑑別力，綠燈不算數。
import { chromium } from 'playwright'

const VIEWPORTS = [[375, 667], [768, 1024], [1280, 800], [1440, 900]]
const INJECT = process.env.TF_INJECT === '1'
const MAX_STOPS = 120
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

    if (INJECT) {
      await page.evaluate(() => {
        const b = document.createElement('button')
        b.textContent = 'TF_INJECT'
        b.className = 'tf-inject-probe'
        b.style.cssText = 'position:fixed;left:10px;top:200px;z-index:99999;outline:none!important;box-shadow:none!important'
        document.body.prepend(b)
      })
    }

    // 從頁面最前面開始按 Tab，跟使用者剛進站時一樣。
    await page.evaluate(() => { document.activeElement?.blur?.(); document.body.focus?.() })
    const seen = new Set()
    let offscreen = 0
    let noIndicator = 0
    for (let i = 0; i < MAX_STOPS; i++) {
      await page.keyboard.press('Tab')
      const r = await page.evaluate(() => {
        const el = document.activeElement
        if (!el || el === document.body || el === document.documentElement) return null
        const cs = getComputedStyle(el)
        const rect = el.getBoundingClientRect()
        const key = `${el.tagName}.${el.className.toString().slice(0, 22)}「${(el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 20)}」`
        return {
          key,
          // 焦點停在視窗外：瀏覽器連捲都捲不進來，畫面上完全沒有反應。
          offscreen: rect.right <= 0 || rect.bottom <= 0 || rect.left >= innerWidth || rect.top >= innerHeight,
          // 有沒有視覺回饋：交給瀏覽器判定 :focus-visible，再確認它真的畫了東西
          // （只有 :focus-visible 相符、但 outline 被 none 蓋掉一樣是看不見）。
          indicated: (el.matches(':focus-visible') && cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0)
            || cs.boxShadow !== 'none',
        }
      })
      if (!r) break                 // 焦點跑回瀏覽器 UI＝這一圈走完了
      if (seen.has(r.key)) continue // 同一種控制項只記一次
      seen.add(r.key)
      if (r.offscreen) { offscreen++; problems.push(`${tag}: 焦點在視窗外 ${r.key}`); console.log('  ✗ 焦點在視窗外 ' + r.key) }
      else if (!r.indicated) { noIndicator++; problems.push(`${tag}: 無焦點指示器 ${r.key}`); console.log('  ✗ 無焦點指示器 ' + r.key) }
    }
    console.log(`${tag} stops=${seen.size} offscreen=${offscreen} noIndicator=${noIndicator}`)
  }
  await page.close()
}
await browser.close()

if (problems.length) {
  console.log('\nN81 發現 ' + problems.length + ' 個問題')
  process.exit(1)
}
console.log('\nN81 OK')
