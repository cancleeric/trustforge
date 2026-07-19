// @vitest-environment jsdom

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HoyaBitIntegrationNotice } from './StatusPage'

describe('HoyaBitIntegrationNotice', () => {
  it('labels the integration as partial and names every unavailable online contract', () => {
    render(<HoyaBitIntegrationNotice />)

    const notice = screen.getByRole('status')
    expect(notice).toHaveTextContent('HOYA BIT：部分接')
    expect(notice).toHaveTextContent('ticker')
    expect(notice).toHaveTextContent('depth／orderbook／trades')
    expect(notice).toHaveTextContent('未設定時不會列為即時真值來源')
  })
})
