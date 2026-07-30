// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HermesI18nProvider } from './hermesI18n'
import WorkspaceStageDrilldown from './WorkspaceStageDrilldown'
import { buildWorkspaceStageDetails } from './workspaceStageDetails'
import type { HermesWorkspaceModule } from './HermesModuleDeck'

const MODULES: HermesWorkspaceModule[] = ['analyze', 'compare', 'history', 'status', 'costs', 'whale']

describe('workspace stage detail contract', () => {
  it('creates 30 unique stage ids and five distinct headings per workspace', () => {
    const details = MODULES.flatMap((module) => buildWorkspaceStageDetails(module, 'zh-TW', null))
    expect(details).toHaveLength(30)
    expect(new Set(details.map((detail) => detail.id))).toHaveLength(30)
    for (const module of MODULES) {
      const headings = details.filter((detail) => detail.module === module).map((detail) => detail.label)
      expect(new Set(headings)).toHaveLength(5)
    }
  })

  it('states that a missing contract is unavailable instead of inventing a metric', () => {
    const detail = buildWorkspaceStageDetails('history', 'zh-TW', null)[2]
    expect(detail).toMatchObject({ id: 'history:2', status: 'unavailable', metric: undefined })
    render(<HermesI18nProvider><WorkspaceStageDrilldown detail={detail} onClose={() => undefined} /></HermesI18nProvider>)
    expect(screen.getByRole('dialog', { name: '每日回放' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('尚未提供這一階段的可驗證遙測契約')
  })

  it('uses only the corresponding pipeline telemetry and preserves exact metric semantics', () => {
    const detail = buildWorkspaceStageDetails('costs', 'zh-TW', {
      runId: 'run-7',
      pipelineStages: [
        { id: 'calls', label: 'calls', metric: '20', unit: '本頁已載入紀錄', status: 'completed' },
        { id: 'models', label: 'models', metric: '3', unit: '已記錄模型', status: 'completed' },
        { id: 'tokens', label: 'tokens', metric: '400', unit: '已記錄 token', status: 'completed' },
        { id: 'ledger', label: 'ledger', metric: 'sealed', unit: 'append-only', status: 'completed' },
        { id: 'total', label: 'total', metric: '$1.20', unit: '跨 run 已記錄成本', status: 'completed' },
      ],
    })[0]
    expect(detail.metric).toBe('20')
    expect(detail.unit).toBe('本頁已載入紀錄')
    expect(detail.facts).toContainEqual({ label: 'run', value: 'run-7' })
  })

  it('explicitly explains append-only and cross-run cost stages', () => {
    const costs = buildWorkspaceStageDetails('costs', 'zh-TW', null)
    expect(costs[3].purpose).toContain('append-only ledger')
    expect(costs[4].purpose).toContain('跨 run')
  })

  it('keeps every cost-ledger metric bound to its own typed stage contract', () => {
    const details = buildWorkspaceStageDetails('costs', 'zh-TW', {
      workspaceStageMetrics: [
        { metric: '50', unit: '本頁已載入的模型呼叫' },
        { metric: '2', unit: '已記錄模型' },
        { metric: '2,564', unit: '已記錄 token' },
        { metric: '3,385', unit: 'append-only ledger run' },
        { metric: '$0.0064', unit: '跨 run 已記錄 LLM 成本' },
      ],
    })
    expect(details.map(({ metric, unit }) => `${metric} ${unit}`)).toEqual([
      '50 本頁已載入的模型呼叫',
      '2 已記錄模型',
      '2,564 已記錄 token',
      '3,385 append-only ledger run',
      '$0.0064 跨 run 已記錄 LLM 成本',
    ])
  })
})
