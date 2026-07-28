import { useMemo, useState } from 'react'
import type { Evidence, ExecutionEvent, ExecutionManifest, Report } from '../lib/types'
import ReportDownloads from './ReportDownloads'
import { useHermesI18n, type MessageKey } from '../hermes/hermesI18n'

const NODE_FALLBACK_DEFS: Array<{ id: string; labelKey: MessageKey; order: number }> = [
  { id: 'source_ingestion', labelKey: 'hepNodeSourceIngestion', order: 1 },
  { id: 'claim_extraction', labelKey: 'hepNodeClaimExtraction', order: 2 },
  { id: 'trust_reasoning', labelKey: 'hepNodeTrustReasoning', order: 3 },
  { id: 'evidence_assembly', labelKey: 'hepNodeEvidenceAssembly', order: 4 },
  { id: 'report_delivery', labelKey: 'hepNodeReportDelivery', order: 5 },
]

function eventNode(event: ExecutionEvent, t: (key: MessageKey, params?: Record<string, string | number>) => string) {
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
  return { id: 'report_delivery', label: t('hepNodeReportDelivery'), status: 'observed', runId: undefined }
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

function nodeSummary(events: ExecutionEvent[], nodeId: string, t: (key: MessageKey, params?: Record<string, string | number>) => string) {
  const nodeEvents = events.filter((event) => eventNode(event, t).id === nodeId)
  const elapsed = nodeEvents.map((event) => event.elapsed_sec).filter(Number.isFinite)
  const duration = elapsed.length > 1 ? Math.max(...elapsed) - Math.min(...elapsed) : 0
  const failed = nodeEvents.some((event) => eventNode(event, t).status === 'failed')
  const completed = nodeEvents.some((event) => eventNode(event, t).status === 'completed')
  return { nodeEvents, duration, failed, completed }
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
  const { t } = useHermesI18n()
  const [query, setQuery] = useState('')
  const [nodeFilter, setNodeFilter] = useState('all')

  const NODE_FALLBACK = NODE_FALLBACK_DEFS.map((n) => ({ id: n.id, label: t(n.labelKey), order: n.order }))

  const normalizedExecution: ExecutionManifest = execution ?? {
    agent: 'hermes',
    run_id: events[0] ? eventNode(events[0], t).runId || 'legacy-run' : 'legacy-run',
    started_at: events[0]?.ts || report.generated_at,
    elapsed_sec: events.at(-1)?.elapsed_sec || 0,
    budget_sec: 900,
    nodes: NODE_FALLBACK,
  }

  const nodes = normalizedExecution.nodes?.length ? normalizedExecution.nodes : NODE_FALLBACK

  const visibleEvents = useMemo(() => events.filter((event) => {
    const haystack = `${event.tool} ${event.summary} ${eventNode(event, t).label}`.toLowerCase()
    return (nodeFilter === 'all' || eventNode(event, t).id === nodeFilter) && haystack.includes(query.toLowerCase())
  }), [events, nodeFilter, query, t])
  const sourceEvents = events.map(sourceDetails).filter((item): item is NonNullable<typeof item> => item !== null)
  const successfulSources = sourceEvents.filter((item) => item.outcome === 'ok').length
  const failedSources = sourceEvents.filter((item) => item.outcome === 'failed').length

  return (
    <section className="hermes-clip rounded-lg border border-tf-border bg-tf-card p-4" aria-label={t('hepAriaLabel')}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold tracking-wide text-tf-link">{t('hepKicker')}</p>
          <h2 className="mt-1 text-base font-semibold text-tf-text">{t('hepHeading')}</h2>
          <p className="mt-1 text-xs text-tf-text2">{t('hepDesc')}</p>
          <p className="mt-1 font-mono text-xs text-tf-muted">run_id: {normalizedExecution.run_id}</p>
        </div>
        <div className="text-right text-xs text-tf-muted">
          <div>{normalizedExecution.elapsed_sec.toFixed(2)}s / {normalizedExecution.budget_sec}s</div>
          <div>{events.length}{t('hepEventsCountSuffix')} · {successfulSources}{t('hepSourcesOkSuffix')}{failedSources ? ` · ${failedSources}${t('hepSourcesFailedSuffix')}` : ''}</div>
        </div>
      </div>

      <ol className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-5" aria-label={t('hepNodesAriaLabel')}>
        {nodes.slice().sort((a, b) => a.order - b.order).map((node) => {
          const { nodeEvents, duration, failed, completed } = nodeSummary(events, node.id, t)
          const done = completed || (node.id === 'report_delivery' && events.some((event) => event.tool === 'report.done'))
          return (
            <li key={node.id} className={`relative border p-3 ${failed ? 'border-tf-bad/70 bg-tf-bad/10' : done ? 'border-tf-good/70 bg-tf-good/10' : 'border-tf-border bg-tf-bg/40'}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs text-tf-muted">0{node.order}</span>
                <span className={`text-xs font-semibold ${failed ? 'text-tf-bad' : done ? 'text-tf-good' : 'text-tf-muted'}`}>{failed ? t('hepStatusPartialFail') : done ? t('hepStatusDone') : t('hepStatusWaiting')}</span>
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
          <h3 className="text-sm font-semibold text-tf-text">{t('hepSourceDetailsTitle')}</h3>
          <p className="mt-0.5 text-xs text-tf-muted">{t('hepSourceDetailsDesc')}</p>
        </div>
        <span className="text-xs text-tf-muted">{sourceEvents.length}{t('hepSourceEventCountSuffix')}</span>
      </div>
      {sourceEvents.length > 0 ? (
        <div className="mt-2 overflow-auto border border-tf-border" aria-label={t('hepSourceDetailsAria')}>
          <div className="grid min-w-[520px] grid-cols-[1.4fr_0.8fr_0.8fr_0.8fr] gap-2 border-b border-tf-border bg-tf-bg/50 px-3 py-2 text-xs font-semibold text-tf-muted">
            <span>{t('hepColSource')}</span><span>{t('hepColState')}</span><span>{t('hepColDocs')}</span><span>{t('hepColDuration')}</span>
          </div>
          {sourceEvents.map((item, index) => (
            <div key={`${item.source}-${index}`} className="grid min-w-[520px] grid-cols-[1.4fr_0.8fr_0.8fr_0.8fr] gap-2 border-b border-tf-border px-3 py-2 text-xs last:border-b-0">
              <span className="font-mono text-tf-text">{item.source} <span className="text-tf-muted">{item.kind}</span></span>
              <span className={item.outcome === 'failed' ? 'text-tf-bad' : item.outcome === 'ok' ? 'text-tf-good' : 'text-tf-muted'}>{item.outcome}</span>
              <span className="text-tf-text2">{item.documentCount ?? '-'}</span>
              <span className="font-mono text-tf-text2">{item.durationMs === null ? '-' : `${item.durationMs.toFixed(1)} ms`}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-2 border border-dashed border-tf-border px-3 py-4 text-sm text-tf-muted">{t('hepNoSourceEvents')}</div>
      )}

      <div className="mt-5 flex flex-wrap items-end gap-2 border-t border-tf-border pt-4">
        <label className="flex min-w-48 flex-1 flex-col gap-1 text-xs text-tf-muted">
          {t('hepSearchLabel')}
          <input value={query} onChange={(event) => setQuery(event.target.value)} className="rounded border border-tf-border bg-tf-bg px-2 py-1.5 text-sm text-tf-text" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-tf-muted">
          {t('hepNodeFilterLabel')}
          <select value={nodeFilter} onChange={(event) => setNodeFilter(event.target.value)} className="rounded border border-tf-border bg-tf-bg px-2 py-1.5 text-sm text-tf-text">
            <option value="all">{t('hepAllNodes')}</option>
            {nodes.map((node) => <option key={node.id} value={node.id}>{node.label}</option>)}
          </select>
        </label>
        {/* N71：實作已抽到 `ReportDownloads`，報告抬頭那排共用同一份。 */}
        <ReportDownloads execution={normalizedExecution} events={events} report={report} evidence={evidence} />
      </div>

      <div className="mt-3 max-h-64 overflow-auto border border-tf-border">
        {visibleEvents.map((event, index) => (
          <div key={`${event.ts}-${event.tool}-${index}`} className="grid grid-cols-[72px_1fr] gap-3 border-b border-tf-border px-3 py-2 last:border-b-0">
            <span className="font-mono text-xs text-tf-muted">+{event.elapsed_sec.toFixed(2)}s</span>
            <div>
              <p className="text-xs font-semibold text-tf-text">{eventNode(event, t).label} · {event.tool}</p>
              <p className="mt-0.5 text-xs text-tf-text2">{event.summary || t('hepEventRecorded')}</p>
            </div>
          </div>
        ))}
        {visibleEvents.length === 0 && <p className="p-3 text-sm text-tf-muted">{t('hepNoMatchingEvents')}</p>}
      </div>
    </section>
  )
}
