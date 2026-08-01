import { useEffect, useRef, type CSSProperties } from 'react'
import { modeLabel, useHermesI18n } from '../hermes/hermesI18n'

export function FormalRunConfirmDialog({ coin, mode, question, onConfirm, onCancel }: { coin: string; mode: string; question: string; onConfirm: () => void; onCancel: () => void }) {
  const { t } = useHermesI18n()
  const dialogRef = useRef<HTMLDivElement>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)
  const confirming = useRef(false)

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null
    confirmRef.current?.focus()
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
      if (e.key !== 'Tab') return
      const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') ?? [])
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      previouslyFocused?.focus()
    }
  }, [onCancel])

  const handleConfirm = () => {
    if (confirming.current) return
    confirming.current = true
    onConfirm()
  }

  return (
    <div role="dialog" aria-modal="true" aria-label={t('fcAriaLabel')} className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-5" onClick={onCancel}>
      <div ref={dialogRef} className="hermes-clip max-h-[calc(100dvh-2.5rem)] w-full max-w-[480px] overflow-y-auto rounded-[14px] border border-tf-accent/30 p-5 shadow-2xl sm:p-6" style={{ background: 'var(--color-tf-panel)' }} onClick={(e) => e.stopPropagation()}>
        <p className="text-[10.5px] font-semibold uppercase tracking-[0.22em] text-tf-link">{t('fcTitle')}</p>
        <dl className="mt-4 grid gap-2 text-sm">
          <div className="flex gap-2">
            <dt className="text-tf-muted">{t('fcAssetLabel')}</dt>
            <dd className="text-tf-text">{coin}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-tf-muted">{t('fcIntentLabel')}</dt>
            <dd className="text-tf-text">{modeLabel(mode, t)}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-tf-muted">{t('fcQuestionLabel')}</dt>
            <dd className="break-words text-tf-text">{question}</dd>
          </div>
        </dl>
        <p className="mt-4 rounded-md border border-tf-warn/40 bg-tf-warn/10 p-3 text-xs text-tf-text2">{t('fcWarning')}</p>
        <div className="mt-5 flex flex-wrap justify-end gap-3">
          <button type="button" onClick={onCancel} className="rounded-[8px] border border-tf-border px-4 py-2 text-xs font-semibold text-tf-text2 hover:text-tf-text">{t('fcCancel')}</button>
          <button type="button" ref={confirmRef} onClick={handleConfirm} className="rounded-[8px] px-5 py-2 text-xs font-bold tracking-wide text-tf-bg hover:opacity-90" style={{ background: 'linear-gradient(135deg,var(--color-tf-accent),#3bc0c8)' }}>{t('fcConfirm')}</button>
        </div>
      </div>
    </div>
  )
}

export function ReconnectingBanner() {
  const { t } = useHermesI18n()
  const style = {
    '--tf-state-color': 'var(--color-tf-link)',
    borderColor: 'var(--color-tf-link)',
    backgroundColor: 'color-mix(in srgb, var(--color-tf-link) 8%, transparent)',
  } as CSSProperties
  return (
    <div role="status" className="tf-reconnecting-state flex items-center gap-3 rounded-lg border p-4 text-sm" style={style}>
      <span className="tf-loading-scan" aria-hidden="true" />
      <div>
        <p className="font-semibold" style={{ color: 'var(--color-tf-link)' }}>{t('reconnectingBanner')}</p>
        <p className="mt-1 text-xs text-tf-text2">{t('reconnectingBannerHint')}</p>
      </div>
    </div>
  )
}

export function PartialResultBanner() {
  const { t } = useHermesI18n()
  const style = {
    '--tf-state-color': 'var(--color-tf-warn)',
    borderColor: 'var(--color-tf-warn)',
    backgroundColor: 'color-mix(in srgb, var(--color-tf-warn) 8%, transparent)',
  } as CSSProperties
  return (
    <div role="status" className="tf-partial-state flex items-start gap-3 rounded-lg border p-4 text-sm" style={style}>
      <span aria-hidden="true">⚠</span>
      <div>
        <p className="font-semibold" style={{ color: 'var(--color-tf-warn)' }}>{t('partialBanner')}</p>
        <p className="mt-1 text-xs text-tf-text2">{t('partialBannerHint')}</p>
      </div>
    </div>
  )
}
