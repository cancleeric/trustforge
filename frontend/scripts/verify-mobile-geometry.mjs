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

    for (const viewport of HIT_TEST_VIEWPORTS) {
      await runHitTestMatrix(browser, viewport)
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

async function runHitTestMatrix(browser, viewport) {
  const label = `${viewport.width}x${viewport.height}`
  const context = await browser.newContext()
  // English strings are the longest of the two supported locales and are
  // what the original N22/N23 audits reproduced against.
  await context.addCookies([{ name: 'trustforge_hermes_locale', value: 'en', url: BASE_URL }])
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
      // beginner-mode toggle itself, the language toggle) that is
      // genuinely on-screen must remain reachable while the narrative is
      // open in its default state. A full-viewport click-blocking overlay
      // (like the removed backdrop) would fail every one of these. Buttons
      // whose center falls outside the viewport are skipped here — that's
      // a separate, pre-existing narrow-viewport topbar-overflow issue
      // (confirmed unrelated to the narrative: same position whether it's
      // open or closed), out of this fix's scope.
      const topbarButtons = Array.from(document.querySelectorAll('.hermes-topbar button, .hermes-topbar a'))
      for (const btn of topbarButtons) {
        const br = btn.getBoundingClientRect()
        const cx = br.x + br.width / 2
        const cy = br.y + br.height / 2
        const onScreen = cx >= 0 && cy >= 0 && cx <= window.innerWidth && cy <= window.innerHeight
        if (!onScreen) continue
        const r = probe(btn)
        if (r.visible && r.hits === 0) {
          const label = btn.getAttribute('aria-label') || btn.textContent?.trim().slice(0, 30) || btn.tagName
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
