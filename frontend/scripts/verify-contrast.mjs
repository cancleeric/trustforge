/**
 * 對比檢查（WCAG 1.4.3）——跨 主題 × 語系 × 斷點 × 路由 全掃。
 *
 * 為什麼要自己寫而不是只靠人眼：畫面解析度每台電腦不同，同一個顏色組合在
 * 1280 看得到、在 430 因為換了斷點就換了底色。這支把矩陣跑完再回報。
 *
 * 兩個必須做對、先前踩過的地方：
 *
 * 1) alpha 合成。HERMES 大量使用 `rgba(232,179,77,.13)` 這種半透明底。若直接
 *    拿 rgba 當底色算，會得出「琥珀字壓琥珀底 = 1.0:1」的假紅——實際上它疊在
 *    深色卡片上之後對比是足夠的。這裡沿祖先鏈把每層半透明底依序合成回不透明
 *    色再算。
 *
 * 2) gradient 底。`background: linear-gradient(...)` 不會出現在
 *    `backgroundColor`（那裡是 transparent），天真的實作會一路往上找到卡片底，
 *    於是把「深色字壓在青色漸層鈕上」誤判成 1.08:1 的假紅。凡是自身或祖先帶
 *    `background-image` 的一律跳過並計入 skipped，寧可漏報也不要製造假紅——
 *    假紅會逼人去「修」根本沒壞的東西。
 *
 * 用法：node scripts/verify-contrast.mjs [baseUrl]
 * 需要先起好 server（vite preview / dev 皆可）。
 */
import { chromium } from 'playwright'

const BASE = process.argv[2] || 'http://localhost:4188'
const ROUTES = ['/', '/analyze', '/compare', '/history', '/status', '/costs', '/help', '/asset-context', '/eco-link', '/peer-metrics']
const VIEWPORTS = [[1280, 800], [1024, 768], [768, 1024], [430, 932], [375, 667]]
const THEMES = ['light', 'dark']
const LOCALES = ['zh-TW', 'en']

const audit = () => {
  const parse = (c) => {
    const n = (c.match(/[\d.]+/g) || []).map(Number)
    return { r: n[0] || 0, g: n[1] || 0, b: n[2] || 0, a: n.length > 3 ? n[3] : 1 }
  }
  const over = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  })
  const lum = ({ r, g, b }) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4) }
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
  }

  const fails = []
  let skipped = 0
  for (const el of document.querySelectorAll('*')) {
    if (el.children.length || !el.textContent.trim()) continue
    const cs = getComputedStyle(el)
    const rect = el.getBoundingClientRect()
    if (!rect.width || !rect.height) continue
    if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) === 0) continue

    // 沿祖先鏈收集半透明底，遇到 gradient 就放棄這個元素（見檔頭說明 2）。
    const layers = []
    let node = el
    let hasGradient = false
    while (node && node !== document.documentElement) {
      const s = getComputedStyle(node)
      if (s.backgroundImage && s.backgroundImage !== 'none') { hasGradient = true; break }
      const bg = parse(s.backgroundColor)
      if (bg.a > 0) { layers.push(bg); if (bg.a === 1) break }
      node = node.parentElement
    }
    if (hasGradient) { skipped++; continue }
    if (!layers.length || layers[layers.length - 1].a !== 1) layers.push({ r: 255, g: 255, b: 255, a: 1 })

    // 由最底層往上合成（見檔頭說明 1）。
    let bg = layers[layers.length - 1]
    for (let i = layers.length - 2; i >= 0; i--) bg = over(layers[i], bg)

    const fg = over(parse(cs.color), bg)
    const [l1, l2] = [lum(fg), lum(bg)]
    const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)
    const px = parseFloat(cs.fontSize)
    const need = px >= 24 || (px >= 18.66 && Number(cs.fontWeight) >= 700) ? 3 : 4.5
    if (ratio < need) {
      fails.push({ text: el.textContent.trim().slice(0, 30), ratio: +ratio.toFixed(2), need, px, color: cs.color })
    }
  }
  return { fails, skipped }
}

const browser = await chromium.launch()
let total = 0
let totalSkipped = 0
try {
  for (const theme of THEMES) {
    for (const locale of LOCALES) {
      for (const [width, height] of VIEWPORTS) {
        const ctx = await browser.newContext({ viewport: { width, height } })
        await ctx.addInitScript(([t]) => localStorage.setItem('tf-theme', t), [theme])
        const page = await ctx.newPage()
        for (const route of ROUTES) {
          const url = `${BASE}${route}?qa=1&reducedMotion=1`
          await page.goto(url, { waitUntil: 'domcontentloaded' })
          // 語系走 cookie，不是 localStorage；設完要 reload 才會套用。
          await page.evaluate((l) => { document.cookie = `trustforge_hermes_locale=${l}; Path=/` }, locale)
          await page.reload({ waitUntil: 'domcontentloaded' })
          await page.waitForTimeout(2200)
          const { fails, skipped } = await page.evaluate(audit)
          totalSkipped += skipped
          if (fails.length) {
            total += fails.length
            console.error(`FAIL [${theme}/${locale}/${width}x${height}] ${route}`)
            for (const f of fails) console.error(`   ${f.ratio}:1 (need ${f.need}) ${f.px}px ${f.color} — ${JSON.stringify(f.text)}`)
          }
        }
        await ctx.close()
      }
    }
  }
} finally {
  await browser.close()
}

if (total) {
  console.error(`\nContrast FAILED: ${total} violation(s).`)
  process.exit(1)
}
console.log(`Contrast OK: ${ROUTES.length} routes x ${THEMES.length} themes x ${LOCALES.length} locales x ${VIEWPORTS.length} viewports (${totalSkipped} gradient-backed nodes skipped)`)
