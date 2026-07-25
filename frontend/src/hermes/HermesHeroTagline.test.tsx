// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HermesI18nProvider } from './hermesI18n'
import HermesHeroTagline from './HermesHeroTagline'

describe('HermesHeroTagline', () => {
  it('renders the hero tagline and badge on first screen (default zh-TW)', () => {
    render(
      <HermesI18nProvider>
        <HermesHeroTagline />
      </HermesI18nProvider>,
    )

    expect(
      screen.getByText('不只是再問 AI 一次——我們對每一條市場主張做多源裁判，附證據、血統與反方意見。'),
    ).toBeInTheDocument()
    expect(screen.getByText('多源 × 5 模式 × 不可竄改血統')).toBeInTheDocument()
  })
})
