// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import HermesTopBar from './HermesTopBar'
import { HermesI18nProvider } from './hermesI18n'

function renderTopBar() {
  return render(
    <MemoryRouter>
      <HermesI18nProvider>
        <HermesTopBar
          version="v0.test · GALAXY"
          costLedger={1.25}
          trackedCount={5}
          tierCounts={{ healthy: 0, moderate: 5, danger: 0 }}
          uplinkLatency="2.4s"
          serviceMonitor={{ OVERVIEW: 'ok' }}
        />
      </HermesI18nProvider>
    </MemoryRouter>,
  )
}

describe('HermesTopBar', () => {
  it('shows identity and cost as display-only text', () => {
    renderTopBar()
    expect(screen.getByText('v0.test · GALAXY')).toBeInTheDocument()
    expect(screen.getByText('$1.2500')).toBeInTheDocument()
  })

  // N70（CEO：「能按的都移到左邊欄」）：頂欄只剩顯示。這裡的負向控制是這幾顆
  // 導覽鈕——它們原本就在頂欄，改動前這個 expect 會抓到而 fail，所以能分辨
  // 「真的搬走了」與「測試根本沒抓到東西」。
  it('has no navigation or mode controls left in the top bar', () => {
    renderTopBar()
    for (const name of ['HERMES 主頁', '分析', '比較', '歷史趨勢', '來源狀態', '成本', '切換語言']) {
      expect(screen.queryByRole('button', { name })).toBeNull()
    }
    // 唯一允許存在的按鈕就是遙測膠囊。
    expect(screen.getAllByRole('button')).toHaveLength(1)
  })

  // N70（CEO：「狀態…放上方 做顯示 BAR 點了會打開」）
  it('keeps market telemetry collapsed until the chip is clicked', () => {
    renderTopBar()
    const chip = screen.getByRole('button', { expanded: false })
    expect(screen.queryByRole('group', { name: '市場遙測' })).toBeNull()
    fireEvent.click(chip)
    const panel = screen.getByRole('group', { name: '市場遙測' })
    expect(panel).toBeInTheDocument()
    expect(panel).toHaveTextContent('2.4s')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('group', { name: '市場遙測' })).toBeNull()
  })
})
