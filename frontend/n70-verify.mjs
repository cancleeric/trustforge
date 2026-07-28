// N70 現場驗收：控制項位置 + 遙測展開 + 角度不給選。跨解析度與雙語系。
import { chromium } from 'playwright'

const VIEWPORTS = [
  [320, 568], [375, 667], [390, 844], [430, 932], [540, 720],
  [561, 700], [768, 1024], [900, 620], [1024, 768], [1280, 800], [1440, 900], [1920, 1080],
]
const browser = await chromium.launch()
const problems = []
for (const locale of ['zh-TW', 'en']) {
  for (const [w, h] of VIEWPORTS) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h } })
    const page = await ctx.newPage()
    await page.goto('http://localhost:4175/?qa=1&reducedMotion=1', { waitUntil: 'domcontentloaded' })
    await page.evaluate((l) => { document.cookie = 'trustforge_hermes_locale=' + l + '; Path=/' }, locale)
    await page.reload({ waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(2300)

    const tag = `${locale} ${w}x${h}`
    const r = await page.evaluate(() => {
      const vis = (el) => {
        if (!el) return false
        const r = el.getBoundingClientRect()
        const s = getComputedStyle(el)
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'
      }
      const controls = document.querySelector('.hermes-rail-controls')
      // 只看可按的：分隔線 (.hermes-rail-controls-sep) 不是控制項。
      const items = [...document.querySelectorAll('.hermes-rail-controls > button')]
      const topbar = document.querySelector('.hermes-topbar')
      const chip = document.querySelector('.hermes-telemetry-chip')
      const cr = controls?.getBoundingClientRect()
      return {
        controlsVisible: vis(controls),
        visibleItems: items.filter(vis).length,
        totalItems: items.length,
        // 橫向控制條可捲，所以看的是「捲動容器裡放得下」而不是視窗
        overflowRight: controls ? Math.max(0, controls.scrollWidth - controls.clientWidth) : 0,
        controlsRight: cr ? Math.round(cr.right) : null,
        topbarButtons: topbar ? topbar.querySelectorAll('button').length : -1,
        chipVisible: vis(chip),
        focusSelect: !!document.querySelector('#hermes-focus'),
        derivedNote: document.querySelector('.hermes-focus-derived')?.textContent?.trim() ?? null,
        smallTargets: items.filter((el) => vis(el) && el.getBoundingClientRect().height < 24)
          .map((el) => `${el.textContent?.trim().slice(0, 12)}:${Math.round(el.getBoundingClientRect().height)}`),
        docOverflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      }
    })

    if (!r.controlsVisible) problems.push(`${tag}: 控制條不可見`)
    if (r.visibleItems < r.totalItems) problems.push(`${tag}: 控制項只看到 ${r.visibleItems}/${r.totalItems}`)
    if (r.topbarButtons !== 1) problems.push(`${tag}: 頂欄按鈕數 ${r.topbarButtons}（應為 1＝遙測膠囊）`)
    if (!r.chipVisible) problems.push(`${tag}: 遙測膠囊不可見`)
    if (r.focusSelect) problems.push(`${tag}: 角度下拉還在`)
    if (!r.derivedNote) problems.push(`${tag}: 缺少角度說明`)
    if (r.smallTargets.length) problems.push(`${tag}: 點擊目標過小 ${r.smallTargets.join(', ')}`)
    if (r.docOverflowX > 0) problems.push(`${tag}: 頁面橫向溢出 ${r.docOverflowX}px`)

    // 遙測面板點擊展開
    if (r.chipVisible) {
      await page.click('.hermes-telemetry-chip')
      await page.waitForTimeout(200)
      const panel = await page.evaluate(() => {
        const p = document.querySelector('#hermes-telemetry-panel')
        if (!p) return null
        const b = p.getBoundingClientRect()
        return { right: Math.round(b.right), bottom: Math.round(b.bottom), w: Math.round(b.width) }
      })
      if (!panel) problems.push(`${tag}: 點擊後遙測面板沒展開`)
      else if (panel.right > w) problems.push(`${tag}: 遙測面板超出畫面右緣 ${panel.right}>${w}`)
    }
    console.log(`${tag} items=${r.visibleItems}/${r.totalItems} topbarBtns=${r.topbarButtons} scrollOverflow=${r.overflowRight} note=${(r.derivedNote ?? '').slice(0, 24)}`)
    await ctx.close()
  }
}
await browser.close()
console.log(problems.length ? '\nPROBLEMS:\n' + problems.join('\n') : '\nN70 OK: all viewports x 2 locales')
