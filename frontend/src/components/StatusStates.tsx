import type { CSSProperties } from 'react'
import { useHermesI18n } from '../hermes/hermesI18n'

export function LoadingState({ label = '載入中…' }: { label?: string }) {
  return (
    <div className="tf-loading-state flex items-center justify-center rounded-lg border border-tf-border bg-tf-card p-8 text-sm text-tf-muted" role="status">
      <span className="tf-loading-scan" aria-hidden="true" />{label}
    </div>
  )
}

/** N3 fix: `message` comes straight from `apiClient.ts` / the backend envelope,
 * both of which hard-code zh-TW text regardless of the active UI locale (see
 * apiClient.ts's `timeoutFailure`/`parseFailure`/etc.). Rendering it directly
 * meant the error banner never respected the EN/zh-TW toggle. The fix maps
 * `error.code` to an i18n string pair instead of ever printing the raw
 * `message` — with a generic fallback for codes we don't recognize (e.g.
 * `analysis_failed`, `http_error`, `analysis_job_mismatch`, ...), so unknown
 * codes still render something localized rather than leaking backend text. */
export function ErrorState({ code, message, note, onRetry }: { code: string; message: string; note?: string; onRetry?: () => void }) {
  const { t } = useHermesI18n()
  const isAutomationPaused = code === 'automation_disabled'
  const isWip = code === 'analysis_wip'
  const tone = (isAutomationPaused || isWip) ? 'var(--color-tf-warn)' : 'var(--color-tf-bad)'
  const style = {
    '--tf-state-color': tone,
    borderColor: tone,
    backgroundColor: `color-mix(in srgb, ${tone} 8%, transparent)`,
  } as CSSProperties
  const title = isAutomationPaused ? t('errorTitleAutomationPaused')
    : isWip ? t('errorTitleAnalysisWip')
    : code === 'network_error' ? t('errorTitleNetwork')
      : code === 'timeout' ? t('errorTitleTimeout')
        : code === 'parse_error' ? t('errorTitleParse')
          : code === 'server_busy' ? t('errorTitleBusy')
            : code === 'analysis_timeout' ? t('errorTitleAnalysisTimeout')
              : t('errorTitleGeneric')
  const description = isAutomationPaused ? t('errorDescAutomationPaused')
    : isWip ? t('errorDescAnalysisWip')
    : code === 'network_error' ? t('errorDescNetwork')
      : code === 'timeout' ? t('errorDescTimeout')
        : code === 'parse_error' ? t('errorDescParse')
          : code === 'server_busy' ? t('errorDescBusy')
            : code === 'analysis_timeout' ? t('errorDescAnalysisTimeout')
              : t('errorDescGeneric')
  void message
  return (
    <div
      className="tf-error-state rounded-lg border p-4 text-sm"
      style={style}
      role={isAutomationPaused ? 'status' : 'alert'}
    >
      <span className="tf-error-signal" aria-hidden="true" />
      <div className="min-w-0 flex-1" title={`${title} (${code})`}>
        <p className="font-semibold" style={{ color: tone }}>{title}</p>
        <p className="truncate text-xs text-tf-text2">{description}{note ? `；${note}` : ''}</p>
        <p className="font-mono text-[10px] text-tf-muted opacity-60" aria-hidden="true">{code}</p>
      </div>
      <button className="tf-error-retry" type="button" title={t('errorRetryTitle')} onClick={() => (onRetry ? onRetry() : window.location.reload())}>{t('errorRetry')}</button>
    </div>
  )
}
