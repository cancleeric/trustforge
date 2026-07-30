import { useEffect, useMemo, useState } from 'react'
import AnalyzePage from '../pages/AnalyzePage'
import ComparePage from '../pages/ComparePage'
import HistoryPage from '../pages/HistoryPage'
import StatusPage from '../pages/StatusPage'
import CostsPage from '../pages/CostsPage'
import WhaleHistoryPanel from '../components/WhaleHistoryPanel'
import { BridgeHologramProvider, type BridgeHologramData } from '../components/BridgeHologramContext'
import { useHermesI18n } from './hermesI18n'

export type HermesWorkspaceModule = 'analyze' | 'compare' | 'history' | 'status' | 'costs' | 'whale'

const MODULES = {
  analyze: AnalyzePage,
  compare: ComparePage,
  history: HistoryPage,
  status: StatusPage,
  costs: CostsPage,
  whale: WhaleHistoryPanel,
}

export default function HermesModuleDeck({
  module, onClose, onTelemetry, onBusyChange, resubmitSignal,
}: {
  module: HermesWorkspaceModule
  onClose: () => void
  onTelemetry: (data: BridgeHologramData | null) => void
  /** Only meaningful for the `analyze` module — forwards its internal
   * loading state so the host can reflect it even on error outcomes,
   * where `onTelemetry` never fires (see AnalyzePage's N2 fix). */
  onBusyChange?: (busy: boolean) => void
  /** Only meaningful for the `analyze` module — a counter the host bumps on
   * every explicit "立即重新分析" click so AnalyzePage can force a real
   * resubmit even when the question text (and therefore the URL) didn't
   * change (see AnalyzePage's N13 fix). */
  resubmitSignal?: number
}) {
  const { t } = useHermesI18n()
  const [data, setData] = useState<BridgeHologramData | null>(null)
  const value = useMemo(() => ({ data, setData }), [data])
  const Module = MODULES[module]

  useEffect(() => {
    onTelemetry(data)
    return () => onTelemetry(null)
  }, [data, onTelemetry])

  return (
    <BridgeHologramProvider value={value}>
      <section className="hermes-module-deck" aria-label={`${module} workspace`}>
        <header className="hermes-module-deck-header">
          <span><i /> HERMES WORKSPACE</span>
          <b>{module.toUpperCase()} · {data ? 'TELEMETRY LOCKED' : 'SNAPSHOT ACTIVE'}</b>
          <button type="button" onClick={onClose} aria-label={t('close')} title={t('close')}>×</button>
        </header>
        <div className={`hermes-module-hologram hermes-module-hologram-${module}`} aria-hidden="true">
          <span className="module-holo-beam" />
          <span className="module-holo-ring module-holo-ring-a" />
          <span className="module-holo-ring module-holo-ring-b" />
          <span className="module-holo-core">{module === 'costs'
            ? `$${(data?.primaryValue ?? 0).toFixed(4)}`
            : module === 'whale' && data?.primaryValue != null
              ? formatCompactUsd(data.primaryValue)
              : data?.primaryValue != null
                ? `${Math.round(data.primaryValue * 100)}%`
                : module.slice(0, 3).toUpperCase()}</span>
          <span className="module-holo-caption">{data?.primaryLabel || module.toUpperCase()} · {data?.total ?? 0} SIGNALS</span>
        </div>
        <div className="hermes-module-deck-scroll">
          {module === 'analyze' ? <AnalyzePage embedded onBusyChange={onBusyChange} resubmitSignal={resubmitSignal} /> : <Module />}
        </div>
      </section>
    </BridgeHologramProvider>
  )
}

function formatCompactUsd(value: number): string {
  const absolute = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (absolute >= 1_000_000_000) return `${sign}$${(absolute / 1_000_000_000).toFixed(1)}B`
  if (absolute >= 1_000_000) return `${sign}$${(absolute / 1_000_000).toFixed(1)}M`
  if (absolute >= 1_000) return `${sign}$${(absolute / 1_000).toFixed(0)}K`
  return `${sign}$${absolute.toFixed(0)}`
}
