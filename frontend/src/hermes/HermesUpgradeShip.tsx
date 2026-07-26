import { useMemo, useState } from 'react'
import { postHermesUpgradeDecision, type HermesUpgradeData } from '../lib/endpoints'
import { loadSessionToken } from '../lib/adminConsole'
import { useHermesI18n } from './hermesI18n'

interface Props { data: HermesUpgradeData | null; loading: boolean; onClose: () => void; onRefresh: () => void }

export default function HermesUpgradeShip({ data, loading, onClose, onRefresh }: Props) {
  const { t } = useHermesI18n()
  const channelLabel: Record<string, string> = {
    'sandbox-policy': t('shipChannelSandboxPolicy'), 'reviewed-release': t('shipChannelReviewedRelease'),
    'model-gate': t('shipChannelModelGate'), 'core-release': t('shipChannelCoreRelease'),
  }
  const [selected, setSelected] = useState<string | null>(null)
  const [actor, setActor] = useState('')
  const [reason, setReason] = useState('')
  const [gateMessage, setGateMessage] = useState('')
  const [gateBusy, setGateBusy] = useState(false)
  const modules = useMemo(() => data?.modules ?? [], [data?.modules])
  const active = modules.find((module) => module.id === selected) ?? modules[0] ?? null
  const planes = useMemo(() => (data?.planes ?? []).map((plane) => ({
    plane, modules: modules.filter((module) => module.plane === plane),
  })), [data?.planes, modules])
  const candidate = active?.proposals[0]
  const durable = candidate ? data?.automation.durable_queue.proposals.find((row) => row.proposal_id === candidate.id) : null

  async function decide(decision: 'approve' | 'reject') {
    const token = loadSessionToken()
    if (!candidate || !token) { setGateMessage(t('shipGateNeedToken')); return }
    if (!actor.trim() || !reason.trim()) { setGateMessage(t('shipGateNeedActorReason')); return }
    setGateBusy(true)
    const result = await postHermesUpgradeDecision(token, candidate.id, decision, actor.trim(), reason.trim())
    setGateBusy(false)
    setGateMessage(result.ok
      ? t('shipDecisionResultTemplate', { id: candidate.id, action: decision === 'approve' ? t('shipApproveAction') : t('shipRejectAction') })
      : result.error.message)
    if (result.ok) onRefresh()
  }

  return <section className="hermes-upgrade-overlay" role="dialog" aria-modal="true" aria-label={t('shipAriaLabel')}>
    <header className="hermes-upgrade-head">
      <span className="ship-mark">⬡</span><strong>{t('shipTitle')}</strong>
      <small>{t('shipSubtitle')}</small><i />
      <span className="ship-projection">{t('shipOuterModulesTemplate', { count: modules.length || '—' })}</span><button type="button" onClick={onClose}>{t('shipClose')}</button>
    </header>
    <div className="upgrade-control-body">
      {loading && !data ? <div className="ship-loading">{t('shipLoading')}</div> : null}
      <aside className="upgrade-automation-rail">
        <header><span>{t('shipAutonomousLoop')}</span><b>{t('shipDataDrivenTuning')}</b><small>{t('shipCoreNotInScope')}</small></header>
        <div className="automation-stages">{data?.automation.stages.map((stage, index) => <div key={stage.id}><i>{String(index + 1).padStart(2, '0')}</i><span>{stage.id}</span><b className={stage.state}>{stage.state}</b></div>)}</div>
        <section><h3>{t('shipObservedData')}</h3><dl>
          <div><dt>{t('shipJobs')}</dt><dd>{String(data?.automation.measurements.job_count ?? '—')}</dd></div>
          <div><dt>{t('shipFailed')}</dt><dd>{String(data?.automation.measurements.failed_jobs ?? '—')}</dd></div>
          <div><dt>{t('shipRetried')}</dt><dd>{String(data?.automation.measurements.retried_jobs ?? '—')}</dd></div>
          <div><dt>{t('shipSimilarQ')}</dt><dd>{data?.automation.measurements.similar_question_rate == null ? '—' : `${Math.round(Number(data.automation.measurements.similar_question_rate) * 100)}%`}</dd></div>
        </dl></section>
        <section className="llm-review-state"><h3>{t('shipLlmReview')}</h3><b>{data?.automation.llm_review.status ?? 'not_run'}</b><p>{t('shipLlmReviewDesc')}</p></section>
        <section className="durable-upgrade-queue"><h3>{t('shipDurableQueue')}</h3><b>{t('shipProposalCountTemplate', { count: data?.automation.durable_queue.proposal_count ?? 0 })}</b><p>{data?.automation.durable_queue.durable ? t('shipDurableYes') : t('shipDurableNo')}</p></section>
        <section className="historical-source-matrix"><h3>{t('shipHistoricalMatrix')}</h3><p>{t('shipHistoricalDesc')}</p><div>{(data?.automation.historical_sources ?? []).map((source) => <article key={source.source}><span>{source.source}</span><b className={source.status}>{source.status}</b><small>{source.coverage}</small></article>)}</div></section>
      </aside>
      <div className="upgrade-topology">
        {data?.core_package ? <section className="trust-kernel-package">
          <header><span>{t('shipIndependentCore')}</span><b>{data.core_package.name}</b><em>{data.core_package.state}</em></header>
          <div className="kernel-version"><strong>{data.core_package.version}</strong><code>{data.core_package.revision}</code><span>{data.core_package.upgrade_channel}</span></div>
          <div className="kernel-controls">{data.core_package.controls.map((control) => <i key={control}>◆ {control}</i>)}</div>
          <footer><b>{t('shipExternalUpgradeStatusPrefix')}{data.core_package.external_upgrade.status}</b><span>{t('shipExternalUpgradeNote')}</span></footer>
        </section> : null}
        {planes.map(({ plane, modules: rows }) => <section className={`upgrade-plane plane-${plane.toLowerCase().replace(' ', '-')}`} key={plane}>
          <header><b>{plane}</b><span>{t('shipModulesCountTemplate', { count: rows.length })}</span></header>
          <div>{rows.map((module) => <button type="button" key={module.id} onClick={() => setSelected(module.id)} className={`${module.state} ${active?.id === module.id ? 'selected' : ''}`}>
            <i /><span><strong>{module.name}</strong><small>{module.id}</small></span><code className="module-release"><b>{module.version}</b><small>{module.revision_short}</small></code>
          </button>)}</div>
        </section>)}
      </div>
      <aside className="upgrade-inspector">
        {active ? <>
          <div className="upgrade-inspector-title"><span>{active.plane}</span><b>{active.name}</b><em className={active.state}>{active.state}</em></div>
          <dl>
            <div><dt>{t('shipModuleId')}</dt><dd>{active.id}</dd></div><div><dt>{t('shipChannel')}</dt><dd>{channelLabel[active.channel] ?? active.channel}</dd></div>
            <div><dt>{t('shipArtifact')}</dt><dd>{active.family}</dd></div><div><dt>{t('shipReleaseVersion')}</dt><dd><code>{active.version}</code></dd></div>
            <div><dt>{t('shipOrigin')}</dt><dd>{active.origin}</dd></div>
            <div className="wide"><dt>{t('shipRevisionHash')}</dt><dd><code>{active.revision}</code></dd></div>
          </dl>
          <section className="upgrade-path"><h3>{t('shipUpgradeBoundary')}</h3>{active.channel === 'core-release'
            ? <p>{t('shipCoreReleaseDesc')}</p>
            : active.channel === 'model-gate' ? <p>{t('shipModelGateDesc')}</p>
              : <p>{t('shipDefaultUpgradeDesc')}</p>}</section>
          <section className="upgrade-proposals"><h3>{t('shipCurrentProposals')}</h3>{active.proposals.length ? active.proposals.map((proposal) => <article key={proposal.id}><b>{proposal.id}</b><span>{proposal.severity}</span><p>{proposal.proposed_experiment}</p><small>{t('shipSuccessThresholdPrefix')}{proposal.success_metric}</small></article>) : <p>{t('shipNoProposals')}</p>}</section>
          {candidate ? <section className="upgrade-human-gate"><h3>{t('shipHumanGate')}</h3><b>{t('shipSqliteStatePrefix')}{durable?.state ?? 'unknown'}</b><input value={actor} onChange={(e) => setActor(e.target.value)} placeholder={t('shipActorPlaceholder')} /><textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t('shipReasonPlaceholder')} /><div><button disabled={gateBusy || durable?.state !== 'sandbox_passed'} onClick={() => void decide('approve')}>{t('shipApprove')}</button><button disabled={gateBusy || durable?.state === 'approved' || durable?.state === 'rejected'} onClick={() => void decide('reject')}>{t('shipReject')}</button></div><p>{gateMessage || t('shipGateDefaultMessage')}</p></section> : null}
        </> : null}
      </aside>
      <footer className="upgrade-policy-bar"><b>{t('shipPolicyBarTemplate', { outer: data?.coverage.registered ?? 0, candidates: data?.diagnostic.proposal_count ?? 0 })}</b><span>{t('shipPolicyBarFlow')}</span><em>{t('shipTrustKernelPrefix')}{data?.core_package.version ?? '—'} · {t('shipNoRecursiveUpgrade')}</em></footer>
    </div>
  </section>
}
