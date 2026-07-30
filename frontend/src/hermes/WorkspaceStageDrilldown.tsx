import { HERMES_AMBER, HERMES_CYAN } from '../lib/hermesData'
import { useHermesI18n } from './hermesI18n'
import type { WorkspaceStageDetail } from './workspaceStageDetails'

export default function WorkspaceStageDrilldown({ detail, onClose }: { detail: WorkspaceStageDetail; onClose: () => void }) {
  const { locale, t } = useHermesI18n()
  const available = detail.status === 'available'
  const color = available ? HERMES_CYAN : HERMES_AMBER
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="hermes-workspace-stage-title" className="hermes-clip-lg hermes-stage-drilldown hermes-workspace-stage-drilldown">
      <div className="hermes-workspace-stage-header">
        <div>
          <small>{detail.module.toUpperCase()} · 0{detail.index + 1}</small>
          <strong id="hermes-workspace-stage-title">{detail.label}</strong>
        </div>
        <button type="button" onClick={onClose} aria-label={t('close')}>{t('close')} ✕</button>
      </div>
      <div className="hermes-workspace-stage-body">
        <section style={{ borderLeftColor: color }}>
          <small>{locale === 'zh-TW' ? '這一關在做什麼' : 'WHAT THIS STAGE DOES'}</small>
          <p>{detail.purpose}</p>
        </section>
        {available ? (
          <>
            <div className="hermes-workspace-stage-metric">
              <small>{locale === 'zh-TW' ? '本次可驗證遙測' : 'VERIFIED TELEMETRY'}</small>
              <strong>{detail.metric} <span>{detail.unit}</span></strong>
            </div>
            {detail.facts.length > 0 && <dl>{detail.facts.map((fact) => <div key={`${fact.label}:${fact.value}`}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl>}
          </>
        ) : (
          <div role="status" className="hermes-workspace-stage-missing">
            <strong>{locale === 'zh-TW' ? '尚無可驗證階段資料' : 'NO VERIFIED STAGE DATA'}</strong>
            <p>{detail.missingReason}</p>
          </div>
        )}
      </div>
    </div>
  )
}
