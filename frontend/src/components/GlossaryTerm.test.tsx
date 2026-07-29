// @vitest-environment jsdom
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import GlossaryTerm from './GlossaryTerm'
import { GLOSSARY_BY_ID, GLOSSARY_CATALOG, HELP_CENTER_GLOSSARY } from '../lib/glossaryCatalog'

describe('GlossaryTerm', () => {
  it('opens by click and closes with Escape for keyboard touch users', () => {
    render(<GlossaryTerm term="trustScore" />)
    const trigger = screen.getByRole('button', { name: /信任分數/ })

    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('note')).toHaveTextContent('不是價格漲跌機率')

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('uses the shared glossary catalog for popovers and help center entries', () => {
    render(<GlossaryTerm term="fdv" />)

    expect(screen.getByRole('button', { name: /FDV/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /FDV/ }))
    expect(screen.getByRole('note')).toHaveTextContent(GLOSSARY_BY_ID.fdv.description)
    expect(HELP_CENTER_GLOSSARY.some((term) => term.term_id === 'fdv')).toBe(true)
  })

  it('preserves approved score-axis popover semantics', () => {
    expect(GLOSSARY_BY_ID.recency.description).toContain('獨立 0-100 子分數')
    expect(GLOSSARY_BY_ID.manipulation.label).toBe('抗操縱能力')
    expect(GLOSSARY_BY_ID.manipulation.description).toContain('喊單、誇大承諾與協同行為')
    expect(GLOSSARY_BY_ID.divergence.label).toBe('跨來源分歧')
    expect(GLOSSARY_BY_ID.divergence.description).toContain('結論越需要保守解讀')
  })

  it('shows a ⚠️ risk note for terms that have one', () => {
    render(<GlossaryTerm term="tvl" />)
    fireEvent.click(screen.getByRole('button', { name: /TVL/ }))

    const note = screen.getByRole('note')
    expect(note).toHaveTextContent('⚠️')
    expect(note).toHaveTextContent(GLOSSARY_BY_ID.tvl.riskNote as string)
  })

  it('does not render a risk note block for terms without one', () => {
    render(<GlossaryTerm term="market_cap" />)
    fireEvent.click(screen.getByRole('button', { name: /MC/ }))

    expect(GLOSSARY_BY_ID.market_cap.riskNote).toBeUndefined()
    expect(screen.getByRole('note')).not.toHaveTextContent('⚠️')
  })
})

describe('GlossaryTerm 新手模式白話提示（#847）', () => {
  const beginner = (on: boolean) => {
    if (on) document.documentElement.dataset.tfBeginner = '1'
    else delete document.documentElement.dataset.tfBeginner
  }
  afterEach(() => { beginner(false); vi.useRealTimers() })

  it('新手模式滑過去浮出白話短句，離開就收起來', () => {
    vi.useFakeTimers()
    beginner(true)
    render(<GlossaryTerm term="trustScore" />)
    const trigger = screen.getByRole('button', { name: /信任分數/ })

    fireEvent.pointerEnter(trigger.parentElement as HTMLElement)
    expect(screen.queryByRole('note')).not.toBeInTheDocument()  // 300ms 前不該浮
    act(() => { vi.advanceTimersByTime(320) })
    expect(screen.getByRole('note')).toHaveTextContent(GLOSSARY_BY_ID.trustScore.tooltip!['zh-TW'])

    fireEvent.pointerLeave(trigger.parentElement as HTMLElement)
    act(() => { vi.advanceTimersByTime(200) })
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('關掉新手模式就不浮——一般模式的畫面跟以前一模一樣', () => {
    vi.useFakeTimers()
    beginner(false)
    render(<GlossaryTerm term="trustScore" />)
    fireEvent.pointerEnter(screen.getByRole('button', { name: /信任分數/ }).parentElement as HTMLElement)
    act(() => { vi.advanceTimersByTime(500) })
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('點開拿到的仍是正式定義，不是白話短句', () => {
    beginner(true)
    render(<GlossaryTerm term="trustScore" />)
    fireEvent.click(screen.getByRole('button', { name: /信任分數/ }))
    // 白話版只在滑過時出現；點開＝要看完整解釋，內容必須是 description 原文。
    expect(screen.getByRole('note')).toHaveTextContent(GLOSSARY_BY_ID.trustScore.description)
  })

  it('白話提示是加上去的，不會改寫比賽方看的正式定義', () => {
    // #847 的白話文一律另存 tooltip 欄位。這條擋的是「有人為了讓新手看懂，
    // 直接把 description 改成口語」——那份是報告與說明中心用的措辭，不能動。
    expect(GLOSSARY_BY_ID.trustScore.description).toBe(
      '綜合來源信譽、交叉佐證、資料時效與抗操縱能力的可信程度；不是價格漲跌機率。',
    )
    expect(GLOSSARY_BY_ID.recency.description).toContain('獨立 0-100 子分數')
    for (const term of GLOSSARY_CATALOG) {
      if (!term.tooltip) continue
      expect(term.tooltip['zh-TW']).not.toBe(term.description)
      // 雙語是硬性要求：只寫中文的話，英文版使用者會滑出一排中文。
      expect(term.tooltip.en.length).toBeGreaterThan(0)
      expect(term.tooltip.en).not.toMatch(/[一-鿿]/)
    }
  })
})
