import type { AnalyzeData } from '../lib/types'
import { deriveResultReadiness, type ResultReadiness } from '../lib/resultReadiness'
import { useHermesI18n } from '../hermes/hermesI18n'

export default function PlainLanguageResultSummary({ data }: { data: AnalyzeData }) {
  const { t } = useHermesI18n()
  const readinessCopy: Record<ResultReadiness, { label: string; description: string; tone: string }> = {
    ready: { label: t('plsReadyLabel'), description: t('plsReadyDesc'), tone: 'var(--color-tf-good)' },
    limited: { label: t('plsLimitedLabel'), description: t('plsLimitedDesc'), tone: 'var(--color-tf-warn)' },
    insufficient: { label: t('plsInsufficientLabel'), description: t('plsInsufficientDesc'), tone: 'var(--color-tf-bad)' },
  }
  const readiness = deriveResultReadiness(data)
  const copy = readinessCopy[readiness]
  const reasons = data.report.key_basis.slice(0, 3)

  return (
    <section className="hermes-result-summary" aria-labelledby="result-summary-title">
      <div className="hermes-result-summary-heading">
        <div>
          <p>{t('plsEyebrow')}</p>
          <h2 id="result-summary-title">{data.report.market_judgment}</h2>
        </div>
        <div className="hermes-readiness" style={{ borderColor: copy.tone }}>
          <i style={{ background: copy.tone }} />
          <span><b style={{ color: copy.tone }}>{copy.label}</b><small>{copy.description}</small></span>
        </div>
      </div>

      <div className="hermes-result-metrics">
        <div><span>{t('plsInfoCompleteness')}</span><b>{Math.round(data.report.calibrated_confidence * 100)}%</b></div>
        <div><span>{t('plsEvidenceCount')}</span><b>{t('plsEvidenceUnitTemplate', { count: data.evidence.length })}</b></div>
        <div><span>{t('plsKnownLimits')}</span><b>{t('plsLimitsUnitTemplate', { count: data.report.limits.length })}</b></div>
      </div>

      <div className="hermes-summary-reasons">
        <h3>{t('plsThreeReasons')}</h3>
        {reasons.length ? (
          <ol>{reasons.map((reason, index) => <li key={`${reason.claim}-${index}`}><b>{index + 1}</b><span>{reason.claim}</span></li>)}</ol>
        ) : <p>{t('plsNoReasons')}</p>}
      </div>

      <nav className="hermes-result-next" aria-label={t('plsNextStepsAria')}>
        <span>{t('plsNextStepsLabel')}</span>
        <a href="#key-basis">{t('plsViewKeyBasis')}</a>
        {data.report.limits.length > 0 && <a href="#known-limits">{t('plsUnderstandLimits')}</a>}
        <a href="#evidence-list">{t('plsCheckEvidence')}</a>
        <a href="#technical-analysis">{t('plsViewFullAnalysis')}</a>
      </nav>
      <p className="hermes-result-disclaimer">{t('plsDisclaimer')}</p>
    </section>
  )
}
