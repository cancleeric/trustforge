// @vitest-environment jsdom

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import HermesExecutionPanel from './HermesExecutionPanel'
import { executionLogDownload } from '../lib/executionLogDownload'
import type { Evidence, ExecutionEvent, ExecutionManifest, Report } from '../lib/types'

const execution: ExecutionManifest = {
  agent: 'hermes', run_id: 'hermes-test-run', started_at: '2026-07-13T00:00:00Z', elapsed_sec: 4.2, budget_sec: 900,
  nodes: [
    { id: 'source_ingestion', label: '來源蒐集', order: 1 },
    { id: 'claim_extraction', label: '主張抽取', order: 2 },
    { id: 'trust_reasoning', label: '信任推理', order: 3 },
    { id: 'evidence_assembly', label: '證據組裝', order: 4 },
    { id: 'report_delivery', label: '報告交付', order: 5 },
  ],
}

const event: ExecutionEvent = {
  ts: '2026-07-13T00:00:01Z', elapsed_sec: 1, tool: 'ingestion.collect', summary: '來源完成',
  params: { hermes: { run_id: 'hermes-test-run', agent: 'hermes', node_id: 'source_ingestion', node_label: '來源蒐集', node_order: 1, status: 'completed' } },
}

const sourceEvent: ExecutionEvent = {
  ts: '2026-07-13T00:00:01Z', elapsed_sec: 1.2, tool: 'ingestion.source', summary: 'sec-edgar：ok，2 documents，48.0 ms',
  params: { source: 'sec-edgar', kind: 'regulatory', outcome: 'ok', document_count: 2, duration_ms: 48, hermes: { run_id: 'hermes-test-run', agent: 'hermes', node_id: 'source_ingestion', node_label: '來源蒐集', node_order: 1, status: 'completed' } },
}

const report = {
  coin: 'BTC', question: '分析 BTC', market_judgment: '中性', facts: [], inferences: [], key_basis: [],
  confidence: 0.5, calibrated_confidence: 0.5, decision_state: 'normal', limits: [], could_flip: [],
  contrarian: [], generated_at: '2026-07-13T00:00:00Z', direction: '中性', question_type: 'multi_source',
  cross_source_signal: null,
} as Report

describe('HermesExecutionPanel', () => {
  it('renders the stable agent graph and auditable run id', () => {
    render(<HermesExecutionPanel execution={execution} events={[event, sourceEvent]} report={report} evidence={[] as Evidence[]} />)
    expect(screen.getByText('Hermes Agent')).toBeInTheDocument()
    expect(screen.getByText(/hermes-test-run/)).toBeInTheDocument()
    expect(screen.getAllByText('來源蒐集').length).toBeGreaterThan(0)
    expect(screen.getAllByText('報告交付').length).toBeGreaterThan(0)
    expect(screen.getByText('來源完成')).toBeInTheDocument()
    expect(screen.getByText('sec-edgar')).toBeInTheDocument()
    expect(screen.getByText('48.0 ms')).toBeInTheDocument()
  })

  it('exports a standard JSON execution envelope', () => {
    const artifact = executionLogDownload(execution, [event, sourceEvent])
    const payload = JSON.parse(artifact.body)
    expect(artifact.name).toBe('hermes-test-run-execution-log.json')
    expect(artifact.type).toBe('application/json')
    expect(payload.execution.run_id).toBe('hermes-test-run')
    expect(payload.events).toHaveLength(2)
    expect(payload.events[1].tool).toBe('ingestion.source')
  })
})
