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

  it('can be dismissed', () => {
    renderIt()
    fireEvent.click(screen.getByRole('button', { name: '關閉新手脈絡引導' }))
    expect(screen.queryByText('查代幣定位')).not.toBeInTheDocument()
  })
})
