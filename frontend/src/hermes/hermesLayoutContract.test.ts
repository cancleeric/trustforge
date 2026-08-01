// @vitest-environment jsdom
// N6 round 2 needs `document`/`getComputedStyle` for a genuine computed-style
// assertion (see below) instead of the string/regex checks the rest of this
// file otherwise uses; jsdom is a superset so the existing string-based
// checks below are unaffected by switching this whole file to jsdom.
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

describe('Hermes responsive bridge layout contract', () => {
  it('keeps training status and divergence in the right rail flow without overlapping layers', () => {
    const dashboard = readFileSync(path.join(__dirname, '..', 'pages', 'HermesDashboard.tsx'), 'utf8')
    const rightRail = readFileSync(path.join(__dirname, 'HermesRightRail.tsx'), 'utf8')

    expect(dashboard).toContain('trainingStatus={<TrainingStatusCard />}')
    expect(dashboard).not.toContain('hermes-training-status-layer')
    expect(rightRail).toContain('zIndex: 5')
    expect(rightRail).toContain('className="hermes-training-status-slot"')
    expect(rightRail).toContain('className="hermes-clip hermes-divergence-dock"')
  })

  it('uses the viewport without a fixed canvas or page scrolling', () => {
    const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')
    const dashboard = readFileSync(path.join(__dirname, '..', 'pages', 'HermesDashboard.tsx'), 'utf8')
    const drilldown = readFileSync(path.join(__dirname, 'StageDrilldown.tsx'), 'utf8')

    expect(dashboard).toContain("height: '100dvh'")
    expect(dashboard).toContain("width: '100%', height: '100%'")
    expect(dashboard).not.toContain('width: 1440, height: 900')
    expect(dashboard).not.toContain('transform: `scale(${scale})`')
    expect(dashboard).not.toContain('window.innerWidth / 1440')
    expect(dashboard).not.toContain("clipPath: boot.left")
    expect(dashboard).not.toContain("clipPath: boot.right")
    expect(css).toContain('.hermes-boot-layer')
    expect(css).toContain('@media (max-width:900px)')
    expect(css).not.toContain('transform: none !important')
    expect(css).not.toContain("position: relative !important")
    expect(dashboard).not.toContain("left: 50, top: 50")
    expect(dashboard).toContain('degradedMessage={globalError}')
    expect(css).toContain('--hermes-rail: clamp(')
    expect(css).toContain('right: calc(var(--hermes-right-rail) + 18px)')
    expect(css).toContain('z-index: 50')
    expect(css).toContain('width: min(490px, calc(100% - var(--hermes-rail) - var(--hermes-right-rail) - 36px))')
    expect(css).toContain('background: linear-gradient(180deg, rgba(5, 11, 19, .94), rgba(3, 8, 15, .9))')
    expect(drilldown).not.toContain('left: 640')
    expect(dashboard).toContain('window.setInterval(refresh, 30_000)')
    expect(dashboard).toContain('useState<GalaxyModel>(() => buildGalaxyModel(null))')
    expect(dashboard).toContain('系統啟動與模組載入')
    expect(dashboard).toContain('startupStep / 5')
    expect(dashboard).not.toContain('CHANNELS VERIFIED')
    expect(dashboard).toContain('serviceMonitor={serviceMonitor}')
    expect(dashboard).toContain('<HermesUpgradeShip')
    expect(dashboard).toContain('getHermesUpgrades')
    expect(dashboard).toContain('data-region="galaxy"')
    expect(dashboard).toContain("activeModule ? ' hermes-galaxy-background' : ''")
    expect(dashboard).toContain('inert={activeModule ? true : undefined}')
    expect(dashboard).toContain('activeModule ? 10_000 : 1500')
    expect(dashboard).toContain("activeModule !== 'history'")
    expect(dashboard).toContain("const requestedModule = searchParams.get('workspace')")
    expect(dashboard).toContain('const activeModule: HermesWorkspaceModule | null =')
    expect(dashboard).toContain("qaMode ? ' is-qa-mode' : ''")
    expect(css).toContain('.hermes-dashboard.is-qa-mode')
    const upgradeShip = readFileSync(path.join(__dirname, 'HermesUpgradeShip.tsx'), 'utf8')
    const i18nDict = readFileSync(path.join(__dirname, 'hermesI18n.tsx'), 'utf8')
    // N34-1: 這兩段文案已改走 i18n。合約仍然成立——元件必須引用該 key，
    // 且字典的 zh-TW 值必須是原文案，只是文字不再字面寫在元件裡。
    expect(upgradeShip).toContain("t('shipNoRecursiveUpgrade')")
    expect(i18nDict).toContain("shipNoRecursiveUpgrade: '禁止遞回升級'")
    expect(upgradeShip).toContain("t('shipLlmReview')")
    expect(i18nDict).toContain("shipLlmReview: 'LLM 對抗審查'")
    expect(readFileSync(path.join(__dirname, 'HermesUpgradeShip.tsx'), 'utf8')).toContain('(data?.automation.historical_sources ?? []).map')
    // N72（CEO：「這把畫面擋住了，而且沒有疊層的感覺」「看要不要蓋掉右邊就好」）：
    // 升級控制台原本是 `left: 18px` 的近滿版面板，且沒有背幕，讀起來像換頁。
    // 新合約：左緣退到左軌之後（左軌整條留著看得見），並且一定要有背幕。
    expect(css).toContain('left: calc(var(--hermes-rail) + 14px)')
    expect(css).toContain('.hermes-upgrade-scrim')
    const dashboardSrc = readFileSync(path.join(__dirname, '..', 'pages', 'HermesDashboard.tsx'), 'utf8')
    expect(dashboardSrc).toContain('className="hermes-upgrade-scrim"')
  })

  it('does not restart the Hermes report for every score counter frame', () => {
    const dashboard = readFileSync(path.join(__dirname, '..', 'pages', 'HermesDashboard.tsx'), 'utf8')

    expect(dashboard).toContain('const displayScoreRef = useRef(0)')
    expect(dashboard).toContain('const start = displayScoreRef.current')
    expect(dashboard).not.toContain('}, [displayScore])')
  })

  // N66：這條原本叫「provides real light tokens…」，斷言 hermes.css 含有
  // `:root[data-theme='light'] .hermes-surface` 與 `… .app-header` 兩個字串。
  // 它是空轉的——.hermes-surface 那條規則早在 N64 就因為對比問題被刪掉了，
  // 測試之所以還綠，只是因為 N64 的「註解」把那個 selector 當說明文字引述了
  // 一次，toContain 讀到的是散文不是規則。這正是字串比對型斷言的陷阱。
  //
  // 改成驗真正的不變式，並先把註解剝掉再比對，免得再被說明文字餵成假綠。
  // 現行契約（N64 + N66）：HERMES 是暗色優先，.hermes-surface 之下的表面
  // 不接受淺色主題半套覆寫——字被鎖成暗色系 token，底若被改成淺色就會變成
  // 「暗字壓淺底」，實測低到 1.24:1。要做真正的淺色 HERMES 得整組 hermes-*
  // 色票（含輝光、星系）都有淺色版，不能靠覆寫幾個底色假裝有。
  //
  // 實際的對比把關在 scripts/verify-contrast.mjs（主題×語系×斷點×路由 全掃，
  // 會真的算合成後的對比值）；這裡只鎖住「不要再長回半套覆寫」。
  it('keeps Hermes surfaces dark-locked instead of half-overriding them for light theme', () => {
    const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')
    const app = readFileSync(path.join(__dirname, '..', 'App.tsx'), 'utf8')
    const rules = css.replace(/\/\*[\s\S]*?\*\//g, '')

    expect(app).toContain('<HermesI18nProvider>')
    for (const surface of ['.hermes-surface', '.app-header', '.bridge-workspace', '.bridge-hologram-bay', '.bridge-route-viewport', '.bridge-side-rail', '.bridge-engine-deck']) {
      expect(rules).not.toContain(`:root[data-theme='light'] ${surface}`)
    }
  })

  it('keeps every inner route inside the hologram bridge workspace', () => {
    const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')
    const app = readFileSync(path.join(__dirname, '..', 'App.tsx'), 'utf8')
    const shell = readFileSync(path.join(__dirname, '..', 'components', 'BridgeWorkspaceShell.tsx'), 'utf8')

    expect(app).toContain('<BridgeWorkspaceShell><RoutedContent /></BridgeWorkspaceShell>')
    expect(shell).toContain('bridge-hologram-bay')
    expect(shell).toContain('bridge-engine-deck')
    expect(shell).toContain('HERMES ENGINE')
    expect(css).toContain('.bridge-route-viewport>main')
    expect(css).toContain('.bridge-holo-display')
    expect(shell).toContain("pathname === '/compare'")
    expect(shell).toContain("pathname === '/history'")
    expect(shell).toContain("pathname === '/status'")
    expect(shell).toContain("pathname === '/costs'")
    expect(css).toContain('.hermes-module-hologram')
    expect(css).toContain('.module-holo-core')
    expect(css).toContain('.hermes-energy-conduit')
    expect(css).toContain('@keyframes hermes-energy-flow')
    expect(readFileSync(path.join(__dirname, 'HermesModuleDeck.tsx'), 'utf8')).not.toContain('AWAITING DATA')
  })

  it('N1: keeps the error banner retry button intact instead of a clipped 30px icon box', () => {
    const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')
    const retryRuleMatch = css.match(/\.tf-error-retry\s*\{[^}]*\}/)
    expect(retryRuleMatch).not.toBeNull()
    const retryRule = retryRuleMatch![0]

    // A fixed 30px box was what crushed the "↻ 重新嘗試" label into a
    // vertical, clipped stack (see StatusStates.tsx's ErrorState button).
    expect(retryRule).not.toMatch(/width:\s*30px/)
    expect(retryRule).not.toMatch(/height:\s*30px/)
    expect(retryRule).toContain('white-space: nowrap')
    expect(retryRule).toContain('flex: 0 0 auto')
  })

  it('N6 (round 1 — string check only, see round 2 below for computed-style proof): the mid-breakpoint block still declares single-column + !important, and hides the hero tagline', () => {
    const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')

    // At ~768–1024px the left rail narrows (--hermes-rail shrinks) while
    // .hermes-intent-picker>div was still forced into a 2-column grid,
    // crushing Chinese question titles into 2–3 character line wraps.
    //
    // CEO round-1 rejection: this rule and the always-on base rule
    // `.hermes-intent-picker>div { grid-template-columns: 1fr 1fr }`
    // (defined later in the file, outside any @media block) have identical
    // specificity (1 class + 1 element). CSS cascade breaks specificity ties
    // by *source order*, not by whether a rule sits inside a matching
    // @media block — so the later, unconditional base rule was silently
    // winning even when this media query matched, and DevTools computed
    // style showed 2 columns the whole time. `!important` now forces this
    // rule to win regardless of source order.
    //
    // NOT OBSERVED here: jsdom does not re-evaluate `@media (max-width:…)`
    // rules against a simulated `window.innerWidth` for `getComputedStyle`
    // (verified empirically — changing `window.innerWidth` via
    // `Object.defineProperty` had no effect on which of two conflicting
    // matched-vs-unmatched-media rules jsdom applied). So this assertion is
    // still only a string/regex check on the source, same limitation as
    // round 1. The computed-style proof below only covers the `.is-module-
    // open` fix, which is the fix CEO's browser measurements actually
    // pinned the regression on. A true viewport-driven check for this
    // narrower slice needs a real browser (see CEO's own DevTools numbers).
    const midBreakpointMatch = css.match(/@media \(max-width:1024px\) \{([\s\S]*?)\n\}\n\n\/\* N6 \(round 2/)
    expect(midBreakpointMatch).not.toBeNull()
    const midBreakpointBlock = midBreakpointMatch![1]

    expect(midBreakpointBlock).toMatch(/\.hermes-intent-picker>div\s*\{\s*grid-template-columns:\s*1fr\s*!important;\s*\}/)

    // The full-width hero tagline strip (left:0,right:0, z-index above the
    // left rail) only reserves a single-line height via --hermes-top and
    // wraps to 2 lines at this width, overlapping the left-rail cards below
    // it. It must be suppressed at this breakpoint to avoid the overlap.
    // (CEO confirmed this half fixed in round 1 — untouched here.)
    expect(midBreakpointBlock).toMatch(/\.hermes-hero-tagline\s*\{\s*display:\s*none\s*!important;\s*\}/)
  })

  it('N6 (round 2 — genuine computed-style proof, not a string assertion): hides the left-rail intent picker once the analysis module deck is open, regardless of viewport width', () => {
    // CEO round-1 rejection also identified the *real* root cause: the
    // squish reproduces at 1440px too, whenever the analysis module deck
    // (HermesModuleDeck, opened via `?workspace=analyze`) is open — its
    // `inset` is hard-coded (`44px 300px 120px`), independent of
    // `--hermes-rail`, so the left rail (and its intent-picker cards) keeps
    // rendering at native size in whatever sliver is left, regardless of
    // viewport width. CEO measured `.hermes-intent-picker` computed width
    // at 109px in that state. Per CEO's own accepted criterion ("該區塊乾脆
    // 不顯示" is an acceptable outcome), the fix hides the intent picker
    // entirely while the module deck is open.
    //
    // This test builds a minimal DOM mirroring the real markup
    // (`.hermes-dashboard.is-module-open` → `.hermes-frame` →
    // `[data-region='left-rail']` → `.hermes-clip` → `.hermes-intent-picker`),
    // injects the real hermes.css into a `<style>` tag, and reads
    // `getComputedStyle(...).display` — i.e. what actually wins the cascade,
    // not just "the rule text exists somewhere in the file". This is the
    // kind of check CEO asked for after round 1's string-only test gave a
    // false green.
    const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')
    const style = document.createElement('style')
    style.textContent = css
    document.head.appendChild(style)
    document.body.innerHTML = `
      <div class="hermes-dashboard">
        <div class="hermes-frame">
          <div data-region="left-rail">
            <div class="hermes-clip">
              <div class="hermes-intent-picker">
                <div class="hermes-intent-title"></div>
                <p></p>
                <div><button>a</button><button>b</button></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    `
    const dashboard = document.querySelector('.hermes-dashboard') as HTMLElement
    const picker = document.querySelector('.hermes-intent-picker') as HTMLElement

    // baseline: visible while the module deck is closed
    expect(getComputedStyle(picker).display).not.toBe('none')

    dashboard.classList.add('is-module-open')
    expect(getComputedStyle(picker).display).toBe('none')

    try {
      document.head.removeChild(style)
      document.body.innerHTML = ''
    } catch { /* best-effort cleanup */ }
  })

  it('N12 (genuine computed-style proof): stage labels wrap instead of ellipsis-truncating, and the icon column no longer eats as much width', () => {
    // CEO measured (real browser, actual computed style, at 960px viewport):
    //   Cross-Verify   scrollWidth 77px vs clientWidth 75px
    //   Manipulation   scrollWidth 77px vs clientWidth 75px
    //   Composite Score scrollWidth 96px vs clientWidth 75px
    // i.e. `text-overflow: ellipsis` was clipping "Cross-Veri…" etc. Verified
    // independently with a real Playwright run against the dev server at
    // exactly 960px (see CTO round report): before the fix, clientWidth was
    // 75px and scrollWidth 75/75/77/77/96px for the five labels (byte-for-
    // byte matching CEO's numbers); after the fix, clientWidth grew to 94px
    // and scrollWidth === clientWidth for all five (no more clipping).
    //
    // jsdom has no real layout/viewport engine, so it can't reproduce that
    // vw-driven pixel measurement here. What *is* genuinely computable here
    // — real CSS cascade resolution, not a string/regex check — is: (a) the
    // icon column got narrower (frees width for the label), and (b) the
    // label element no longer forces a single line via `white-space: nowrap`
    // (which is what makes `text-overflow: ellipsis` actually clip instead
    // of just being a no-op declaration).
    const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')
    const style = document.createElement('style')
    style.textContent = css
    document.head.appendChild(style)
    document.body.innerHTML = `
      <div class="hermes-energy-station">
        <span class="hermes-energy-copy"><strong>Composite Score</strong><small></small></span>
      </div>
    `
    const station = document.querySelector('.hermes-energy-station') as HTMLElement
    const label = document.querySelector('.hermes-energy-copy strong') as HTMLElement

    expect(getComputedStyle(station).gridTemplateColumns).toBe('22px 1fr')
    expect(getComputedStyle(label).whiteSpace).not.toBe('nowrap')
    expect(getComputedStyle(label).whiteSpace).toBe('normal')

    try {
      document.head.removeChild(style)
      document.body.innerHTML = ''
    } catch { /* best-effort cleanup */ }
  })

  it('N10 (string check only — see comment for why a computed-style proof is not possible here): widens the ~960px left-rail breakpoint clamp', () => {
    // CEO measured (real browser, 960px viewport, analysis workspace open):
    // left rail actual rendered width was cramped, card subtitle text ran
    // right up to the card edge — not broken (no 3-char title wraps), but
    // tight ("很擠"). Independently verified with a real Playwright run at
    // exactly 960px against the dev server: `[data-region='left-rail']`
    // computed width was 180px before this fix (18vw of 960px = 172.8px,
    // clamped up to the 180px floor) and 215px after (22vw of 960px =
    // 211.2px, clamped up to the new 215px floor) — a real, not just
    // declared, 35px gain, with zero clipped leaf text nodes
    // (scrollWidth > clientWidth) in either state.
    //
    // As established by the N6 (round 1) test above: jsdom does not
    // re-evaluate `@media (max-width:…)` rules against a simulated
    // `window.innerWidth` for `getComputedStyle` (verified empirically —
    // changing `window.innerWidth` via `Object.defineProperty` had no effect
    // on which of two conflicting matched-vs-unmatched-media rules jsdom
    // applied). So the automated regression guard here can only be a
    // string/regex check on the source, same limitation as N6 round 1; the
    // real computed-width proof above was done manually via Playwright, not
    // in this jsdom suite. NOT OBSERVED in this file: the actual rendered
    // pixel width — see the CTO round report for the Playwright numbers.
    const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')
    const midBreakpointMatch = css.match(/@media \(max-width:1024px\) \{([\s\S]*?)\n\}\n\n\/\* N6 \(round 2/)
    expect(midBreakpointMatch).not.toBeNull()
    const midBreakpointBlock = midBreakpointMatch![1]

    expect(midBreakpointBlock).toMatch(/--hermes-rail:\s*clamp\(215px,\s*22vw,\s*245px\);/)
  })
})

describe('N76 左軌：選單只縮不長，對話區保底且輸入框看得見', () => {
  const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')
  const rail = readFileSync(path.join(__dirname, 'HermesLeftRail.tsx'), 'utf8')

  it('選單 pane 只縮不長（flex 0 1 auto），且自己捲', () => {
    const base = css.slice(css.indexOf('.hermes-rail-menu {'))
    const body = base.slice(0, base.indexOf('}'))
    expect(body).toContain('flex: 0 1 auto')
    expect(body).toContain('overflow-y: auto')
  })

  it('對話區樓地板 300px——200px 會把 composer 擠到捲軸下面', () => {
    // 真正生效的是 inline style（inline 贏過 class），所以釘的是元件那一行。
    expect(rail).toContain("flexDirection: 'column', minHeight: 300,")
    const chat = css.slice(css.indexOf('.hermes-rail-chat {'))
    expect(chat.slice(0, chat.indexOf('}'))).toContain('min-height: 300px')
  })

  it('N73 的手動收合鈕已移除（選單本來就該自己縮，不該要人按）', () => {
    expect(rail).not.toContain('hermes-rail-menu-toggle')
    expect(rail).not.toContain('menuCollapsed')
    expect(css).not.toContain('hermes-rail-menu-toggle')
  })
})

describe('N74 頂欄下拉面板不得被工作區蓋住', () => {
  const topbar = readFileSync(path.join(__dirname, 'HermesTopBar.tsx'), 'utf8')
  const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')

  it('頂欄本身要疊在 module deck 之上（它是 stacking context，面板加數字沒用）', () => {
    const m = topbar.match(/height: 'var\(--hermes-top\)', zIndex: (\d+)/)
    expect(m).not.toBeNull()
    const topbarZ = Number(m![1])
    const deck = css.slice(css.indexOf('.hermes-module-deck'))
    const deckZ = Number(deck.match(/z-index:\s*(\d+)/)![1])
    expect(topbarZ).toBeGreaterThan(deckZ)
    // 但仍要低於 drilldown 遮罩，drilldown 打開時頂欄要跟著被壓暗。
    expect(topbarZ).toBeLessThan(49)
  })
})

describe('N75 被工作區蓋住的區塊不得留在 DOM 當隱形陷阱', () => {
  it('分歧卡只在沒有開工作區時 render', () => {
    const dash = readFileSync(path.join(__dirname, '..', 'pages', 'HermesDashboard.tsx'), 'utf8')
    expect(dash).toMatch(/\{!activeModule && \(\s*<HermesMobileDivergenceEntry/)
  })
})

describe('N78 意圖鈕：欄數跟著容器走，不要硬寫兩欄', () => {
  const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')

  it('用 auto-fit 而不是固定 1fr 1fr', () => {
    // ≥1280px 左軌會拆出獨立選單欄，寬度只有 clamp(124px,10vw,160px)。
    // 硬塞兩欄實測每顆鈕剩 55px、<b> 標題剩 39px、中文硬換行成 3 行。
    // 改 auto-fit 後同尺寸量到 118px / 102px / 1 行。
    // 這個選擇器出現兩次：窄視窗 @media 裡的 `1fr !important` 覆寫（檔案前段）
    // 與基礎規則（檔案後段）。要釘的是後者，所以取最後一個。
    const block = css.slice(css.lastIndexOf('.hermes-intent-picker>div {'))
    // 註解裡會出現 `1fr 1fr`（在說明為什麼不用它），先剝掉才不會誤判。
    const body = block.slice(0, block.indexOf('}')).replace(/\/\*[\s\S]*?\*\//g, '')
    expect(body).toContain('repeat(auto-fit, minmax(118px, 1fr))')
    expect(body).not.toContain('1fr 1fr')
  })
})

describe('N78 左軌收合的 details 不得佔位', () => {
  const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')

  it('關起來的 details 內容明寫 display:none', () => {
    // 瀏覽器原生的收合在這個 flex 直欄裡沒生效：實測 1440x900，「相似歷史」
    // details 是 open=false、自己只有 32px 高，子元素照樣排在 y=546→685，
    // 直接疊在 .hermes-focus-derived / .hermes-analysis-expectation 上。
    expect(css).toContain('.hermes-rail-menu>details:not([open])>*:not(summary) { display: none; }')
  })
})

describe('N76 對話區：pane 不捲，訊息串才捲', () => {
  const rail = readFileSync(path.join(__dirname, 'HermesLeftRail.tsx'), 'utf8')

  it('pane overflowY 是 hidden、訊息串 minHeight 是 0', () => {
    // 兩處要一起成立才會綠：pane 若是 auto，整塊（含 composer）一起捲走；
    // 訊息串若留 minHeight:140，pane 改 hidden 反而把 composer 裁掉。
    // 實測 6 個尺寸（960x800 / 1024x900 / 1100x950 / 1279x900 /
    // 1024x600 / 900x560）在只改其中一處時全 RED，兩處都改後 15/15 綠。
    expect(rail).toContain("minHeight: 300, overflowY: 'hidden'")
    expect(rail).toContain("flex: 1, minHeight: 0, overflowY: 'auto'")
  })
})

describe('N78 手機版站點編號不得與狀態列搶同一條車道', () => {
  const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')

  it('≤560px 的 .hermes-energy-index 掛底部而不是 top:7px', () => {
    // `.hermes-engine-activity` 是絕對定位、`top: 7px`、z-index 5 的狀態列。
    // 編號原本也是 `top: 7px`，兩塊文字直接疊在一起：實測 375x667 是
    // 「03」壓「風險評估」、430x932 是「02」壓「BTC」，七個模組全中。
    const block = css.slice(css.lastIndexOf('.hermes-energy-index {'))
    const body = block.slice(0, block.indexOf('}')).replace(/\/\*[\s\S]*?\*\//g, '')
    expect(body).toContain('bottom: 5px')
    expect(body).not.toMatch(/top:\s*7px/)
  })
})

describe('N80 站點狀態串不得被切到看不懂', () => {
  const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')
  const bar = readFileSync(path.join(__dirname, 'StageBar.tsx'), 'utf8')

  it('.hermes-energy-copy small 換行兩列而不是 nowrap 截斷', () => {
    // 內容是「0 待命 · 排隊 5 · 重試 0」這種狀態串，5 欄站點在 1280 寬每欄
    // 只有 ~76px。實測 nowrap 時被吃掉 42%~72%，畫面上只剩「0 待…」。
    const block = css.slice(css.indexOf('.hermes-energy-copy small {'))
    const body = block.slice(0, block.indexOf('}')).replace(/\/\*[\s\S]*?\*\//g, '')
    expect(body).toContain('-webkit-line-clamp: 2')
    expect(body).not.toMatch(/white-space:\s*nowrap/)
  })

  it('狀態串補 title 當最後保險', () => {
    expect(bar).toContain('<small title={`${stage.metric} ${stage.unit}`.trim()}>')
  })
})

describe('N80 窄螢幕頂欄不得把語言切換鈕擠出視窗', () => {
  const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')
  const bar = readFileSync(path.join(__dirname, 'HermesTopBar.tsx'), 'utf8')

  it('≤640px 收掉遙測膠囊的標題文字', () => {
    // 375 英文版實測頂欄內容 376px、可用 373px，語言鈕右緣 377 溢出。
    // 縮 padding/gap 量出來反而更寬（pad12→378、gap10→384），因為這一格會
    // 把讓出來的空間吃掉；只能讓內容變短。
    expect(css).toMatch(/\.hermes-telemetry-chip-label\s*\{\s*display:\s*none/)
  })

  it('膠囊收字之後名字改掛 aria-label，不能只剩兩個數字', () => {
    expect(bar).toContain("aria-label={t('telemetry')}")
  })

  it('FPS/畫質 HUD 常駐，且桌機與手機都維持右上定位', () => {
    const dashboard = readFileSync(path.join(__dirname, '..', 'pages', 'HermesDashboard.tsx'), 'utf8')

    expect(dashboard).toContain('<FpsMeter')
    expect(dashboard).toContain('fps={fps}')
    expect(dashboard).toContain('quality={quality}')
    expect(dashboard).toContain('measuring={measuring}')
    expect(dashboard).not.toMatch(/searchParams\.get\('fps'\).*FpsMeter/)
    const baseMeterBlock = css.slice(css.indexOf('.hermes-fps-meter {'))
    const baseMeter = baseMeterBlock.slice(0, baseMeterBlock.indexOf('}')).replace(/\/\*[\s\S]*?\*\//g, '')
    expect(baseMeter).toMatch(/top:\s*calc\(var\(--hermes-top\) \+ 12px\)/)
    expect(baseMeter).toMatch(/right:\s*12px/)
    expect(baseMeter).not.toMatch(/\b(?:bottom|left)\s*:/)
    const mobileMeter = css.match(/@media \(max-width:560px\)[\s\S]*?\.hermes-fps-meter\s*\{([^}]*)\}/)?.[1]
    expect(mobileMeter).toBeDefined()
    expect(mobileMeter).not.toMatch(/\b(?:bottom|left)\s*:/)
    expect(css).toMatch(/\.hermes-fps-quality::after\s*\{[^}]*content:\s*attr\(data-short\)/s)
  })

})
