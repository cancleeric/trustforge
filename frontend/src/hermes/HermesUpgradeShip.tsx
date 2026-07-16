import { useMemo, useState } from 'react'
import type { HermesUpgradeData } from '../lib/endpoints'

interface Props { data: HermesUpgradeData | null; loading: boolean; onClose: () => void }

const channelLabel: Record<string, string> = {
  'sandbox-policy': 'SANDBOX POLICY', 'reviewed-release': 'REVIEWED RELEASE',
  'model-gate': 'MODEL GATE', 'core-release': 'CORE RELEASE',
}

export default function HermesUpgradeShip({ data, loading, onClose }: Props) {
  const [selected, setSelected] = useState<string | null>(null)
  const modules = data?.modules ?? []
  const active = modules.find((module) => module.id === selected) ?? modules[0] ?? null
  const planes = useMemo(() => (data?.planes ?? []).map((plane) => ({
    plane, modules: modules.filter((module) => module.plane === plane),
  })), [data?.planes, modules])

  return <section className="hermes-upgrade-overlay" role="dialog" aria-modal="true" aria-label="Hermes 升級控制面">
    <header className="hermes-upgrade-head">
      <span className="ship-mark">⬡</span><strong>HERMES UPGRADE CONTROL</strong>
      <small>模塊拓撲 · 真實版本 · sandbox 候選 · release gate</small><i />
      <span className="ship-projection">{modules.length || '—'} MODULES</span><button type="button" onClick={onClose}>關閉 ×</button>
    </header>
    <div className="upgrade-control-body">
      {loading && !data ? <div className="ship-loading">讀取版本與提案…</div> : null}
      <div className="upgrade-topology">
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
        </> : null}
      </aside>
      <footer className="upgrade-policy-bar"><b>{data?.diagnostic.proposal_count ?? 0} CANDIDATES</b><span>diagnose → sandbox → validation → human approval → active pointer → rollback</span><em>禁止遞回升級 · 禁止自動部署</em></footer>
    </div>
  </section>
}
