import { useEffect, type ReactNode } from 'react'
import type { Evidence } from '../lib/types'
import { sourceDisplayName } from '../lib/sourceBrand'
import GlossaryTerm from './GlossaryTerm'
import { useHermesI18n } from '../hermes/hermesI18n'

/** 原始快照 modal（R2 深色艦橋設計稿）：展示單筆 `Evidence` 的「不可竄改」
 * 原始資料與血統鏈。刻意只用 `Evidence` 上真實存在的欄位組 payload——
 * 設計稿範例塞了 price_usd/volume_24h 等假數字，這裡不比照捏造，改用
 * content_reference/related_claim/trust_components 等本來就有的真實欄位。 */

const JSON_KEY = 'var(--color-tf-link)'
const JSON_STRING = 'var(--color-tf-warn)'
const JSON_NUM = 'var(--color-tf-text)'
const JSON_PUNCT = 'var(--color-tf-muted)'

function buildPayload(ev: Evidence) {
  return {
    source: ev.source,
    kind: ev.kind,
    fetched_at: ev.fetched_at,
    trust: ev.trust,
    content_reference: ev.content_reference,
    related_claim: ev.related_claim,
    flags: ev.flags,
    data_lineage: ev.data_lineage ?? null,
  }
}

function jsonLine(parts: Array<[string, string]>): { html: ReactNode } {
  return {
    html: (
      <>
        {parts.map(([text, color], i) => (
          <span key={i} style={{ color }}>{text}</span>
        ))}
      </>
    ),
  }
}

function buildPayloadLines(ev: Evidence) {
  const flagsStr = ev.flags.length > 0 ? JSON.stringify(ev.flags) : '[]'
  const lineageStr = JSON.stringify(ev.data_lineage ?? null)
  const lines = [
    jsonLine([['{', JSON_PUNCT]]),
    jsonLine([[' "source"', JSON_KEY], [': ', JSON_PUNCT], [JSON.stringify(ev.source), JSON_STRING], [',', JSON_PUNCT]]),
    jsonLine([[' "kind"', JSON_KEY], [': ', JSON_PUNCT], [JSON.stringify(ev.kind), JSON_STRING], [',', JSON_PUNCT]]),
    jsonLine([[' "fetched_at"', JSON_KEY], [': ', JSON_PUNCT], [JSON.stringify(ev.fetched_at), JSON_STRING], [',', JSON_PUNCT]]),
    jsonLine([[' "trust"', JSON_KEY], [': ', JSON_PUNCT], [ev.trust.toFixed(3), JSON_NUM], [',', JSON_PUNCT]]),
    jsonLine([[' "content_reference"', JSON_KEY], [': ', JSON_PUNCT], [JSON.stringify(ev.content_reference), JSON_STRING], [',', JSON_PUNCT]]),
    jsonLine([[' "related_claim"', JSON_KEY], [': ', JSON_PUNCT], [JSON.stringify(ev.related_claim), JSON_STRING], [',', JSON_PUNCT]]),
    jsonLine([[' "flags"', JSON_KEY], [': ', JSON_PUNCT], [flagsStr, JSON_NUM], [',', JSON_PUNCT]]),
    jsonLine([[' "data_lineage"', JSON_KEY], [': ', JSON_PUNCT], [lineageStr, ev.data_lineage ? JSON_STRING : JSON_NUM]]),
    jsonLine([['}', JSON_PUNCT]]),
  ]
  return lines.map((l, i) => ({ n: i + 1, html: l.html }))
}

export default function SnapshotModal({ ev, onClose }: { ev: Evidence; onClose: () => void }) {
  const { t } = useHermesI18n()
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const payloadLines = buildPayloadLines(ev)
  const payloadJson = JSON.stringify(buildPayload(ev), null, 2)
  const weightPct = Math.round(Math.max(0, Math.min(1, ev.trust)) * 100)
  const snapshotId = `${ev.source}_${ev.fetched_at}`.replace(/[^a-zA-Z0-9]/g, '').slice(0, 24)

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t('smAriaLabelTemplate', { source: sourceDisplayName(ev.source) })}
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-5"
      onClick={onClose}
    >
      <div
        className="hermes-clip flex max-h-[calc(100vh-80px)] w-full max-w-[720px] flex-col rounded-[14px] border border-tf-accent/30 bg-tf-panel shadow-2xl"
        style={{ background: 'var(--color-tf-panel)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex-shrink-0 border-b border-tf-border px-6 py-4">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div>
              <p className="mb-2 flex items-center gap-2 text-[10.5px] font-semibold uppercase tracking-[0.22em] text-tf-link">
                <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-tf-link" />
                {t('smKicker')}
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-xl font-semibold text-tf-text">{sourceDisplayName(ev.source)}</span>
                <span
                  className="rounded-full border px-2.5 py-1 text-xs font-semibold"
                  style={{ color: 'var(--color-tf-green)', background: 'color-mix(in srgb, var(--color-tf-green) 12%, transparent)', borderColor: 'color-mix(in srgb, var(--color-tf-green) 40%, transparent)' }}
                >
                  <GlossaryTerm term="weight" label={`${t('smWeightLabel')} ${weightPct}`} compact />
                </span>
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label={t('smClose')}
              className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-[7px] border border-tf-border text-tf-muted hover:text-tf-text"
            >
              ✕
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-tf-muted">
            <span>{t('smSnapshotIdPrefix')}<span className="text-tf-text2">{snapshotId}</span></span>
            <span>·</span>
            <span>{t('smFetchedAtPrefix')}{ev.fetched_at}</span>
            <span>·</span>
            <span style={{ color: 'var(--color-tf-warn)' }}>
              <GlossaryTerm term="immutable" label={t('smImmutable')} compact />
            </span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          <p className="mb-3 text-[10px] uppercase tracking-[0.2em] text-tf-muted">{t('smMetaTitle')}</p>
          <div className="mb-6 flex flex-wrap gap-2">
            {[
              [t('smMetaSource'), sourceDisplayName(ev.source)],
              [t('smMetaKind'), ev.kind],
              [t('smMetaTrust'), ev.trust.toFixed(2)],
              [t('smMetaFlags'), String(ev.flags.length)],
            ].map(([k, v]) => (
              <span key={k} className="inline-flex items-center gap-2 rounded-[7px] border border-tf-border bg-tf-card px-3 py-1.5 text-xs text-tf-muted">
                <span className="text-tf-muted">{k}</span>
                <span className="text-tf-text">{v}</span>
              </span>
            ))}
          </div>

          <div className="mb-3 flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-[0.2em] text-tf-muted">{t('smPayloadTitle')}</span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void navigator.clipboard?.writeText(payloadJson)}
                className="rounded-[6px] border border-tf-accent/40 px-2.5 py-1 text-[10.5px] text-tf-link"
              >
                {t('smCopy')}
              </button>
              <a
                href={`data:application/json,${encodeURIComponent(payloadJson)}`}
                download={`${snapshotId || 'snapshot'}.json`}
                className="rounded-[6px] border border-tf-border px-2.5 py-1 text-[10.5px] text-tf-muted no-underline"
              >
                {t('smDownloadJson')}
              </a>
            </div>
          </div>
          <div className="mb-6 overflow-x-auto rounded-[10px] border border-tf-border bg-tf-well py-3">
            {payloadLines.map((l) => (
              <div key={l.n} className="flex items-start px-4 leading-[1.7]">
                <span className="mr-4 w-7 flex-shrink-0 text-right font-mono text-[11px] text-tf-border">{l.n}</span>
                <span className="font-mono text-xs whitespace-pre">{l.html}</span>
              </div>
            ))}
          </div>

          <p className="mb-3 text-[10px] uppercase tracking-[0.2em] text-tf-muted">
            <GlossaryTerm term="lineage" label={t('smLineageTitle')} compact />{t('smLineageSuffix')}
          </p>
          {ev.data_lineage ? (
            <div className="space-y-4 rounded-[10px] border border-tf-border bg-tf-card px-6 py-4">
              <div className="flex flex-wrap items-center gap-5">
                {[t('smLineageStepFetch'), t('smLineageStepArchive'), t('smLineageStepIncludeTemplate', { dataset: ev.data_lineage.dataset_name })].map((label, i, arr) => (
                  <div key={label} className="flex items-center gap-5">
                    <div className="flex flex-col items-center gap-2">
                      <span
                        className="flex h-8 w-8 rotate-45 items-center justify-center text-[9px]"
                        style={{ background: i < 2 ? 'color-mix(in srgb, var(--color-tf-accent) 15%, transparent)' : 'color-mix(in srgb, var(--color-tf-warn) 20%, transparent)', border: `1.5px solid ${i < 2 ? 'var(--color-tf-accent)' : 'var(--color-tf-warn)'}` }}
                      >
                        <span className="-rotate-45" style={{ color: i < 2 ? 'var(--color-tf-accent)' : 'var(--color-tf-warn)' }}>{i + 1}</span>
                      </span>
                      <span className="whitespace-nowrap text-center text-xs text-tf-text">{label}</span>
                    </div>
                    {i < arr.length - 1 && <div className="mb-5 h-0.5 w-12 bg-tf-accent" />}
                  </div>
                ))}
              </div>
              <dl className="grid gap-x-5 gap-y-2 text-xs sm:grid-cols-2">
                <div><dt className="inline text-tf-muted">{t('smLineageFile')}</dt><dd className="inline text-tf-text">{ev.data_lineage.file}</dd></div>
                <div><dt className="inline text-tf-muted">{t('smLineageRole')}</dt><dd className="inline text-tf-text">{ev.data_lineage.dataset_role}</dd></div>
                <div><dt className="inline text-tf-muted">{t('smLineageCoverage')}</dt><dd className="inline text-tf-text">{ev.data_lineage.coverage.start_date} ~ {ev.data_lineage.coverage.end_date}</dd></div>
                <div><dt className="inline text-tf-muted">{t('smLineageWindow')}</dt><dd className="inline text-tf-text">{ev.data_lineage.analysis_window}</dd></div>
                <div><dt className="inline text-tf-muted">{t('smLineagePair')}</dt><dd className="inline text-tf-text">{ev.data_lineage.trading_pair}</dd></div>
                <div><dt className="inline text-tf-muted">{t('smLineageRows')}</dt><dd className="inline text-tf-text">{ev.data_lineage.rows}</dd></div>
                <div><dt className="inline text-tf-muted">{t('smLineageTimeBasis')}</dt><dd className="inline text-tf-text">{ev.data_lineage.time_basis}</dd></div>
                <div><dt className="inline text-tf-muted">{t('smLineageInterval')}</dt><dd className="inline text-tf-text">{ev.data_lineage.interval}</dd></div>
                <div><dt className="inline text-tf-muted">{t('smLineageGeneratedAt')}</dt><dd className="inline text-tf-text">{ev.data_lineage.dataset_generated_at}</dd></div>
                <div><dt className="inline text-tf-muted">{t('smLineageUnit')}</dt><dd className="inline text-tf-text">{ev.data_lineage.price_unit}</dd></div>
                <div className="sm:col-span-2"><dt className="inline text-tf-muted">{t('smLineageColumns')}</dt><dd className="inline break-words font-mono text-tf-text">{ev.data_lineage.columns.join(', ')}</dd></div>
                <div className="sm:col-span-2"><dt className="inline text-tf-muted">{t('smLineageSha')}</dt><dd className="inline break-all font-mono text-tf-text">{ev.data_lineage.sha256}</dd></div>
              </dl>
            </div>
          ) : (
            <p className="rounded-[10px] border border-tf-border bg-tf-card px-4 py-3 text-xs text-tf-muted">
              {t('smNoLineage')}
            </p>
          )}
        </div>

        <div className="flex flex-shrink-0 items-center justify-between rounded-b-[14px] border-t border-tf-border bg-tf-panel px-6 py-4">
          <a href="/status" className="text-xs text-tf-link no-underline hover:underline">
            {t('smViewInStatus')}
          </a>
          <button
            type="button"
            onClick={onClose}
            className="rounded-[8px] px-6 py-2.5 text-xs font-bold tracking-wide text-tf-bg"
            style={{ background: 'linear-gradient(135deg,var(--color-tf-accent),#3bc0c8)' }}
          >
            {t('smCloseButton')}
          </button>
        </div>
      </div>
    </div>
  )
}
