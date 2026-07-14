import { useMemo, useState } from 'react'
import type { Evidence, ExecutionEvent, ExecutionManifest, Report } from '../lib/types'
import { executionLogDownload } from '../lib/executionLogDownload'

const NODE_FALLBACK = [
  { id: 'source_ingestion', label: '來源蒐集', order: 1 },
  { id: 'claim_extraction', label: '主張抽取', order: 2 },
  { id: 'trust_reasoning', label: '信任推理', order: 3 },
  { id: 'evidence_assembly', label: '證據組裝', order: 4 },
  { id: 'report_delivery', label: '報告交付', order: 5 },
]

function eventNode(event: ExecutionEvent) {
  const hermes = event.params.hermes
  if (typeof hermes === 'object' && hermes !== null) {
    const data = hermes as Record<string, unknown>
    if (typeof data.node_id === 'string' && typeof data.node_label === 'string') {
      return {
        id: data.node_id,
        label: data.node_label,
        status: typeof data.status === 'string' ? data.status : 'observed',
        runId: typeof data.run_id === 'string' ? data.run_id : undefined,
      }
    }
  }
  return { id: 'report_delivery', label: '報告交付', status: 'observed', runId: undefined }
}

function sourceDetails(event: ExecutionEvent) {
  if (event.tool !== 'ingestion.source') return null
  const params = event.params
  return {
    source: typeof params.source === 'string' ? params.source : 'unknown',
    kind: typeof params.kind === 'string' ? params.kind : 'unknown',
    durationMs: typeof params.duration_ms === 'number' ? params.duration_ms : null,
    documentCount: typeof params.document_count === 'number' ? params.document_count : null,
    outcome: typeof params.outcome === 'string' ? params.outcome : 'observed',
  }
}

function nodeSummary(events: ExecutionEvent[], nodeId: string) {
  const nodeEvents = events.filter((event) => eventNode(event).id === nodeId)
  const elapsed = nodeEvents.map((event) => event.elapsed_sec).filter(Number.isFinite)
  const duration = elapsed.length > 1 ? Math.max(...elapsed) - Math.min(...elapsed) : 0
  const failed = nodeEvents.some((event) => eventNode(event).status === 'failed')
  const completed = nodeEvents.some((event) => eventNode(event).status === 'completed')
  return { nodeEvents, duration, failed, completed }
}

function download(name: string, body: string, type: string) {
  const href = URL.createObjectURL(new Blob([body], { type }))
  const a = document.createElement('a')
  a.href = href
  a.download = name
  a.click()
  URL.revokeObjectURL(href)
}

function reportMarkdown(report: Report): string {
  const lines = [
    `# ${report.coin} 市場分析報告`,
    '',
    `> Hermes run: ${report.generated_at}`,
    `> 問題：${report.question}`,
    '',
    '## 1. 結論 / 市場判斷',
    report.market_judgment,
    '',
    '## 2. 關鍵依據',
    '### 事實',
    ...report.facts.map((item) => `- ${item}`),
    '### 推論',
    ...report.inferences.map((item) => `- ${item}`),
    '### Evidence 對應',
    ...report.key_basis.map((item) => `- ${item.claim} [${item.evidence_idx.map((index) => `E${index}`).join(', ')}]：${item.explanation}`),
    '',
    '## 3. 資訊完整度說明',
    `- 校準後資訊完整度：${report.calibrated_confidence.toFixed(2)}`,
    `- 決策狀態：${report.decision_state}`,
    ...report.limits.map((item) => `- 已知限制：${item}`),
    ...report.could_flip.map((item) => `- 可能推翻條件：${item}`),
  ]
  return lines.join('\n')
}

export default function HermesExecutionPanel({
  execution,
  events,
  report,
  evidence,
}: {
  execution?: ExecutionManifest
  events: ExecutionEvent[]
  report: Report
  evidence: Evidence[]
}) {
  const normalizedExecution: ExecutionManifest = execution ?? {
    agent: 'hermes',
    run_id: events[0] ? eventNode(events[0]).runId || 'legacy-run' : 'legacy-run',
    started_at: events[0]?.ts || report.generated_at,
    elapsed_sec: events.at(-1)?.elapsed_sec || 0,
    budget_sec: 900,
    nodes: NODE_FALLBACK,
  }
  const [query, setQuery] = useState('')
  const [nodeFilter, setNodeFilter] = useState('all')
  const nodes = normalizedExecution.nodes?.length ? normalizedExecution.nodes : NODE_FALLBACK
  const visibleEvents = useMemo(() => events.filter((event) => {
    const haystack = `${event.tool} ${event.summary} ${eventNode(event).label}`.toLowerCase()
    return (nodeFilter === 'all' || eventNode(event).id === nodeFilter) && haystack.includes(query.toLowerCase())
  }), [events, nodeFilter, query])
  const sourceEvents = events.map(sourceDetails).filter((item): item is NonNullable<typeof item> => item !== null)
  const successfulSources = sourceEvents.filter((item) => item.outcome === 'ok').length
  const failedSources = sourceEvents.filter((item) => item.outcome === 'failed').length

  return (
    <section className="rounded-lg border border-tf-border bg-tf-card p-4" aria-label="Hermes Agent execution">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-tf-link">Hermes Agent</p>
          <h2 className="mt-1 text-base font-semibold text-tf-text">執行旅程</h2>
          <p className="mt-1 text-xs text-tf-text2">來源蒐集到報告交付的每一個節點都保留在本次 run 中。</p>
          <p className="mt-1 font-mono text-xs text-tf-muted">run_id: {normalizedExecution.run_id}</p>
        </div>
        <div className="text-right text-xs text-tf-muted">
          <div>{normalizedExecution.elapsed_sec.toFixed(2)}s / {normalizedExecution.budget_sec}s</div>
          <div>{events.length} events · {successfulSources} sources ok{failedSources ? ` · ${failedSources} failed` : ''}</div>
        </div>
      </div>

      <ol className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-5" aria-label="Hermes 執行節點">
        {nodes.slice().sort((a, b) => a.order - b.order).map((node) => {
          const { nodeEvents, duration, failed, completed } = nodeSummary(events, node.id)
          const done = completed || node.id === 'report_delivery' && events.some((event) => event.tool === 'report.done')
          return (
            <li key={node.id} className={`relative border p-3 ${failed ? 'border-tf-bad/70 bg-tf-bad/10' : done ? 'border-tf-good/70 bg-tf-good/10' : 'border-tf-border bg-tf-bg/40'}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs text-tf-muted">0{node.order}</span>
                <span className={`text-xs font-semibold ${failed ? 'text-tf-bad' : done ? 'text-tf-good' : 'text-tf-muted'}`}>{failed ? '部分失敗' : done ? '完成' : '等待'}</span>
              </div>
              <p className="mt-2 text-sm font-semibold text-tf-text">{node.label}</p>
              <div className="mt-2 flex items-center justify-between gap-2 text-xs text-tf-muted">
                <span>{nodeEvents.length} events</span>
                <span className="tf-num">{duration > 0 ? `${duration.toFixed(2)}s` : '—'}</span>
              </div>
            </li>
          )
        })}
      </ol>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-tf-text">來源執行明細</h3>
          <p className="mt-0.5 text-xs text-tf-muted">每個來源的取得結果、文件數與網路耗時。</p>
        </div>
        <span className="text-xs text-tf-muted">{sourceEvents.length} 個來源事件</span>
      </div>
      {sourceEvents.length > 0 ? <div className="mt-2 overflow-auto border border-tf-border" aria-label="來源執行明細">
        <div className="grid min-w-[520px] grid-cols-[1.4fr_0.8fr_0.8fr_0.8fr] gap-2 border-b border-tf-border bg-tf-bg/50 px-3 py-2 text-xs font-semibold text-tf-muted">
          <span>來源</span><span>狀態</span><span>文件</span><span>耗時</span>
        </div>
        {sourceEvents.map((item, index) => <div key={`${item.source}-${index}`} className="grid min-w-[520px] grid-cols-[1.4fr_0.8fr_0.8fr_0.8fr] gap-2 border-b border-tf-border px-3 py-2 text-xs last:border-b-0">
          <span className="font-mono text-tf-text">{item.source} <span className="text-tf-muted">{item.kind}</span></span>
          <span className={item.outcome === 'failed' ? 'text-tf-bad' : item.outcome === 'ok' ? 'text-tf-good' : 'text-tf-muted'}>{item.outcome}</span>
          <span className="text-tf-text2">{item.documentCount ?? '-'}</span>
          <span className="font-mono text-tf-text2">{item.durationMs === null ? '-' : `${item.durationMs.toFixed(1)} ms`}</span>
        </div>)}
      </div> : <div className="mt-2 border border-dashed border-tf-border px-3 py-4 text-sm text-tf-muted">本次 run 沒有可呈現的來源事件；完整事件 log 仍可下載檢查。</div>}

      <div className="mt-5 flex flex-wrap items-end gap-2 border-t border-tf-border pt-4">
        <label className="flex min-w-48 flex-1 flex-col gap-1 text-xs text-tf-muted">
          搜尋事件
          <input value={query} onChange={(event) => setQuery(event.target.value)} className="rounded border border-tf-border bg-tf-bg px-2 py-1.5 text-sm text-tf-text" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-tf-muted">
          節點
          <select value={nodeFilter} onChange={(event) => setNodeFilter(event.target.value)} className="rounded border border-tf-border bg-tf-bg px-2 py-1.5 text-sm text-tf-text">
            <option value="all">全部節點</option>
            {nodes.map((node) => <option key={node.id} value={node.id}>{node.label}</option>)}
          </select>
        </label>
        <button onClick={() => download(`${normalizedExecution.run_id}-report.md`, reportMarkdown(report), 'text/markdown;charset=utf-8')} className="rounded border border-tf-link px-3 py-1.5 text-sm font-semibold text-tf-link">報告</button>
        <button onClick={() => download(`${normalizedExecution.run_id}-evidence.json`, JSON.stringify(evidence, null, 2), 'application/json')} className="rounded border border-tf-link px-3 py-1.5 text-sm font-semibold text-tf-link">Evidence</button>
        <button onClick={() => {
          const artifact = executionLogDownload(normalizedExecution, events)
          download(artifact.name, artifact.body, artifact.type)
        }} className="rounded border border-tf-link px-3 py-1.5 text-sm font-semibold text-tf-link">Log</button>
      </div>

      <div className="mt-3 max-h-64 overflow-auto border border-tf-border">
        {visibleEvents.map((event, index) => (
          <div key={`${event.ts}-${event.tool}-${index}`} className="grid grid-cols-[72px_1fr] gap-3 border-b border-tf-border px-3 py-2 last:border-b-0">
            <span className="font-mono text-xs text-tf-muted">+{event.elapsed_sec.toFixed(2)}s</span>
            <div>
              <p className="text-xs font-semibold text-tf-text">{eventNode(event).label} · {event.tool}</p>
              <p className="mt-0.5 text-xs text-tf-text2">{event.summary || '事件已記錄'}</p>
            </div>
          </div>
        ))}
        {visibleEvents.length === 0 && <p className="p-3 text-sm text-tf-muted">沒有符合條件的執行事件。</p>}
      </div>
    </section>
  )
}
