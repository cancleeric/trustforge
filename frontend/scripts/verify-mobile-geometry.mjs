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
        await page.goto(`${BASE_URL}${route.url}`, { waitUntil: 'networkidle' })
        await page.waitForSelector(route.selectors.root, { state: 'visible' })
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
      }
    }
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
  await page.goto(`${BASE_URL}/?qa=1&reducedMotion=1`, { waitUntil: 'networkidle' })
  await page.waitForSelector('.hermes-dashboard', { state: 'visible' })

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
  await page.goto(`${BASE_URL}/?qa=1&reducedMotion=1`, { waitUntil: 'networkidle' })
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

  while (Date.now() - started < 20_000) {
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
