import { useEffect, useMemo, useState } from 'react'
import AnalyzePage from '../pages/AnalyzePage'
import ComparePage from '../pages/ComparePage'
import HistoryPage from '../pages/HistoryPage'
import StatusPage from '../pages/StatusPage'
import CostsPage from '../pages/CostsPage'
import { BridgeHologramProvider, type BridgeHologramData } from '../components/BridgeHologramContext'
import { useHermesI18n } from './hermesI18n'

export type HermesWorkspaceModule = 'analyze' | 'compare' | 'history' | 'status' | 'costs'

const MODULES = {
  analyze: AnalyzePage,
  compare: ComparePage,
  history: HistoryPage,
  status: StatusPage,
  costs: CostsPage,
}

export default function HermesModuleDeck({
  module, onClose, onTelemetry, onBusyChange,
}: {
  module: HermesWorkspaceModule
  onClose: () => void
  onTelemetry: (data: BridgeHologramData | null) => void
  /** Only meaningful for the `analyze` module — forwards its internal
   * loading state so the host can reflect it even on error outcomes,
   * where `onTelemetry` never fires (see AnalyzePage's N2 fix). */
  onBusyChange?: (busy: boolean) => void
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
          <span className="module-holo-core">{module === 'costs' ? `$${(data?.primaryValue ?? 0).toFixed(4)}` : data?.primaryValue != null ? `${Math.round(data.primaryValue * 100)}%` : module.slice(0, 3).toUpperCase()}</span>
          <span className="module-holo-caption">{data?.primaryLabel || module.toUpperCase()} · {data?.total ?? 0} SIGNALS</span>
        </div>
        <div className="hermes-module-deck-scroll">
          {module === 'analyze' ? <AnalyzePage embedded onBusyChange={onBusyChange} /> : <Module />}
        </div>
      </section>
    </BridgeHologramProvider>
  )
}
