import type { CSSProperties } from 'react'

export function LoadingState({ label = '載入中…' }: { label?: string }) {
  return (
    <div className="tf-loading-state flex items-center justify-center rounded-lg border border-tf-border bg-tf-card p-8 text-sm text-tf-muted" role="status">
      <span className="tf-loading-scan" aria-hidden="true" />{label}
    </div>
  )
}

export function ErrorState({ code, message }: { code: string; message: string }) {
  const isAutomationPaused = code === 'automation_disabled'
  const tone = isAutomationPaused ? 'var(--color-tf-warn)' : 'var(--color-tf-bad)'
  const style = {
    '--tf-state-color': tone,
    borderColor: tone,
    backgroundColor: `color-mix(in srgb, ${tone} 8%, transparent)`,
  } as CSSProperties
  const title = isAutomationPaused ? '自動工作已暫停'
    : code === 'network_error' ? '連線異常'
      : code === 'timeout' ? '回應逾時'
        : code === 'parse_error' ? '資料格式異常'
          : '服務異常'
  return (
    <div
      className="tf-error-state rounded-lg border p-4 text-sm"
      style={style}
      role={isAutomationPaused ? 'status' : 'alert'}
    >
      <span className="tf-error-signal" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold" style={{ color: tone }}>{title} <span className="font-mono opacity-70">{code}</span></p>
        <p className="truncate text-xs text-tf-text2">{message}</p>
      </div>
      <button className="tf-error-retry" type="button" title="重新整理" aria-label="重新整理" onClick={() => window.location.reload()}>↻</button>
    </div>
  )
}
