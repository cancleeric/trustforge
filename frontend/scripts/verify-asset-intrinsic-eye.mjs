import { spawn } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import { chromium } from 'playwright'

const host = '127.0.0.1'
const port = Number(process.env.TRUSTFORGE_INTRINSIC_EYE_PORT ?? 4198)
const baseUrl = `http://${host}:${port}/eye-asset-intrinsic.html`
const outputDir = new URL('../../out/eye-878/', import.meta.url)
const viewports = [
  { name: 'mobile', width: 390, height: 844 },
  { name: 'desktop', width: 1440, height: 900 },
]
const panelScenarios = ['shadow', 'official', 'long', 'malformed']
const locales = ['zh-TW', 'en']
const failures = []
const evidence = []

const vite = spawn(
  process.platform === 'win32' ? 'npm.cmd' : 'npm',
  ['run', 'dev', '--', '--host', host, '--port', String(port), '--strictPort'],
  { cwd: new URL('..', import.meta.url), env: { ...process.env, NODE_ENV: 'development' }, stdio: ['ignore', 'pipe', 'pipe'] },
)

try {
  await waitForServer(vite, baseUrl)
  await mkdir(outputDir, { recursive: true })
  const browser = await chromium.launch()
  try {
    for (const locale of locales) {
      for (const viewport of viewports) {
        for (const scenario of panelScenarios) {
          for (const zoom of scenario === 'shadow' ? [1, 2] : [1]) {
            const context = await browser.newContext({
              viewport,
              locale: locale === 'zh-TW' ? 'zh-TW' : 'en-US',
              deviceScaleFactor: 1,
            })
            await context.addCookies([{ name: 'trustforge_hermes_locale', value: locale, url: baseUrl }])
            const page = await context.newPage()
            await page.goto(`${baseUrl}?scenario=${scenario}`, { waitUntil: 'networkidle' })
            await page.evaluate((factor) => { document.documentElement.style.zoom = String(factor) }, zoom)
            if (scenario === 'long') {
              await page.locator('details').evaluateAll((nodes) => nodes.forEach((node) => { node.open = true }))
            }
            const geometry = await page.evaluate(() => ({
              scrollWidth: document.documentElement.scrollWidth,
              innerWidth: window.innerWidth,
              bodyText: document.body.innerText,
              mode: document.querySelector('[data-intrinsic-mode]')?.getAttribute('data-intrinsic-mode') ?? null,
            }))
            const label = `${locale}/${viewport.name}/${scenario}/zoom-${zoom}`
            if (geometry.scrollWidth > geometry.innerWidth + 1) {
              failures.push(`${label}: horizontal overflow ${geometry.scrollWidth}>${geometry.innerWidth}`)
            }
            if (scenario === 'shadow' && geometry.mode !== 'shadow') failures.push(`${label}: shadow panel missing`)
            if (scenario === 'official' && geometry.mode !== 'official') failures.push(`${label}: official panel missing`)
            if (scenario === 'malformed' && !/資產結構資料格式不相容|Asset Structure payload is incompatible/.test(geometry.bodyText)) {
              failures.push(`${label}: fail-closed error copy missing`)
            }
            const screenshot = `${locale}-${viewport.name}-${scenario}-zoom-${zoom}.png`
            await page.screenshot({ path: new URL(screenshot, outputDir).pathname, fullPage: true })
            evidence.push({ locale, viewport, scenario, zoom, geometry, screenshot })
            await context.close()
          }
        }
        await runProductionPageStates(browser, locale, viewport)
      }
    }
  } finally {
    await browser.close()
  }
} finally {
  vite.kill('SIGTERM')
}

await writeFile(new URL('matrix.json', outputDir), `${JSON.stringify({ failures, evidence }, null, 2)}\n`)
if (failures.length) {
  console.error(failures.map((failure) => `- ${failure}`).join('\n'))
  process.exit(1)
}
console.log(`Asset intrinsic Eye matrix OK: ${evidence.length} renderings; evidence=${outputDir.pathname}`)

async function runProductionPageStates(browser, locale, viewport) {
  const expected = {
    'zh-TW': {
      loading: 'Hermes 正在建立 BTC 的手動分析工作…',
      empty: '尚無分析資料',
      error: '連線異常',
    },
    en: {
      loading: 'Hermes is creating a manual analysis job for BTC…',
      empty: 'No analysis data yet',
      error: 'Connection error',
    },
  }[locale]
  for (const scenario of ['loading', 'empty', 'error']) {
    const context = await browser.newContext({
      viewport,
      locale: locale === 'zh-TW' ? 'zh-TW' : 'en-US',
    })
    await context.addCookies([{ name: 'trustforge_hermes_locale', value: locale, url: baseUrl }])
    const page = await context.newPage()
    await page.route('**/api/**', (route) => {
      if (scenario === 'loading') return
      if (scenario === 'error') {
        return route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ ok: false, error: { code: 'network_error', message: 'eye fixture' } }),
        })
      }
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'offline', message: 'eye fixture' } }) })
    })
    const url = scenario === 'empty'
      ? `http://${host}:${port}/?qa=1&workspace=analyze`
      : `http://${host}:${port}/?qa=1&workspace=analyze&coin=BTC&type=multi_source&mode=risk&q=eye-878&sample=eye-878`
    await page.goto(url, { waitUntil: 'domcontentloaded' })
    const selector = scenario === 'loading' ? '.tf-loading-state' : scenario === 'error' ? '.tf-error-state' : '[role="status"]'
    await page.locator(selector).filter({ hasText: expected[scenario] }).first().waitFor({ state: 'visible' })
    const geometry = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      bodyText: document.body.innerText,
    }))
    const label = `${locale}/${viewport.name}/production-${scenario}`
    if (!geometry.bodyText.includes(expected[scenario])) failures.push(`${label}: localized production state missing`)
    if (geometry.scrollWidth > geometry.innerWidth + 1) failures.push(`${label}: horizontal overflow ${geometry.scrollWidth}>${geometry.innerWidth}`)
    const screenshot = `${locale}-${viewport.name}-production-${scenario}.png`
    await page.screenshot({ path: new URL(screenshot, outputDir).pathname, fullPage: true })
    evidence.push({ locale, viewport, scenario: `production-${scenario}`, zoom: 1, geometry, screenshot })
    await context.close()
  }
}

async function waitForServer(processHandle, url) {
  const started = Date.now()
  let stderr = ''
  processHandle.stderr.on('data', (chunk) => { stderr += chunk.toString() })
  while (Date.now() - started < 90_000) {
    if (processHandle.exitCode !== null) throw new Error(`Vite exited before serving ${url}: ${stderr}`)
    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 250))
    }
  }
  throw new Error(`Timed out waiting for ${url}`)
}
