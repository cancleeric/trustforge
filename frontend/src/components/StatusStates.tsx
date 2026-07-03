export function LoadingState({ label = '載入中…' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center rounded-lg border border-tf-border bg-tf-card p-8 text-sm text-tf-muted">
      {label}
    </div>
  )
}

export function ErrorState({ code, message }: { code: string; message: string }) {
  return (
    <div
      className="rounded-lg border p-4 text-sm"
      style={{ borderColor: '#f85149', backgroundColor: 'color-mix(in srgb, #f85149 8%, transparent)' }}
      role="alert"
    >
      <p className="font-semibold text-tf-bad">請求失敗（{code}）</p>
      <p className="mt-1 text-tf-text2">{message}</p>
    </div>
  )
}
