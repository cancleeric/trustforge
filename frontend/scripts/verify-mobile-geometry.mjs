import { spawn } from 'node:child_process'
import { chromium } from 'playwright'

const PORT = Number(process.env.TRUSTFORGE_MOBILE_GEOMETRY_PORT ?? 4187)
const HOST = '127.0.0.1'
const BASE_URL = `http://${HOST}:${PORT}`
const VIEWPORTS = [
  { name: 'iphone-se', width: 375, height: 667 },
  { name: 'iphone-12-mini', width: 390, height: 844 },
]

// N21/N22/N23 (CEO real-browser hit-test audit): a dozen widths spanning
// phones through desktop, run through a real Playwright/Chromium hit-test
// matrix rather than a single manually-picked width, so a fix scoped to
// "the width the bug was originally found at" can't silently leave 11 other
// breakpoints broken (or newly break them).
const HIT_TEST_VIEWPORTS = [
  { width: 375, height: 667 }, { width: 390, height: 844 }, { width: 430, height: 932 },
  { width: 540, height: 720 }, { width: 561, height: 700 }, { width: 680, height: 500 },
  { width: 768, height: 1024 }, { width: 900, height: 620 }, { width: 960, height: 482 },
  { width: 1024, height: 768 }, { width: 1280, height: 800 }, { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
]

const MIN_TARGET = 24

const CONTRAST_ROUTES = [
  { name: 'status', url: '/?qa=1&reducedMotion=1&workspace=status' },
  { name: 'history', url: '/?qa=1&reducedMotion=1&workspace=history' },
  { name: 'costs', url: '/?qa=1&reducedMotion=1&workspace=costs' },
]

const routes = [
  {
    name: 'home',
    url: '/?qa=1&reducedMotion=1',
    selectors: {
      root: '.hermes-dashboard',
      topbar: '.hermes-topbar',
      leftRail: '[data-region="left-rail"]',
      galaxy: '[data-region="galaxy"]',
      rightRail: '.hermes-right-rail',
      stageBar: '.hermes-energy-deck',
    },
  },
  {
    name: 'analyze-module',
    url: '/?qa=1&reducedMotion=1&workspace=analyze',
    selectors: {
      root: '.hermes-dashboard',
      topbar: '.hermes-topbar',
      leftRail: '[data-region="left-rail"]',
      moduleDeck: '.hermes-module-deck',
      rightRail: '.hermes-right-rail',
      stageBar: '.hermes-energy-deck',
    },
  },
]

const failures = []

const vite = spawn(
  process.platform === 'win32' ? 'npm.cmd' : 'npm',
  ['run', 'dev', '--', '--host', HOST, '--port', String(PORT), '--strictPort'],
  {
    cwd: new URL('..', import.meta.url),
    env: { ...process.env, NODE_ENV: 'development' },
    stdio: ['ignore', 'pipe', 'pipe'],
  },
)

try {
  await waitForServer(vite, BASE_URL)
  const browser = await chromium.launch()
  try {
    for (const viewport of VIEWPORTS) {
      const page = await browser.newPage({ viewport })
      await page.route('**/api/**', (route) => {
        route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ error: 'mobile geometry offline fixture' }),
        })
      })

      for (const route of routes) {
        await page.goto(`${BASE_URL}${route.url}`, { waitUntil: 'domcontentloaded' })
        await page.waitForSelector(route.selectors.root, { state: 'visible' })
        // 換掉 networkidle 之後，root 一掛上就量會量到還沒渲染完的星系
        // （375/390 實測 "galaxy is not visible"）。這裡不能改成等每個
        // selector：右軌在 <=900px 根本不掛載、左軌在 <=430px 是刻意隱藏，
        // 等它們會永遠等不到。所以等 load 事件再給一段固定沉澱時間。
        await page.waitForLoadState('load')
        await page.waitForTimeout(1500)
        const boxes = await collectBoxes(page, route.selectors)
        assertGeometry({ viewport, routeName: route.name, boxes })
      }

      await page.close()
    }

    // N27 is language-dependent (CJK glyphs wrap where Latin labels don't at
    // the same width), so the topbar-focused assertions below run in both
    // supported locales rather than just 'en'.
    for (const viewport of HIT_TEST_VIEWPORTS) {
      for (const locale of ['en', 'zh-TW']) {
        await runHitTestMatrix(browser, viewport, locale)
        await runFullExperienceChecks(browser, viewport, locale)
        if (viewport.width <= 900) {
          await runBeginnerNarrativeSheetChecks(browser, viewport, locale)
        }
      }
    }

    await runThemeContrastChecks(browser)
  } finally {
    await browser.close()
  }
} finally {
  vite.kill('SIGTERM')
}

if (failures.length) {
  console.error(failures.map((failure) => `- ${failure}`).join('\n'))
  process.exit(1)
}

console.log(
  `Mobile geometry OK: ${VIEWPORTS.map((viewport) => `${viewport.width}x${viewport.height}`).join(', ')}`,
)
console.log(
  `Hit-test matrix OK: ${HIT_TEST_VIEWPORTS.map((v) => `${v.width}x${v.height}`).join(', ')}`,
)

async function runHitTestMatrix(browser, viewport, locale = 'en') {
  const label = `${viewport.width}x${viewport.height} ${locale}`
  const context = await browser.newContext()
  // English strings are the longest of the two supported locales and are
  // what the original N22/N23 audits reproduced against. N27's defect was
  // CJK-specific (labels wrap vertically at widths Latin text doesn't), so
  // callers also exercise zh-TW.
  await context.addCookies([{ name: 'trustforge_hermes_locale', value: locale, url: BASE_URL }])
  const page = await context.newPage()
  await page.setViewportSize(viewport)
  await page.route('**/api/**', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: '{}' }))
  await page.goto(`${BASE_URL}/?qa=1&reducedMotion=1`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.hermes-dashboard', { state: 'visible' })

  // N31 (CEO real-browser hit-test audit): beginnerMode defaults ON, so the
  // `.hermes-beginner-narrative` panel is open on every first visit. At
  // <=900px it is absolutely positioned (bottom:18, z-index:12) directly on
  // top of `.hermes-mobile-divergence` (z-index:7), which is *also* always
  // mounted at <=900px regardless of beginnerMode. Real Playwright clicks
  // (not just hit-test probes) on the dock's two controls must succeed while
  // the narrative is in its default open state — this is what a real mobile
  // first-time visitor hits.
  if (viewport.width <= 900) {
    const divergenceLabel = locale === 'zh-TW' ? '跨來源分歧' : 'CROSS-SOURCE DIVERGENCE'
    const tapReviewLabel = locale === 'zh-TW' ? '點擊查看' : 'tap to review'
    try {
      await page.locator('.hermes-mobile-divergence .tf-glossary > button', { hasText: divergenceLabel }).first()
        .click({ timeout: 3000 })
      await page.keyboard.press('Escape')
    } catch {
      failures.push(`${label}: "${divergenceLabel}" glossary trigger unreachable while beginner narrative open (default state), blocked by overlapping panel`)
    }
    try {
      await page.locator('.hermes-mobile-divergence > button', { hasText: tapReviewLabel }).first()
        .click({ timeout: 3000 })
      // Clicking this button opens the StageDrilldown overlay (scrim +
      // panel) — close it back out (Escape, wired in HermesDashboard) so it
      // doesn't contaminate the rest of this viewport's hit-test probes
      // below.
      await page.keyboard.press('Escape')
    } catch {
      failures.push(`${label}: mobile divergence tap-review button unreachable while beginner narrative open (default state), blocked by overlapping panel`)
    }
  }

  const result = await page.evaluate((minTarget) => {
    const out = { failures: [] }

    function probe(el) {
      const r = el.getBoundingClientRect()
      if (r.width <= 0 || r.height <= 0) return { visible: false, rect: r }
      const points = []
      for (let i = 0; i < 5; i++) {
        for (let j = 0; j < 5; j++) {
          points.push([
            r.x + (r.width * (i + 0.5)) / 5,
            r.y + (r.height * (j + 0.5)) / 5,
          ])
        }
      }
      let hits = 0
      let coverer = null
      let coveredByNarrative = false
      for (const [x, y] of points) {
        if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) continue
        const hit = document.elementFromPoint(x, y)
        if (hit && (hit === el || el.contains(hit))) {
          hits++
        } else if (hit) {
          coverer = hit.className || hit.tagName
          if (hit.closest('.hermes-beginner-narrative')) coveredByNarrative = true
        }
      }
      return { visible: true, rect: { w: r.width, h: r.height }, hits, coverer, coveredByNarrative }
    }

    // --- Phase 1: beginner narrative is open (default first-run state) ---
    // Its own dismiss control must be reachable and >=24x24.
    //
    // A prior attempt at this fix added a full-viewport `aria-hidden="true"`
    // backdrop div behind the narrative card to "catch" clicks that would
    // otherwise fall through to whatever's underneath. That backdrop was a
    // site-wide regression caught by the coordinator's own independent
    // audit (not by this script): since beginnerMode defaults ON, every
    // first-time visit had ~99.6% of the viewport intercepted by that one
    // div — including the topbar's own "Analyze" nav link and "Beginner
    // mode" toggle, i.e. users could not even turn beginner mode off. The
    // backdrop was removed. The assertions below are what SHOULD have
    // existed before that backdrop was ever added, and are written to
    // fail loudly (RED) if anything shaped like that backdrop reappears:
    // every topbar button must stay reachable while the narrative is open
    // in its default state, and no single `aria-hidden="true"` element may
    // cover a large majority of the viewport while intercepting pointer
    // events.
    const narrative = document.querySelector('.hermes-beginner-narrative')
    if (narrative) {
      const closeBtn = Array.from(narrative.querySelectorAll('button'))
        .find((b) => b.getAttribute('aria-label')?.toLowerCase().includes('close') || b.getAttribute('aria-label')?.includes('關閉'))
      if (closeBtn) {
        const r = probe(closeBtn)
        if (r.visible && r.hits === 0) out.failures.push(`beginner-narrative close button unreachable, covered by ${r.coverer}`)
        if (r.visible && (r.rect.w < minTarget || r.rect.h < minTarget)) {
          out.failures.push(`beginner-narrative close button ${r.rect.w.toFixed(1)}x${r.rect.h.toFixed(1)} under ${minTarget}x${minTarget}`)
        }
      } else {
        out.failures.push('beginner narrative open but no close button found')
      }

      // Any interactive-looking button in the topbar (nav links, the
      // beginner-mode toggle itself, the language toggle) must remain
      // reachable while the narrative is open in its default state. A
      // full-viewport click-blocking overlay (like the removed backdrop)
      // would fail every one of these.
      //
      // N28: buttons whose center falls outside the viewport used to be
      // skipped here, because at the time this was written the topbar had
      // a real, separate narrow-viewport overflow bug (the language toggle
      // and others fell off the right edge at 375-390px and 561-680px,
      // confirmed unrelated to the narrative). That overflow is now fixed
      // (hermes.css ≤560px/≤430px topbar collapse rules + the ≤900px
      // nav-adjacent-badge collapse), so every topbar button with non-zero
      // geometry is now required to be on-screen — this is the assertion
      // that would have caught N28 in the first place.
      const topbarButtons = Array.from(document.querySelectorAll('.hermes-topbar button, .hermes-topbar a'))
      for (const btn of topbarButtons) {
        const br = btn.getBoundingClientRect()
        if (br.width <= 0 || br.height <= 0) continue
        const label = btn.getAttribute('aria-label') || btn.textContent?.trim().slice(0, 30) || btn.tagName
        if (br.x < -1 || br.y < -1 || br.right > window.innerWidth + 1 || br.bottom > window.innerHeight + 1) {
          out.failures.push(`topbar control "${label}" overflows viewport x=${br.x.toFixed(1)} right=${br.right.toFixed(1)} innerWidth=${window.innerWidth}`)
          continue
        }
        const r = probe(btn)
        if (r.visible && r.hits === 0) {
          out.failures.push(`topbar control "${label}" unreachable while beginner narrative open, covered by ${r.coverer}`)
        }
      }

      // Direct area-coverage guard: no single aria-hidden pointer-blocking
      // element should cover most of the viewport. (This is the assertion
      // that would have caught the backdrop regression even before
      // anyone thought to check the topbar specifically.)
      const viewportArea = window.innerWidth * window.innerHeight
      for (const el of document.querySelectorAll('[aria-hidden="true"]')) {
        const cs = getComputedStyle(el)
        if (cs.pointerEvents === 'none') continue
        const r = el.getBoundingClientRect()
        const area = Math.max(0, r.width) * Math.max(0, r.height)
        if (viewportArea > 0 && area / viewportArea > 0.5) {
          out.failures.push(`aria-hidden element covers ${(100 * area / viewportArea).toFixed(1)}% of viewport and accepts pointer events: <${el.tagName.toLowerCase()} class="${el.className}">`)
        }
      }

      // N32 (CEO 2nd-pass audit): comprehensive scan, not just the specific
      // controls this file already knew to check. The previous version only
      // checked the galaxy's "Focus <coin>" chips in phase 2, *after* the
      // narrative had already been closed (see closeBtn?.click() below) —
      // that blind spot let a "push the narrative up to clear the mobile
      // divergence dock" fix silently start covering those chips instead
      // (same class of bug as the removed full-viewport backdrop: a fix
      // that only satisfies the specific assertions that already existed).
      //
      // Scope, deliberately narrower than "every element on screen":
      // - <=900px only. Above that the narrative is a centered modal-style
      //   card that has *always* intentionally sat above the right-rail HUD
      //   (same as every other viewport CEO already accepted); flagging
      //   that would just be noise, not a real bug.
      // - Only counts as a failure when the *narrative itself* is the thing
      //   actually covering the element (`coveredByNarrative`), not when
      //   some unrelated element (e.g. two orbiting galaxy planets legitimately
      //   overlapping each other) happens to win the same hit-test point —
      //   that's a pre-existing, separate condition unrelated to this bug.
      // - Excludes `.hermes-energy-deck` (StageBar): by design, on every
      //   viewport including ones CEO already signed off on, the onboarding
      //   card sits above the detail HUD until dismissed — that's intentional
      //   progressive disclosure, not a dead click, and is true even at
      //   baseline (before this fix existed).
      // - Excludes the narrative's own controls (checked separately above).
      if (window.innerWidth <= 900) {
        document.querySelectorAll('button, a[href], [role="button"]').forEach((el) => {
          if (el.closest('.hermes-beginner-narrative')) return
          if (el.closest('.hermes-energy-deck')) return
          const r = probe(el)
          if (!r.visible) return
          if (r.hits === 0 && r.coveredByNarrative) {
            const desc = el.getAttribute('aria-label') || el.textContent?.trim().slice(0, 30) || el.tagName
            out.failures.push(`interactive element "${desc}" covered by beginner narrative panel (default open state) at <=900px`)
          }
        })
      }

      // close it so phase 2 (separate evaluate() call below, after a real
      // event-loop turn for React to re-render) exercises the controls it
      // was covering, the same way a real user would after reading the
      // onboarding card.
      closeBtn?.click()
    }

    return out
  }, MIN_TARGET)

  await page.waitForSelector('.hermes-beginner-narrative', { state: 'detached' }).catch(() => {})

  const result2 = await page.evaluate((minTarget) => {
    const out = { failures: [] }

    function probe(el) {
      const r = el.getBoundingClientRect()
      if (r.width <= 0 || r.height <= 0) return { visible: false, rect: r }
      const points = []
      for (let i = 0; i < 5; i++) {
        for (let j = 0; j < 5; j++) {
          points.push([
            r.x + (r.width * (i + 0.5)) / 5,
            r.y + (r.height * (j + 0.5)) / 5,
          ])
        }
      }
      let hits = 0
      let coverer = null
      for (const [x, y] of points) {
        if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) continue
        const hit = document.elementFromPoint(x, y)
        if (hit && (hit === el || el.contains(hit))) {
          hits++
        } else if (hit) {
          coverer = hit.className || hit.tagName
        }
      }
      return { visible: true, rect: { w: r.width, h: r.height }, hits, coverer }
    }

    // --- Phase 2: narrative dismissed, exercise the rest of the surface ---

    // N21: LIVE TELEMETRY value cells must not be covered by the right rail.
    document.querySelectorAll('.hermes-telemetry-row b').forEach((b) => {
      const r = probe(b)
      if (r.visible && r.hits === 0) {
        out.failures.push(`telemetry value "${b.textContent}" fully covered by ${r.coverer}`)
      }
    })

    // N23: quick-selector "Focus <coin>" chips (the real, accessible entry
    // point) must be reachable and meet the 24x24 minimum target size.
    document.querySelectorAll('[aria-label^="Focus "]').forEach((btn) => {
      if (btn.tagName !== 'BUTTON') return
      const r = probe(btn)
      if (!r.visible) return
      if (r.hits === 0) out.failures.push(`"${btn.getAttribute('aria-label')}" unreachable, covered by ${r.coverer}`)
      if (r.rect.w < minTarget || r.rect.h < minTarget) {
        out.failures.push(`"${btn.getAttribute('aria-label')}" target ${r.rect.w.toFixed(1)}x${r.rect.h.toFixed(1)} under ${minTarget}x${minTarget}`)
      }
    })

    // N23: the decorative orbit planets must NOT be exposed as focusable
    // interactive elements (no keyboard-reachable dead buttons).
    document.querySelectorAll('.hermes-galaxy button').forEach((el) => {
      out.failures.push(`orbit planet is still a <button> (should be decorative): ${el.outerHTML.slice(0, 80)}`)
    })

    // language toggle: min click target. aria-label is the translated
    // t('language') string ("切換語言" / "Switch language"), not the
    // literal word "Language"/"語言" — match on the button that toggles
    // locale (identified by its rendered text, which is always "EN" or
    // "繁中" regardless of locale).
    const langBtn = Array.from(document.querySelectorAll('button')).find(
      (b) => b.textContent?.trim() === 'EN' || b.textContent?.trim() === '繁中',
    )
    if (langBtn) {
      const r = probe(langBtn)
      if (r.rect.w < minTarget || r.rect.h < minTarget) {
        out.failures.push(`language toggle ${r.rect.w.toFixed(1)}x${r.rect.h.toFixed(1)} under ${minTarget}x${minTarget}`)
      }
    } else {
      out.failures.push('language toggle button not found (selector broken)')
    }

    return out
  }, MIN_TARGET)

  for (const f of [...result.failures, ...result2.failures]) failures.push(`${label}: ${f}`)
  await context.close()
}

// N33 (CEO real-browser geometry audit): N31/N32 stopped the beginner
// narrative panel from overlapping the mobile divergence dock / galaxy
// chips at <=900px, but did so by pinning the panel's `top` to an
// empirically-measured `55.2vh` guess — which, on short/wide viewports
// (680x500, 900x620, 960x482), crushed the panel itself down to 32-42px
// tall while its content still needed ~350-480px of scrollHeight to read.
// A panel that never overlaps anything is not "fixed" if the content is
// physically unreadable, and beginnerMode defaults ON, so this was every
// new user's first impression.
//
// The panel is now a collapsible sheet: collapsed by default (small,
// content-driven height that can never collide with anything, replacing
// the vh guess entirely) and expandable on demand via a mobile-only toggle,
// at which point `top` pins to the real `--hermes-top` topbar-height
// variable instead of a measured constant. This checks the *expanded*
// state actually delivers on that: real geometry (clientHeight vs
// scrollHeight), a visible scroll affordance for any leftover overflow,
// the panel's own close button staying reachable via a real
// `locator.click()` while expanded (covering the screen), and no new
// horizontal overflow. Runs in a dedicated context so it can't leave the
// narrative dismissed for the shared runHitTestMatrix/runFullExperience
// checks that run alongside it.
async function runBeginnerNarrativeSheetChecks(browser, viewport, locale) {
  const label = `${viewport.width}x${viewport.height} ${locale} (beginner sheet)`
  const context = await browser.newContext()
  await context.addCookies([{ name: 'trustforge_hermes_locale', value: locale, url: BASE_URL }])
  const page = await context.newPage()
  await page.setViewportSize(viewport)
  await page.route('**/api/**', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: '{}' }))
  await page.goto(`${BASE_URL}/?qa=1&reducedMotion=1`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.hermes-dashboard', { state: 'visible' })
  await page.waitForSelector('.hermes-beginner-narrative', { state: 'visible' })

  // Expand it. On a pre-fix build there is no toggle at all (the panel was
  // always shown fully expanded), so this deliberately no-ops in that case
  // rather than failing for the wrong reason — the geometry assertion below
  // is what's meant to catch the actual regression either way.
  const toggle = page.locator('.hermes-beginner-narrative-toggle')
  if (await toggle.count()) {
    await toggle.first().click()
  }

  const geometry = await page.evaluate(() => {
    const el = document.querySelector('.hermes-beginner-narrative')
    if (!el) return null
    const style = getComputedStyle(el)
    return { clientHeight: el.clientHeight, scrollHeight: el.scrollHeight, overflowY: style.overflowY }
  })

  if (!geometry) {
    failures.push(`${label}: beginner narrative panel missing from DOM while checking expanded readability`)
  } else {
    const minReadable = Math.min(geometry.scrollHeight, 200)
    if (geometry.clientHeight < minReadable) {
      failures.push(
        `${label}: beginner narrative panel clientHeight=${geometry.clientHeight} scrollHeight=${geometry.scrollHeight}, ` +
        `unreadable (needs clientHeight >= min(scrollHeight,200)=${minReadable})`,
      )
    }
    if (geometry.clientHeight < geometry.scrollHeight - 1 && geometry.overflowY !== 'auto' && geometry.overflowY !== 'scroll') {
      failures.push(
        `${label}: beginner narrative panel content overflows (client=${geometry.clientHeight} scroll=${geometry.scrollHeight}) ` +
        `with no visible scroll affordance (overflow-y=${geometry.overflowY})`,
      )
    }
  }

  const closeLabel = locale === 'zh-TW' ? '關閉新手脈絡引導' : 'Close beginner guide'
  try {
    await page.locator(`.hermes-beginner-narrative [aria-label="${closeLabel}"]`).click({ timeout: 3000 })
  } catch {
    failures.push(`${label}: beginner narrative close button unreachable while panel expanded`)
  }

  const noHorizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)
  if (!noHorizontalOverflow) {
    failures.push(`${label}: horizontal overflow introduced while beginner narrative expanded`)
  }

  await context.close()
}

// N24/N25/N27: the default (beginner) topbar in runHitTestMatrix above only
// ever renders the single "analyze" nav item, so it can't exercise the rest
// of the nav row (compare/history/sources/costs) where N27's CJK wrapping
// and clipping actually showed up, nor the "?" glossary triggers (N25),
// which only render once the right rail has real content. This runs with
// the "full experience" cookie so the whole nav row and glossary triggers
// are present, in both locales (N27 is CJK-specific).
async function runFullExperienceChecks(browser, viewport, locale) {
  const label = `${viewport.width}x${viewport.height} ${locale} (full experience)`
  const context = await browser.newContext()
  await context.addCookies([
    { name: 'trustforge_hermes_locale', value: locale, url: BASE_URL },
    { name: 'trustforge_hermes_experience', value: 'full', url: BASE_URL },
  ])
  const page = await context.newPage()
  await page.setViewportSize(viewport)
  await page.route('**/api/**', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: '{}' }))
  await page.goto(`${BASE_URL}/?qa=1&reducedMotion=1`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.hermes-dashboard', { state: 'visible' })

  const result = await page.evaluate((minTarget) => {
    const out = []

    // N27: nav labels (analyze/compare/history/sources/costs) must render
    // as a single line inside the topbar's own bounds, not wrap and get
    // clipped by the topbar's fixed height.
    const topbar = document.querySelector('.hermes-topbar')
    const topbarBox = topbar ? topbar.getBoundingClientRect() : null
    document.querySelectorAll('.hermes-nav-item').forEach((el) => {
      const r = el.getBoundingClientRect()
      if (r.width <= 0 || r.height <= 0) return
      const label = el.textContent?.trim() || '(nav item)'
      if (topbarBox && (r.top < topbarBox.top - 1 || r.bottom > topbarBox.bottom + 1)) {
        out.push(`nav item "${label}" clipped by topbar bounds: item top=${r.top.toFixed(1)} bottom=${r.bottom.toFixed(1)} topbar top=${topbarBox.top.toFixed(1)} bottom=${topbarBox.bottom.toFixed(1)}`)
      }
      // N24: every nav item is an equally-weighted click target, including
      // "analyze", not just its siblings.
      if (r.height < minTarget) {
        out.push(`nav item "${label}" ${r.width.toFixed(1)}x${r.height.toFixed(1)} under ${minTarget}x${minTarget}`)
      }
    })

    // N28: with the full toolbar rendered (ship toggle + cost ledger also
    // visible), every topbar control must still stay on-screen.
    document.querySelectorAll('.hermes-topbar button, .hermes-topbar a').forEach((btn) => {
      const r = btn.getBoundingClientRect()
      if (r.width <= 0 || r.height <= 0) return
      const label = btn.getAttribute('aria-label') || btn.textContent?.trim().slice(0, 30) || btn.tagName
      if (r.x < -1 || r.y < -1 || r.right > window.innerWidth + 1 || r.bottom > window.innerHeight + 1) {
        out.push(`topbar control "${label}" overflows viewport x=${r.x.toFixed(1)} right=${r.right.toFixed(1)} innerWidth=${window.innerWidth}`)
      }
      // N29: every topbar control (home logo, mode/reduced-motion toggles,
      // ship-upgrade button, ...) must meet the 24x24 minimum click target,
      // same as the nav items and language toggle already covered above.
      if (r.height < minTarget) {
        out.push(`topbar control "${label}" ${r.width.toFixed(1)}x${r.height.toFixed(1)} under ${minTarget}x${minTarget} height`)
      }
    })

    // N30: the mobile divergence dock's "點擊查看 →" tap-review button must
    // meet the same 24x24 minimum click target as any other control.
    document.querySelectorAll('.hermes-mobile-divergence > button').forEach((btn) => {
      const r = btn.getBoundingClientRect()
      if (r.width <= 0 || r.height <= 0) return
      const label = btn.textContent?.trim() || '(tap-review button)'
      if (r.width < minTarget || r.height < minTarget) {
        out.push(`mobile divergence tap-review button "${label}" ${r.width.toFixed(1)}x${r.height.toFixed(1)} under ${minTarget}x${minTarget}`)
      }
    })

    // N25: "?" glossary-term triggers (信任分數?/來源信譽?/...) must meet the
    // same 24x24 minimum click target as any other control.
    document.querySelectorAll('.tf-glossary > button').forEach((btn) => {
      const r = btn.getBoundingClientRect()
      if (r.width <= 0 || r.height <= 0) return
      const label = btn.textContent?.trim() || '(glossary term)'
      if (r.width < minTarget || r.height < minTarget) {
        out.push(`glossary term "${label}" ${r.width.toFixed(1)}x${r.height.toFixed(1)} under ${minTarget}x${minTarget}`)
      }
    })

    return out
  }, MIN_TARGET)

  for (const f of result) failures.push(`${label}: ${f}`)

  // N30 (CEO real-browser hit-test audit, 768x1024): at <=900px the desktop
  // HermesRightRail (with its own "跨來源分歧" glossary term) is CSS-hidden
  // (display:none) in favour of HermesMobileDivergenceEntry, but both stayed
  // mounted in the DOM. A locator resolving `.tf-glossary > button` in DOM
  // order (HermesRightRail is mounted before the mobile entry) picks the
  // display:none copy first and hangs waiting for it to become visible —
  // a real, reproducible click-timeout, not a probe false positive.
  if (viewport.width <= 900) {
    const divergenceLabel = locale === 'zh-TW' ? '跨來源分歧' : 'CROSS-SOURCE DIVERGENCE'
    try {
      await page.locator('.tf-glossary > button', { hasText: divergenceLabel }).first().click({ timeout: 3000 })
      // close the popover this click just opened so it can't shadow the
      // separate tap-review reachability check below.
      await page.keyboard.press('Escape')
    } catch {
      failures.push(`${label}: "${divergenceLabel}" glossary trigger unreachable via in-DOM-order click (resolves to a hidden duplicate before the visible mobile entry)`)
    }
    try {
      await page.locator('.hermes-mobile-divergence > button').first().click({ timeout: 3000 })
    } catch {
      failures.push(`${label}: mobile divergence "${await page.locator('.hermes-mobile-divergence > button').first().textContent().catch(() => '')}" tap-review button unreachable`)
    }
  }

  await context.close()
}

async function collectBoxes(page, selectors) {
  return page.evaluate((selectorMap) => {
    const result = {}
    for (const [key, selector] of Object.entries(selectorMap)) {
      const element = document.querySelector(selector)
      if (!element) {
        result[key] = null
        continue
      }

      const rect = element.getBoundingClientRect()
      const style = window.getComputedStyle(element)
      result[key] = {
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
        right: rect.right,
        bottom: rect.bottom,
        display: style.display,
        visibility: style.visibility,
        opacity: Number(style.opacity || 1),
      }
    }
    return result
  }, selectors)
}

function assertGeometry({ viewport, routeName, boxes }) {
  const label = `${viewport.width}x${viewport.height} ${routeName}`
  const leftRailVisible = viewport.width > 560
  const rightRailVisible = viewport.width > 900
  const required = ['root', 'topbar', 'stageBar']
  if (leftRailVisible) required.push('leftRail')
  if (rightRailVisible) required.push('rightRail')
  if (routeName === 'home') required.push('galaxy')
  if (routeName === 'analyze-module') required.push('moduleDeck')

  for (const key of required) {
    const box = boxes[key]
    if (!box || box.display === 'none' || box.visibility === 'hidden' || box.opacity === 0) {
      failures.push(`${label}: ${key} is not visible`)
      continue
    }
    if (box.width <= 0 || box.height <= 0) {
      failures.push(`${label}: ${key} has non-positive geometry ${formatBox(box)}`)
    }
    if (box.x < -1 || box.y < -1 || box.right > viewport.width + 1 || box.bottom > viewport.height + 1) {
      failures.push(`${label}: ${key} overflows viewport ${formatBox(box)}`)
    }
  }

  const content = boxes.moduleDeck ?? boxes.galaxy
  if (!leftRailVisible) assertHiddenOrEmpty(label, 'leftRail', boxes.leftRail)
  if (!rightRailVisible) assertHiddenOrEmpty(label, 'rightRail', boxes.rightRail)

  if (content) {
    assertNotOverlap(label, 'topbar', boxes.topbar, 'content', content)
    assertNotOverlap(label, 'stageBar', boxes.stageBar, 'content', content)
    if (leftRailVisible) assertSeparated(label, 'leftRail', boxes.leftRail, 'content', content)
    if (rightRailVisible) assertSeparated(label, 'content', content, 'rightRail', boxes.rightRail)
  }
}

function assertHiddenOrEmpty(label, name, box) {
  if (!box) return
  const visible = box.display !== 'none' && box.visibility !== 'hidden' && box.opacity > 0
  if (visible && box.width > 1 && box.height > 1) {
    failures.push(`${label}: ${name} should be hidden or empty on mobile ${formatBox(box)}`)
  }
}

function assertNotOverlap(label, aName, a, bName, b) {
  if (!a || !b) return
  const separated = a.bottom <= b.y + 1 || b.bottom <= a.y + 1 || a.right <= b.x + 1 || b.right <= a.x + 1
  if (!separated) failures.push(`${label}: ${aName} overlaps ${bName}`)
}

function assertSeparated(label, aName, a, bName, b) {
  if (!a || !b) return
  if (a.right > b.x + 1) failures.push(`${label}: ${aName} intrudes into ${bName}`)
}

function formatBox(box) {
  return `x=${round(box.x)} y=${round(box.y)} w=${round(box.width)} h=${round(box.height)}`
}

function round(value) {
  return Math.round(value * 10) / 10
}

async function waitForServer(processHandle, url) {
  const started = Date.now()
  let stderr = ''
  processHandle.stderr.on('data', (chunk) => {
    stderr += chunk.toString()
  })

  while (Date.now() - started < 90_000) {
    if (processHandle.exitCode !== null) {
      throw new Error(`Vite exited before serving ${url}: ${stderr}`)
    }

    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 250))
    }
  }

  throw new Error(`Timed out waiting for ${url}`)
}

// N64 (CEO real-browser audit): HERMES 面板底色寫死在 .hermes-root
// (#02040a) 與元件 inline style，不走 tf-* token；一旦有人再幫
// `.hermes-surface` 加一組淺色主題的「文字」token，就會變成深底深字。
// 實測 RED：預設（無存主題→light）「系統狀態」標題對比 1.25、部署版本的
// 版號值對比 1.15，深色主題同一顆 16.58——字有 render，只是看不見。
// 所以這裡不驗 CSS 寫法，直接在真瀏覽器裡量兩種主題下的實際對比，
// 讓「HERMES 不受 light 主題影響」這條契約有可執行的守門員。
async function runThemeContrastChecks(browser) {
  for (const theme of ['light', 'dark']) {
    for (const route of CONTRAST_ROUTES) {
      const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
      await page.addInitScript((value) => localStorage.setItem('tf-theme', value), theme)
      await page.goto(`${BASE_URL}${route.url}`, { waitUntil: 'domcontentloaded' })
      await page.waitForSelector('.hermes-dashboard', { state: 'visible' })
      await page.waitForLoadState('load')
      await page.waitForTimeout(1800)

      const offenders = await page.evaluate(() => {
        const channel = (c) => {
          const v = c / 255
          return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
        }
        const luminance = ([r, g, b]) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
        const parse = (value) => (value.match(/[\d.]+/g) || [0, 0, 0]).slice(0, 3).map(Number)
        // 底色要往上找到第一個「不透明」的祖先，否則量到 rgba(0,0,0,0)
        // 會把每個元素都判成黑底，變成一支只會亂叫的假警報。
        const opaqueBackdrop = (node) => {
          let current = node
          while (current) {
            const parts = getComputedStyle(current).backgroundColor.match(/[\d.]+/g)
            if (parts && (parts.length < 4 || Number(parts[3]) > 0.5)) return parse(getComputedStyle(current).backgroundColor)
            current = current.parentElement
          }
          return [0, 0, 0]
        }

        const workspace = document.querySelector('.hermes-dashboard')
        if (!workspace) return [{ text: '(no dashboard)', ratio: 0, required: 4.5 }]
        const found = []
        for (const node of workspace.querySelectorAll('*')) {
          if (node.children.length) continue
          const text = (node.textContent || '').trim()
          if (!text || text.length > 40) continue
          const box = node.getBoundingClientRect()
          if (box.width < 4 || box.height < 4 || box.bottom < 0 || box.top > window.innerHeight) continue
          const style = getComputedStyle(node)
          if (style.visibility === 'hidden' || style.opacity === '0') continue
          const opacity = Number(style.opacity) || 1
          const backdrop = opaqueBackdrop(node)
          const blended = parse(style.color).map((c, i) => c * opacity + backdrop[i] * (1 - opacity))
          const a = luminance(blended)
          const b = luminance(backdrop)
          const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
          const size = parseFloat(style.fontSize)
          const large = size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700)
          const required = large ? 3 : 4.5
          // 門檻壓在 2.0：這支要抓的是「深底深字整片看不見」這一類，
          // 不是把既有一堆 3.x 的 muted 文字一次全部變成 gate 失敗
          // （那些是另一筆待辦，混進來只會讓這支永遠紅、失去鑑別力）。
          if (ratio < Math.min(required, 2)) found.push({ text, ratio: Number(ratio.toFixed(2)), required })
        }
        return found
      })

      if (offenders.length) {
        const sample = offenders.slice(0, 5).map((o) => `"${o.text}" ${o.ratio}:1`).join(', ')
        failures.push(
          `theme=${theme} workspace=${route.name}: ${offenders.length} text node(s) below 2:1 contrast (dark-on-dark) — ${sample}`,
        )
      }

      await page.close()
    }
  }
}
