// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { ApiEnvelope, EcoLinkResponseData } from '../lib/types'
import { getEcoLink } from '../lib/endpoints'
import { HermesI18nProvider } from '../hermes/hermesI18n'
import EcoLinkPage from './EcoLinkPage'

vi.mock('../lib/endpoints', () => ({
  getEcoLink: vi.fn(),
}))

function renderPage(initialUrl = '/eco-link') {
  return render(
    <HermesI18nProvider>
      <MemoryRouter initialEntries={[initialUrl]}>
        <EcoLinkPage />
      </MemoryRouter>
    </HermesI18nProvider>,
  )
}

describe('EcoLinkPage', () => {
  it('預設查詢 asset:arb，verdict: possible_relation 時渲染影響路徑面板', async () => {
    const response: ApiEnvelope<EcoLinkResponseData> = {
      ok: true,
      data: {
        illustrative: true,
        verdict: 'possible_relation',
        message: 'asset:arb 與 asset:eth 可能相關',
        impact_paths: [
          {
            event_id: 'upgrade:arb:stylus',
            path: ['asset:arb', 'asset:eth'],
            direction: 'mixed',
            confidence: 0.85,
            official_source_url: 'https://arbitrum.foundation/upgrade/stylus',
          },
        ],
      },
    }
    vi.mocked(getEcoLink).mockResolvedValueOnce(response)
    renderPage()
    expect(getEcoLink).toHaveBeenCalledWith('asset:arb', expect.anything())
    expect(await screen.findByText('asset:arb → asset:eth')).toBeInTheDocument()
  })

  it('verdict: insufficient_data 時顯示「資料不足，無法判定」', async () => {
    vi.mocked(getEcoLink).mockResolvedValueOnce({
      ok: true,
      data: { illustrative: true, verdict: 'insufficient_data', message: '資料不足，無法判定', impact_paths: [] },
    })
    renderPage('/eco-link?asset=asset:op')
    expect(await screen.findByText('資料不足，無法判定。')).toBeInTheDocument()
  })

  /** N67：CEO 回報「只有 ARB 有東西 其他也是空的」。原本 chip 掛了
   * asset:sol / asset:bnb，但 EcoLink 的官方來源 allowlist 只放行
   * arbitrum / optimism / ethereum 網域，這兩個資產永遠不可能有合法資料——
   * 是被 UI 推銷出來的死路。這條把 chip 綁回 fixture 實際收錄的資產，
   * 之後誰再加一個查不到東西的 chip 就會紅。 */
  it('快速建議只列出 fixture 真的收錄的資產，不推銷死路', () => {
    // 「查得動」的定義必須跟後端 `impact_paths_for` 一致：該資產要是某個升級
    // 事件的主體，而且它與該事件的受影響資產之間存在依賴邊。只出現在
    // impacted_asset_ids（asset:matic）或只當邊的另一端（asset:eth）都不算——
    // 那兩個查下去一樣是空的。信心度門檻不納入，asset:op 是刻意保留的
    // 「門檻有在擋」示範。
    const root = path.join(__dirname, '..', '..', '..', 'data')
    const edges = JSON.parse(readFileSync(path.join(root, 'ecolink_dependency_edges.json'), 'utf8'))
    const events = JSON.parse(readFileSync(path.join(root, 'ecolink_upgrade_events.json'), 'utf8'))
    const hasEdge = (a: string, b: string) =>
      edges.some(
        (e: { source_asset_id: string; target_asset_id: string }) =>
          (e.source_asset_id === a && e.target_asset_id === b) ||
          (e.source_asset_id === b && e.target_asset_id === a),
      )
    const covered = new Set<string>()
    for (const event of events) {
      for (const impacted of event.impacted_asset_ids) {
        if (impacted !== event.asset_id && hasEdge(event.asset_id, impacted)) covered.add(event.asset_id)
      }
    }
    const page = readFileSync(path.join(__dirname, 'EcoLinkPage.tsx'), 'utf8')
    const chips = /const SUGGESTIONS = \[([^\]]*)\]/.exec(page)?.[1] ?? ''
    const listed = [...chips.matchAll(/'([^']+)'/g)].map((m) => m[1])
    expect(listed.length).toBeGreaterThan(0)
    for (const chip of listed) expect([chip, covered.has(chip)]).toEqual([chip, true])
  })

  it('API 錯誤時顯示 ErrorState', async () => {
    vi.mocked(getEcoLink).mockResolvedValueOnce({
      ok: false,
      error: { code: 'network_error', message: '連線異常，請稍後再試' },
    })
    renderPage()
    expect(await screen.findByText('連線異常')).toBeInTheDocument()
  })

  it('點快速建議可切換查詢資產並帶入 URL', async () => {
    vi.mocked(getEcoLink).mockResolvedValue({
      ok: true,
      data: { illustrative: true, verdict: 'insufficient_data', message: '資料不足，無法判定', impact_paths: [] },
    })
    renderPage()
    await screen.findByText('資料不足，無法判定。')
    fireEvent.click(screen.getByRole('button', { name: 'asset:op' }))
    expect(getEcoLink).toHaveBeenCalledWith('asset:op', expect.anything())
  })
})
