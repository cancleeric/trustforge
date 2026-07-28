// N76（CEO：「這很重要怎麼跑下去了」「左邊選單正常要縮吧 怎麼會卡列」）：
// 量左軌兩個 pane 的空間分配——選單該自己縮，對話區要保底，兩者都不得超出視窗。
import { chromium } from 'playwright'
// 三個區間都要：≤560 橫向控制條 / 561~1279 垂直堆疊 / ≥1280 橫排，外加矮視窗。
const VIEWPORTS = [[561,700],[680,620],[900,760],[928,900],[960,800],[1024,900],[1100,950],[1279,900],
                   [1280,700],[1280,900],[1440,620],[1440,900],[1920,1080],[1024,600],[900,560]]
const browser = await chromium.launch()
const page = await browser.newPage()
const problems = []
for (const [w, h] of VIEWPORTS) {
  await page.setViewportSize({ width: w, height: h })
  await page.goto('http://localhost:4175/?qa=1&reducedMotion=1', { waitUntil: 'domcontentloaded' })
  await page.context().addCookies([{ name: 'trustforge_hermes_locale', value: 'zh-TW', url: 'http://localhost:4175' }])
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2300)
  // 截圖是「分析工作區開著」的狀態，bug 要在這個狀態下量。
  const nav = page.locator('.hermes-nav-item', { hasText: '分析' }).first()
  if (await nav.count()) { await nav.click(); await page.waitForTimeout(900) }
  const r = await page.evaluate(() => {
    const q = (s) => document.querySelector(s)
    const b = (el) => el ? (({top,bottom,height}) => ({top:Math.round(top),bottom:Math.round(bottom),h:Math.round(height)}))(el.getBoundingClientRect()) : null
    const menu = q('.hermes-rail-menu'), chat = q('.hermes-rail-chat'), rail = q("[data-region='left-rail']")
    const spill = []
    for (const el of document.querySelectorAll("[data-region='left-rail'] *")) {
      const r = el.getBoundingClientRect()
      if (r.height && r.bottom > window.innerHeight + 1) {
        // 被祖先 overflow 裁掉的不算——那是視覺上看不到的。
        let clipped = false
        for (let p = el.parentElement; p; p = p.parentElement) {
          const ov = getComputedStyle(p).overflowY
          if ((ov === 'auto' || ov === 'hidden' || ov === 'scroll') && p.getBoundingClientRect().bottom <= window.innerHeight + 1) { clipped = true; break }
        }
        if (!clipped) spill.push(`${el.className || el.tagName}`.slice(0, 46) + ` +${Math.round(r.bottom - window.innerHeight)}px`)
      }
    }
    // N76 真正要驗的是「輸入框看得見」，不是「pane 有幾 px」。
    const composer = chat ? chat.querySelector('textarea, input[type=text], [contenteditable=true]') : null
    const cb = composer ? composer.getBoundingClientRect() : null
    const chatBox = chat ? chat.getBoundingClientRect() : null
    const composerVisible = !!(cb && chatBox && cb.height > 0 &&
      cb.bottom <= chatBox.bottom + 1 && cb.top >= chatBox.top - 1 && cb.bottom <= window.innerHeight + 1)
    return { composerVisible, hasComposer: !!composer, menu: b(menu), chat: b(chat), rail: b(rail),
      menuScroll: menu ? [menu.scrollHeight, menu.clientHeight] : null,
      chatScroll: chat ? [chat.scrollHeight, chat.clientHeight] : null,
      spill: [...new Set(spill)].slice(0, 6), innerH: window.innerHeight }
  })
  const tag = `${w}x${h}`
  console.log(`${tag} menu=${r.menu?.h}(${r.menuScroll}) chat=${r.chat?.h}(${r.chatScroll}) composerVisible=${r.composerVisible} spill=${JSON.stringify(r.spill)}`)
  if (r.spill.length) problems.push(`${tag}: 左軌內容真的畫到視窗外 → ${r.spill.join(' | ')}`)
  if (!r.hasComposer) problems.push(`${tag}: 對話區找不到輸入框（選擇器過時？）`)
  else if (!r.composerVisible) problems.push(`${tag}: 輸入框被擠出可視範圍（對話區 ${r.chat?.h}px，內容 ${r.chatScroll}，選單吃了 ${r.menu?.h}px）`)
}
await browser.close()
if (problems.length) { console.log('\nN76 問題：'); for (const p of problems) console.log('  ✗ ' + p); process.exit(1) }
console.log('\nN76 OK')
