// @vitest-environment jsdom

import { render, screen, waitFor } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import AgentCoreStatusBadge from './AgentCoreStatusBadge'
import * as endpoints from '../lib/endpoints'

vi.mock('../lib/endpoints', async () => {
  const actual = await vi.importActual('../lib/endpoints')
  return { ...actual, getAgentCoreStatus: vi.fn() }
})

test('shows configured state returned by the same-origin API', async () => {
  vi.mocked(endpoints.getAgentCoreStatus).mockResolvedValue({
    ok: true,
    data: {
      provider: 'agentcore',
      selected: true,
      runtime_configured: true,
      state: 'configured',
    },
  })

  render(<AgentCoreStatusBadge locale="en" />)

  await waitFor(() =>
    expect(screen.getByTestId('agentcore-status')).toHaveTextContent(
      'AgentCore selected',
    ),
  )
  expect(screen.getByTestId('agentcore-status')).toHaveAttribute(
    'title',
    'AgentCore selected',
  )
  expect(screen.getByRole('status')).toHaveAccessibleName('AgentCore selected')
})

test('does not claim a connection when the API fails', async () => {
  vi.mocked(endpoints.getAgentCoreStatus).mockRejectedValue(new Error('offline'))
  render(<AgentCoreStatusBadge locale="en" />)
  await waitFor(() =>
    expect(screen.getByTestId('agentcore-status')).toHaveTextContent(
      'Runtime unavailable',
    ),
  )
})
