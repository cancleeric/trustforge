// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import HermesBeginnerNarrative from './HermesBeginnerNarrative'
import { HermesI18nProvider } from '../hermes/hermesI18n'

function renderIt() {
  return render(
    <MemoryRouter>
      <HermesI18nProvider>
        <HermesBeginnerNarrative />
      </HermesI18nProvider>
    </MemoryRouter>,
  )
}

describe('HermesBeginnerNarrative', () => {
  it('renders the 3 beginner steps with CTAs linking the three context modules', () => {
    renderIt()
    expect(screen.getByText('查代幣定位')).toBeInTheDocument()
    expect(screen.getByText('名詞解釋 + 風險提示')).toBeInTheDocument()
    expect(screen.getByText('同層比較 + 生態聯動')).toBeInTheDocument()
    // module② glossary CTA must point at where annotations are live (/asset-context), not /help
    expect(screen.getByRole('button', { name: '查資產脈絡 →' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '看名詞標註 →' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '同層比較 →' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '生態聯動 →' })).toBeInTheDocument()
  })

  // N14：浮層曾用 left:50% + translateX(-50%) + width:min(960px, 100vw-96px)，
  // 在 809×650 會橫向蓋住左欄送出按鈕（真瀏覽器 elementFromPoint 落在浮層上）。
  // jsdom 沒有版面引擎，這裡改用「把 computed style 的 calc 依實際 CSS 變數值
  // 代入求值」的幾何契約：浮層左緣必須落在左欄寬度之外。
  it('geometry contract: expanded overlay never horizontally overlaps the left rail', () => {
    renderIt()
    const overlay = document.querySelector('.hermes-beginner-narrative') as HTMLElement
    expect(overlay).toBeTruthy()
    const style = getComputedStyle(overlay)

    // 809px 視窗（max-width:900px 斷點）實際解析出的左欄寬度
    const RAIL_PX = 250
    const evalCalc = (value: string): number => {
      const substituted = value
        .replace(/var\(--hermes-right-rail,\s*var\(--hermes-rail,\s*0px\)\)/g, `${RAIL_PX}px`)
        .replace(/var\(--hermes-rail,\s*0px\)/g, `${RAIL_PX}px`)
      const expr = substituted.replace(/^calc\((.*)\)$/, '$1').replace(/px/g, '')
      if (!/^[\d\s+*/.()-]+$/.test(expr)) throw new Error(`unresolved css value: ${value}`)
      return Number(new Function(`return (${expr})`)())
    }

    // 不得再用置中位移把自己拉回左欄上方
    expect(style.transform).not.toContain('translateX(-50%)')
    expect(style.left).not.toBe('50%')

    const leftPx = evalCalc(style.left)
    expect(leftPx).toBeGreaterThanOrEqual(RAIL_PX)
    // 右緣同理需避開右欄
    expect(evalCalc(style.right)).toBeGreaterThanOrEqual(0)
  })

  it('can be dismissed', () => {
    renderIt()
    fireEvent.click(screen.getByRole('button', { name: '關閉新手脈絡引導' }))
    expect(screen.queryByText('查代幣定位')).not.toBeInTheDocument()
  })
})
