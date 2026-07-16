import { useMemo, useState } from 'react'
import { postHermesUpgradeDecision, type HermesUpgradeData } from '../lib/endpoints'
import { loadSessionToken } from '../lib/adminConsole'

interface Props { data: HermesUpgradeData | null; loading: boolean; onClose: () => void; onRefresh: () => void }

const channelLabel: Record<string, string> = {
  'sandbox-policy': 'SANDBOX POLICY', 'reviewed-release': 'REVIEWED RELEASE',
  'model-gate': 'MODEL GATE', 'core-release': 'CORE RELEASE',
}

export default function HermesUpgradeShip({ data, loading, onClose, onRefresh }: Props) {
  const [selected, setSelected] = useState<string | null>(null)
  const [actor, setActor] = useState('')
  const [reason, setReason] = useState('')
  const [gateMessage, setGateMessage] = useState('')
  const [gateBusy, setGateBusy] = useState(false)
  const modules = data?.modules ?? []
  const active = modules.find((module) => module.id === selected) ?? modules[0] ?? null
  const planes = useMemo(() => (data?.planes ?? []).map((plane) => ({
    plane, modules: modules.filter((module) => module.plane === plane),
  })), [data?.planes, modules])
  const candidate = active?.proposals[0]
  const durable = candidate ? data?.automation.durable_queue.proposals.find((row) => row.proposal_id === candidate.id) : null

  async function decide(decision: 'approve' | 'reject') {
    const token = loadSessionToken()
    if (!candidate || !token) { setGateMessage('請先到管理頁解鎖 Admin Token。'); return }
    if (!actor.trim() || !reason.trim()) { setGateMessage('操作者與理由皆為必填。'); return }
    setGateBusy(true)
    const result = await postHermesUpgradeDecision(token, candidate.id, decision, actor.trim(), reason.trim())
    setGateBusy(false)
    setGateMessage(result.ok ? `${candidate.id} 已${decision === 'approve' ? '核准' : '拒絕'}；尚未自動啟用。` : result.error.message)
    if (result.ok) onRefresh()
  }

  return <section className="hermes-upgrade-overlay" role="dialog" aria-modal="true" aria-label="Hermes 升級控制面">
    <header className="hermes-upgrade-head">
      <span className="ship-mark">⬡</span><strong>HERMES UPGRADE CONTROL</strong>
      <small>模塊拓撲 · 真實版本 · sandbox 候選 · release gate</small><i />
      <span className="ship-projection">CORE + {modules.length || '—'} OUTER MODULES</span><button type="button" onClick={onClose}>關閉 ×</button>
    </header>
    <div className="upgrade-control-body">
      {loading && !data ? <div className="ship-loading">讀取版本與提案…</div> : null}
      <aside className="upgrade-automation-rail">
        <header><span>AUTONOMOUS OUTER LOOP</span><b>資料驅動調校</b><small>Trust 核心不在自動調校範圍</small></header>
        <div className="automation-stages">{data?.automation.stages.map((stage, index) => <div key={stage.id}><i>{String(index + 1).padStart(2, '0')}</i><span>{stage.id}</span><b className={stage.state}>{stage.state}</b></div>)}</div>
        <section><h3>觀測資料</h3><dl>
          <div><dt>JOBS</dt><dd>{String(data?.automation.measurements.job_count ?? '—')}</dd></div>
          <div><dt>FAILED</dt><dd>{String(data?.automation.measurements.failed_jobs ?? '—')}</dd></div>
          <div><dt>RETRIED</dt><dd>{String(data?.automation.measurements.retried_jobs ?? '—')}</dd></div>
          <div><dt>SIMILAR Q</dt><dd>{data?.automation.measurements.similar_question_rate == null ? '—' : `${Math.round(Number(data.automation.measurements.similar_question_rate) * 100)}%`}</dd></div>
        </dl></section>
        <section className="llm-review-state"><h3>LLM 對抗審查</h3><b>{data?.automation.llm_review.status ?? 'not_run'}</b><p>比對量測證據、候選差異、資料洩漏、回歸與回退缺口。LLM 無核准權。</p></section>
        <section className="durable-upgrade-queue"><h3>SQLITE 候選佇列</h3><b>{data?.automation.durable_queue.proposal_count ?? 0} proposals</b><p>{data?.automation.durable_queue.durable ? '重啟後保留 proposal、LLM verdict 與 gate 狀態' : '佇列目前不可用'}</p></section>
      </aside>
      <div className="upgrade-topology">
        {data?.core_package ? <section className="trust-kernel-package">
          <header><span>INDEPENDENT VERSIONED CORE</span><b>{data.core_package.name}</b><em>{data.core_package.state}</em></header>
          <div className="kernel-version"><strong>{data.core_package.version}</strong><code>{data.core_package.revision}</code><span>{data.core_package.upgrade_channel}</span></div>
          <div className="kernel-controls">{data.core_package.controls.map((control) => <i key={control}>◆ {control}</i>)}</div>
          <footer><b>外接升級介面：{data.core_package.external_upgrade.status}</b><span>尚未連接 adapter；未來候選也必須經核心 release gate</span></footer>
        </section> : null}
        {planes.map(({ plane, modules: rows }) => <section className={`upgrade-plane plane-${plane.toLowerCase().replace(' ', '-')}`} key={plane}>
          <header><b>{plane}</b><span>{rows.length} modules</span></header>
          <div>{rows.map((module) => <button type="button" key={module.id} onClick={() => setSelected(module.id)} className={`${module.state} ${active?.id === module.id ? 'selected' : ''}`}>
            <i /><span><strong>{module.name}</strong><small>{module.id}</small></span><code>{module.version}</code>
          </button>)}</div>
        </section>)}
      </div>
      <aside className="upgrade-inspector">
        {active ? <>
          <div className="upgrade-inspector-title"><span>{active.plane}</span><b>{active.name}</b><em className={active.state}>{active.state}</em></div>
          <dl>
            <div><dt>MODULE ID</dt><dd>{active.id}</dd></div><div><dt>CHANNEL</dt><dd>{channelLabel[active.channel] ?? active.channel}</dd></div>
            <div><dt>ARTIFACT</dt><dd>{active.family}</dd></div><div><dt>ORIGIN</dt><dd>{active.origin}</dd></div>
            <div className="wide"><dt>REVISION / CONTENT HASH</dt><dd><code>{active.revision}</code></dd></div>
          </dl>
          <section className="upgrade-path"><h3>升級邊界</h3>{active.channel === 'core-release'
            ? <p>核心可升級，但只能經 branch、完整測試、PR 審查與 release；執行中的 snapshot 永不改寫。</p>
            : active.channel === 'model-gate' ? <p>候選模型必須通過時間切分 holdout、校準改善與 ModelHub 核准，才能切換 active pointer。</p>
              : <p>診斷只建立候選；sandbox 回放與驗收成功後，由人員核准版本指標，並保留上一版回退。</p>}</section>
          <section className="upgrade-proposals"><h3>目前候選</h3>{active.proposals.length ? active.proposals.map((proposal) => <article key={proposal.id}><b>{proposal.id}</b><span>{proposal.severity}</span><p>{proposal.proposed_experiment}</p><small>成功門檻：{proposal.success_metric}</small></article>) : <p>沒有待核准候選；目前版本持續運行。</p>}</section>
          {candidate ? <section className="upgrade-human-gate"><h3>人工 RELEASE GATE</h3><b>SQLITE STATE · {durable?.state ?? 'unknown'}</b><input value={actor} onChange={(e) => setActor(e.target.value)} placeholder="操作者／審查人" /><textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="核准或拒絕理由" /><div><button disabled={gateBusy || durable?.state !== 'sandbox_passed'} onClick={() => void decide('approve')}>核准候選</button><button disabled={gateBusy || durable?.state === 'approved' || durable?.state === 'rejected'} onClick={() => void decide('reject')}>拒絕候選</button></div><p>{gateMessage || '只有 sandbox_passed 可核准；核准也不會自動部署。'}</p></section> : null}
        </> : null}
      </aside>
      <footer className="upgrade-policy-bar"><b>{data?.coverage.registered ?? 0} OUTER / {data?.diagnostic.proposal_count ?? 0} CANDIDATES</b><span>diagnose → sandbox → validation → human approval → active pointer → rollback</span><em>TRUST KERNEL {data?.core_package.version ?? '—'} · 禁止遞回升級</em></footer>
    </div>
  </section>
}
