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
const scenarios = ['shadow', 'official', 'long', 'loading', 'empty', 'malformed', 'error']
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
        for (const scenario of scenarios) {
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
            if ((scenario === 'malformed' || scenario === 'error') && !/不相容|incompatible/.test(geometry.bodyText)) {
              failures.push(`${label}: fail-closed error copy missing`)
            }
            if (scenario === 'loading' && !/Loading asset structure/.test(geometry.bodyText)) failures.push(`${label}: loading state missing`)
            if (scenario === 'empty' && geometry.mode !== null) failures.push(`${label}: empty legacy payload rendered a panel`)
            const screenshot = `${locale}-${viewport.name}-${scenario}-zoom-${zoom}.png`
            await page.screenshot({ path: new URL(screenshot, outputDir).pathname, fullPage: true })
            evidence.push({ locale, viewport, scenario, zoom, geometry, screenshot })
            await context.close()
          }
        }
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
