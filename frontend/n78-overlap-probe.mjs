// N78：掃「兩塊文字互相疊在一起」——非祖孫關係的兩個文字節點矩形重疊，
// 兩邊都不透明、都有可見文字。與 N75（按鈕被蓋住 = 可點性）不同，這是可讀性。
import { chromium } from 'playwright'
const VIEWPORTS = [[375,667],[430,932],[561,700],[900,760],[1024,900],[1280,800],[1440,900]]
const browser = await chromium.launch()
const page = await browser.newPage()
const problems = []
for (const [w, h] of VIEWPORTS) {
  await page.setViewportSize({ width: w, height: h })
  await page.goto('http://localhost:4175/?qa=1&reducedMotion=1', { waitUntil: 'domcontentloaded' })
  await page.context().addCookies([{ name: 'trustforge_hermes_locale', value: 'zh-TW', url: 'http://localhost:4175' }])
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2300)
  const navs = await page.locator('.hermes-nav-item').all()
  for (const idx of [null, ...navs.keys()]) {
    if (idx !== null) {
      if (!(await navs[idx].isVisible().catch(() => false))) continue
      await navs[idx].click().catch(() => {})
      await page.waitForTimeout(700)
    }
    const mod = idx === null ? '首頁' : ((await navs[idx].textContent().catch(() => '')) || '').trim().slice(0, 8)
    // 自我驗證用：TF_INJECT=1 注入一組必然重疊的文字，證明這支探針還抓得到東西。
    // 加了「被祖先裁切就跳過」的過濾之後，綠燈可能是真的沒問題，也可能是過濾
    // 過頭把什麼都濾掉了——沒跑過這個注入測試的綠燈不算數。
    if (process.env.TF_INJECT === '1') {
      await page.evaluate(() => {
        if (document.getElementById('tf-inject')) return
        const wrap = document.createElement('div')
        wrap.id = 'tf-inject'
        wrap.style.cssText = 'position:fixed;left:40px;top:40px;z-index:99999'
        wrap.innerHTML = '<div style="position:absolute;left:0;top:0;font:16px sans-serif;color:#fff;background:none">合成缺陷文字甲</div>'
          + '<div style="position:absolute;left:6px;top:4px;font:16px sans-serif;color:#fff;background:none">合成缺陷文字乙</div>'
        document.body.appendChild(wrap)
      })
      await page.waitForTimeout(120)
    }
    const hits = await page.evaluate(() => {
      // 元素若整塊落在某個「會捲動的祖先」可視範圍之外，畫面上是被裁掉的，
      // 使用者根本看不到——但 getBoundingClientRect 照樣回報座標。
      // 第一版沒有這個過濾，把左軌選單捲出視野的區塊當成「文字疊文字」報了 6 筆
      // （實測 1280x800：選單 pane 底部 785，被報的元素落在 y=798）。
      // 這跟 n77-clip-probe 用的是同一條判準，兩支要一起維護。
      // 進一步：不只「整塊在外面就跳過」，而是把矩形夾到每個會裁切的祖先
      // 可視範圍內，只留「畫面上真的看得到的那一塊」再拿去比對。
      // 只做整塊判斷會漏掉「一半捲出去」的情況——實測 561x700：選單 pane 高
      // 314px，裡面的 nav 有 403px，「動態」鈕落在 y=360~390，只有上緣露在
      // pane 內，剩下的部分早就被捲軸裁掉了，卻被當成疊在對話區上報了一整批。
      const clipTo = (el, b) => {
        let r = { left: b.left, top: b.top, right: b.right, bottom: b.bottom }
        for (let p = el.parentElement; p; p = p.parentElement) {
          const cs = getComputedStyle(p)
          if (!/auto|scroll|hidden|clip/.test(cs.overflowY + cs.overflowX)) continue
          const pb = p.getBoundingClientRect()
          r = {
            left: Math.max(r.left, pb.left), top: Math.max(r.top, pb.top),
            right: Math.min(r.right, pb.right), bottom: Math.min(r.bottom, pb.bottom),
          }
          if (r.right - r.left <= 1 || r.bottom - r.top <= 1) return null
        }
        r.width = r.right - r.left
        r.height = r.bottom - r.top
        return r
      }
      // 只看「葉節點文字」：自己有直接文字、且不含其他有文字的子元素。
      const leaves = []
      for (const el of document.querySelectorAll('body *')) {
        const cs = getComputedStyle(el)
        if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) < 0.35) continue
        const own = Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim())
        if (!own) continue
        // 裝飾性圖層不算「文字」：`aria-hidden` + `pointer-events:none` 的全息
        // 投影本來就是設計成襯在內容底下的背景畫。它的容器 opacity 只有 .58，
        // 但 getComputedStyle 對子元素回報的是 1，所以要自己往上乘。
        // 沒有這條，`.module-holo-core`（「ANA」「COM」「$0.0064」）會被當成
        // 正文，跟它上面的說明文字互報重疊——那是刻意的疊層，不是缺陷。
        let deco = false, eff = 1
        for (let p = el; p && p !== document.body; p = p.parentElement) {
          if (p.getAttribute('aria-hidden') === 'true') { deco = true; break }
          eff *= Number(getComputedStyle(p).opacity)
        }
        if (deco || eff < 0.35) continue
        const raw = el.getBoundingClientRect()
        if (raw.width < 8 || raw.height < 8) continue
        if (raw.bottom < 0 || raw.top > innerHeight || raw.right < 0 || raw.left > innerWidth) continue
        const b = clipTo(el, raw)
        if (!b || b.width < 8 || b.height < 8) continue
        leaves.push({ el, b, t: el.textContent.trim().replace(/\s+/g, ' ').slice(0, 20) })
      }
      const out = []
      for (let i = 0; i < leaves.length; i++) for (let j = i + 1; j < leaves.length; j++) {
        const A = leaves[i], B = leaves[j]
        if (A.el.contains(B.el) || B.el.contains(A.el)) continue
        const ox = Math.min(A.b.right, B.b.right) - Math.max(A.b.left, B.b.left)
        const oy = Math.min(A.b.bottom, B.b.bottom) - Math.max(A.b.top, B.b.top)
        if (ox <= 2 || oy <= 2) continue
        const area = ox * oy
        const small = Math.min(A.b.width * A.b.height, B.b.width * B.b.height)
        if (area / small < 0.35) continue // 輕微擦邊不算
        // 疊在上面那個若有不透明底色，就是刻意的分層（面板蓋面板），不是文字疊文字。
        const top = document.elementFromPoint(Math.max(A.b.left, B.b.left) + ox / 2, Math.max(A.b.top, B.b.top) + oy / 2)
        // 打空 = 那個座標上根本沒有渲染任何東西（例如被捲軸裁掉的內容，rect 還在
        // 報但畫面上不存在）。第一版把這種情形當成「沒有不透明底」而照報，六筆
        // 假陽性全是這樣來的。沒東西就不是重疊。
        if (!top) continue
        // 只看「兩者共同祖先以下」的底色。往上走到 <body> 一定會撞到頁面底色
        // （實測 rgb(246,248,250)，不透明），那是兩邊共用的，什麼也不代表——
        // 第一版就是這樣把所有「真的看得見的重疊」全部排除掉，導致這支探針
        // 只報得出假的、報不出真的。
        let common = A.el
        while (common && !common.contains(B.el)) common = common.parentElement
        let opaque = false
        for (let p = top; p && p !== common; p = p.parentElement) {
          const bg = getComputedStyle(p).backgroundColor
          const m = bg.match(/rgba?\(([^)]+)\)/)
          if (m && (m[1].split(',')[3] === undefined || parseFloat(m[1].split(',')[3]) > 0.85)) { opaque = true; break }
        }
        if (opaque) continue
        // 帶上兩邊的座標與 class：同一段文字在頁面上常常出現不只一處
        // （例如「HERMES」同時在頂欄與左軌標頭），沒有座標就沒辦法回頭定位，
        // 之前用文字去找元素找錯過對象。
        const box = (x) => `${x.el.className || x.el.tagName}`.slice(0, 22)
          + `[${Math.round(x.b.left)},${Math.round(x.b.top)} ${Math.round(x.b.width)}x${Math.round(x.b.height)}]`
        out.push(`「${A.t}」${box(A)} × 「${B.t}」${box(B)} 重疊 ${Math.round(ox)}x${Math.round(oy)}px`)
      }
      return [...new Set(out)].slice(0, 5)
    })
    const tag = `${w}x${h} ${mod}`
    console.log(`${tag} overlaps=${hits.length}`)
    for (const x of hits) problems.push(`${tag}: ${x}`)
  }
}
await browser.close()
if (problems.length) { console.log('\nN78 問題：'); for (const p of problems) console.log('  ✗ ' + p); process.exit(1) }
console.log('\nN78 OK')
