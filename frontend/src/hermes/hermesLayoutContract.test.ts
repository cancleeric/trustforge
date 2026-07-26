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
    expect(css).toContain('background: linear-gradient(180deg, #050b13, #03080f)')
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
    expect(dashboard).toContain('{!activeModule && (')
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
    expect(css).toContain('left: 18px')
  })

  it('does not restart the Hermes report for every score counter frame', () => {
    const dashboard = readFileSync(path.join(__dirname, '..', 'pages', 'HermesDashboard.tsx'), 'utf8')

    expect(dashboard).toContain('const displayScoreRef = useRef(0)')
    expect(dashboard).toContain('const start = displayScoreRef.current')
    expect(dashboard).not.toContain('}, [displayScore])')
  })

  it('provides real light tokens for every non-dashboard Hermes route', () => {
    const css = readFileSync(path.join(__dirname, 'hermes.css'), 'utf8')
    const app = readFileSync(path.join(__dirname, '..', 'App.tsx'), 'utf8')

    expect(app).toContain('<HermesI18nProvider>')
    expect(css).toContain(":root[data-theme='light'] .hermes-surface")
    expect(css).toContain(":root[data-theme='light'] .app-header")
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
