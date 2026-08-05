// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import EvidenceTable from './EvidenceTable'
import type { Evidence, EvidenceGroup } from '../lib/types'
import { HermesI18nProvider } from '../hermes/hermesI18n'

function makeEvidence(overrides: Partial<Evidence> = {}): Evidence {
  return {
    source: 'test-source',
    fetched_at: '2026-07-20T10:00:00Z',
    content_reference: 'Test content reference',
    related_claim: 'BTC 市場判斷',
    source_url: '',
    kind: 'onchain',
    trust: 0.75,
    trust_components: { reputation: 0.95 },
    flags: [],
    info_flags: [],
    ...overrides,
  }
}

const evidence: Evidence[] = [
  makeEvidence({ source: 'f2pool', content_reference: '算力: 828 TH/s', trust: 0.8 }),
  makeEvidence({ source: 'f2pool', content_reference: '算力: 855 TH/s', trust: 0.85 }),
  makeEvidence({ source: 'f2pool', content_reference: '算力: 891 TH/s', trust: 0.9 }),
  makeEvidence({ source: 'coindesk', content_reference: 'ETF 資金淨流入', kind: 'news', trust: 0.6 }),
]

const groupedEvidence: EvidenceGroup[] = [
  {
    representative_idx: 2,
    member_indices: [0, 1, 2],
    trend: 'rising',
    value_range: '828–891 TH/s',
    latest_value: '891.0 TH/s',
  },
  {
    representative_idx: 3,
    member_indices: [3],
    trend: null,
    value_range: null,
    latest_value: null,
  },
]

function Wrapper({ children }: { children: React.ReactNode }) {
  return <HermesI18nProvider>{children}</HermesI18nProvider>
}

describe('EvidenceTable', () => {
  it('keeps a fixed minimum table width inside a horizontal overflow container for mobile', () => {
    const { container } = render(<EvidenceTable evidence={evidence} />, { wrapper: Wrapper })
    expect(container.firstElementChild?.className).toContain('overflow-x-auto')
    expect(container.querySelector('table')?.className).toContain('min-w-[760px]')
  })
  it('renders flat mode when evidenceGroups is not provided', () => {
    render(<EvidenceTable evidence={evidence} />, { wrapper: Wrapper })
    // All 4 evidence items rendered individually
    expect(screen.getByText('E0')).toBeInTheDocument()
    expect(screen.getByText('E1')).toBeInTheDocument()
    expect(screen.getByText('E2')).toBeInTheDocument()
    expect(screen.getByText('E3')).toBeInTheDocument()
  })

  it('renders flat mode when evidenceGroups is null', () => {
    render(<EvidenceTable evidence={evidence} evidenceGroups={null} />, { wrapper: Wrapper })
    expect(screen.getByText('E0')).toBeInTheDocument()
    expect(screen.getByText('E3')).toBeInTheDocument()
  })

  it('renders grouped mode with evidenceGroups', () => {
    const { container } = render(<EvidenceTable evidence={evidence} evidenceGroups={groupedEvidence} />, { wrapper: Wrapper })
    // Group row shows member count badge
    expect(screen.getByText('3 筆觀測')).toBeInTheDocument()
    // Group row shows value range
    expect(screen.getByText('828–891 TH/s')).toBeInTheDocument()
    // Singleton group renders as normal row
    expect(screen.getByText('E3')).toBeInTheDocument()
    const groupedCells = container.querySelectorAll('tbody tr:first-child td')
    expect(groupedCells[1].textContent).toContain('F2pool')
    expect(groupedCells[2].textContent).toContain('算力: 891 TH/s')
    expect(groupedCells[3].textContent).toContain('2026-07-20T10:00:00Z')
  })

  it('group row shows trend badge', () => {
    render(<EvidenceTable evidence={evidence} evidenceGroups={groupedEvidence} />, { wrapper: Wrapper })
    expect(screen.getByLabelText('上升趨勢')).toBeInTheDocument()
  })

  it('group row shows latest value when collapsed', () => {
    render(<EvidenceTable evidence={evidence} evidenceGroups={groupedEvidence} />, { wrapper: Wrapper })
    expect(screen.getByText(/最新：891.0 TH\/s/)).toBeInTheDocument()
  })

  it('expanding group reveals member rows', () => {
    render(<EvidenceTable evidence={evidence} evidenceGroups={groupedEvidence} />, { wrapper: Wrapper })
    // Initially collapsed — member E0, E1, E2 not shown individually
    expect(screen.queryByText('E0')).not.toBeInTheDocument()
    // Click group row to expand
    const groupRow = screen.getByLabelText(/證據群組：F2pool/)
    fireEvent.click(groupRow)
    // Now member rows are visible
    expect(screen.getByText('E0')).toBeInTheDocument()
    expect(screen.getByText('E1')).toBeInTheDocument()
    expect(screen.getByText('E2')).toBeInTheDocument()
  })

  it('collapsing group hides member rows again', () => {
    render(<EvidenceTable evidence={evidence} evidenceGroups={groupedEvidence} />, { wrapper: Wrapper })
    const groupRow = screen.getByLabelText(/證據群組：F2pool/)
    fireEvent.click(groupRow) // expand
    expect(screen.getByText('E0')).toBeInTheDocument()
    fireEvent.click(groupRow) // collapse
    expect(screen.queryByText('E0')).not.toBeInTheDocument()
  })

  it('sorts the flat table by trust without changing evidence identities', () => {
    const { container } = render(<EvidenceTable evidence={evidence} />, { wrapper: Wrapper })
    fireEvent.click(screen.getByRole('button', { name: '信任分' }))
    const rows = Array.from(container.querySelectorAll('tbody tr'))
    expect(rows.map((row) => row.textContent?.match(/E\d/)?.[0])).toEqual(['E2', 'E1', 'E0', 'E3'])
    fireEvent.click(screen.getByRole('button', { name: /信任分/ }))
    const reversed = Array.from(container.querySelectorAll('tbody tr'))
    expect(reversed.map((row) => row.textContent?.match(/E\d/)?.[0])).toEqual(['E3', 'E0', 'E1', 'E2'])
  })

  it.each([
    ['來源', ['E3', 'E0', 'E1', 'E2']],
    ['時間', ['E0', 'E1', 'E2', 'E3']],
    // #1441: content_reference mixes CJK ('算力') and Latin ('ETF'). The
    // component uses an explicit comparator instead of localeCompare so the
    // order is stable across Node/ICU versions.
    ['摘要', ['E0', 'E1', 'E2', 'E3']],
  ])('sorts by %s deterministically', (column, expected) => {
    const { container } = render(<EvidenceTable evidence={evidence} />, { wrapper: Wrapper })
    fireEvent.click(screen.getByRole('button', { name: column }))
    const rows = Array.from(container.querySelectorAll('tbody tr'))
    expect(rows.map((row) => row.textContent?.match(/E\d/)?.[0])).toEqual(expected)
  })
})
