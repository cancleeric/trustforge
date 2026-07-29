// @vitest-environment jsdom
/**
 * Tests for AdminAgosPage.
 * Issue: #924 | Epic: #914
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AdminAgosPage from './AdminAgosPage'

const { loadSessionToken } = vi.hoisted(() => ({ loadSessionToken: vi.fn((): string | null => 'admin-test-token') }))
vi.mock('../lib/adminConsole', () => ({ loadSessionToken }))

const memory = {
  memory_id: 'memory-1', kind: 'semantic', provider: 'market-feed',
  evidence_eligible: true, evidence_eligible_verified: true,
  content_ref: '[REDACTED]', content_hash: 'memory-hash', run_id: 'run-1',
  published_at: '2026-01-01', retrieved_at: '2026-01-02',
  expires_at: null, created_at: '2026-01-02', lineage_rank: 1,
  selection_reason: 'question_rag_similarity', inclusion_status: 'included',
}
const skill = {
  skill_id: 'analysis-fundamental', revision_hash: 'skill-revision-hash',
  reason: 'run policy', frozen_at: '2026-01-02', family: 'analysis',
  risk_class: 'read_only', lifecycle: 'active', side_effect_class: 'none',
  dependencies: [{ to: 'source-market', relation: 'requires' }],
}
const tool = {
  invocation_id: 'invocation-1', tool_id: 'coingecko-price-fetch',
  input_hash: 'input-hash', output_hash: 'output-hash', status: 'success',
  error: null, evidence_refs: ['evidence-1'], started_at: '2026-01-02',
  completed_at: '2026-01-02', side_effect_class: 'read_only',
  evidence_class: 'trusted_evidence', approval_requirement: 'never',
}
const context = {
  manifest_id: 'manifest-1', run_id: 'run-1', content_hash: 'context-hash',
  token_budget: 1000, token_used: 250, created_at: '2026-01-02',
  included_count: 4, excluded_count: 1, exclusion_reasons: { stale: 1 },
  included_refs: {
    snapshot_ref: 'snapshot-1', question_ref: 'question-1',
    memory_refs: [{ memory_id: 'memory-1', kind: 'semantic', rank: 1, reason: 'selected', evidence_eligible: true }],
    skill_refs: [{ skill_id: 'analysis-fundamental', revision_hash: 'skill-revision-hash', reason: 'selected' }],
    tool_refs: [{ tool_id: 'coingecko-price-fetch', version: '1.2.3' }],
    policy_refs: [{ policy_id: 'policy-1', revision_hash: 'policy-hash' }],
  },
  excluded_refs: [{ ref_id: 'memory-old', ref_type: 'memory', reason: 'stale' }],
}

describe('AdminAgosPage', () => {
  beforeEach(() => {
    loadSessionToken.mockReturnValue('admin-test-token')
    vi.stubGlobal('fetch', vi.fn((url: string) => {
      const data = url.includes('/memories') ? { items: [memory] }
        : url.includes('/skills') ? { items: [skill] }
          : url.includes('/tools') ? { items: [tool] }
            : context
      return Promise.resolve(new Response(JSON.stringify({ status: 'ok', data }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }))
    }))
  })

  it('renders page title', () => {
    render(<AdminAgosPage />)
    expect(screen.getByText('Agent OS Admin')).toBeInTheDocument()
  })

  it('renders all 4 tabs', () => {
    render(<AdminAgosPage />)
    expect(screen.getByText('Memory')).toBeInTheDocument()
    expect(screen.getByText('Skills')).toBeInTheDocument()
    expect(screen.getByText('Tools')).toBeInTheDocument()
    expect(screen.getByText('Context')).toBeInTheDocument()
  })

  it('renders run_id input', () => {
    render(<AdminAgosPage />)
    expect(screen.getByLabelText('Run ID')).toBeInTheDocument()
  })

  it('renders query button', () => {
    render(<AdminAgosPage />)
    expect(screen.getByText('Query')).toBeInTheDocument()
  })

  it('query button is disabled when input empty', () => {
    render(<AdminAgosPage />)
    const btn = screen.getByText('Query')
    expect(btn).toBeDisabled()
  })

  it('sends the session token in the header and renders all four governance rails', async () => {
    render(<AdminAgosPage />)
    fireEvent.change(screen.getByLabelText('Run ID'), { target: { value: 'run 1' } })
    fireEvent.click(screen.getByText('Query'))

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4))
    for (const [url, init] of vi.mocked(fetch).mock.calls) {
      expect(String(url)).toContain('run_id=run%201')
      expect((init?.headers as Record<string, string>)['X-Admin-Token']).toBe('admin-test-token')
      expect(String(url)).not.toContain('admin-test-token')
    }

    expect(await screen.findByText('question_rag_similarity')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Skills' }))
    expect(screen.getByText('source-market', { exact: false })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Tools' }))
    expect(screen.getByText('trusted_evidence')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Context' }))
    expect(screen.getByText('snapshot-1')).toBeInTheDocument()
    expect(screen.getByText('1.2.3', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('policy-hash')).toBeInTheDocument()
  })

  it('fails closed without an admin token', () => {
    loadSessionToken.mockReturnValue(null)
    render(<AdminAgosPage />)
    expect(screen.getByRole('alert')).toHaveTextContent('Admin authorization is required')
    expect(screen.getByText('Query')).toBeDisabled()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('shows authorization errors returned by every endpoint', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(
      JSON.stringify({ status: 'error', error: { code: 'UNAUTHORIZED', message: 'Unauthorized' } }),
      { status: 401, headers: { 'Content-Type': 'application/json' } },
    ))
    render(<AdminAgosPage />)
    fireEvent.change(screen.getByLabelText('Run ID'), { target: { value: 'run-1' } })
    fireEvent.click(screen.getByText('Query'))
    expect(await screen.findByText('Admin authorization is required', { exact: false })).toBeInTheDocument()
  })
})
