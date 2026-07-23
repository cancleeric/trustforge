import { spawn } from 'node:child_process'
import { chromium } from 'playwright'

const PORT = Number(process.env.TRUSTFORGE_MOBILE_GEOMETRY_PORT ?? 4187)
const HOST = '127.0.0.1'
const BASE_URL = `http://${HOST}:${PORT}`
const VIEWPORTS = [
  { name: 'iphone-se', width: 375, height: 667 },
  { name: 'iphone-12-mini', width: 390, height: 844 },
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
        await page.goto(`${BASE_URL}${route.url}`, { waitUntil: 'networkidle' })
        await page.waitForSelector(route.selectors.root, { state: 'visible' })
        const boxes = await collectBoxes(page, route.selectors)
        assertGeometry({ viewport, routeName: route.name, boxes })
      }

      await page.close()
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
