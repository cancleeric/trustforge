export function LoadingState({ label = '載入中…' }: { label?: string }) {
  return (
    <div className="tf-loading-state flex items-center justify-center rounded-lg border border-tf-border bg-tf-card p-8 text-sm text-tf-muted" role="status">
      <span className="tf-loading-scan" aria-hidden="true" />{label}
    </div>
  )
}

export function ErrorState({ code, message }: { code: string; message: string }) {
  const title = code === 'network_error' ? '連線異常'
    : code === 'timeout' ? '回應逾時'
      : code === 'parse_error' ? '資料格式異常'
        : '服務異常'
  return (
    <div
      className="tf-error-state rounded-lg border p-4 text-sm"
      style={{
        borderColor: 'var(--color-tf-bad)',
        backgroundColor: 'color-mix(in srgb, var(--color-tf-bad) 8%, transparent)',
      }}
      role="alert"
    >
      <span className="tf-error-signal" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold text-tf-bad">{title} <span className="font-mono opacity-70">{code}</span></p>
        <p className="truncate text-xs text-tf-text2">{message}</p>
      </div>
      <button className="tf-error-retry" type="button" title="重新整理" aria-label="重新整理" onClick={() => window.location.reload()}>↻</button>
    </div>
  )
}
