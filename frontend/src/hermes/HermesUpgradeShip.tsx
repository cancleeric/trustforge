import type { HermesUpgradeData, HermesUpgradeModule } from '../lib/endpoints'

interface Props { data: HermesUpgradeData | null; loading: boolean; onClose: () => void }

const positions: Record<string, string> = {
  scan: 'ship-module ship-module-left ship-module-1', filter: 'ship-module ship-module-left ship-module-2',
  core: 'ship-module ship-module-left ship-module-3', verify: 'ship-module ship-module-right ship-module-1',
  detect: 'ship-module ship-module-right ship-module-2', engine: 'ship-module ship-module-right ship-module-3',
}

function ModuleCard({ module }: { module: HermesUpgradeModule }) {
  const candidate = module.state === 'candidate'
  return <article className={`${positions[module.id]} ${candidate ? 'is-candidate' : ''}`}>
    <header><strong>{module.name}</strong><b>{module.state === 'locked' ? 'CORE' : module.state === 'candidate' ? '候選' : 'ACTIVE'}</b></header>
    <small>{module.slot}</small>
    <div className="ship-module-version"><code>{module.family}</code><span>REV {module.version}</span></div>
    <div className="ship-module-pips">{[0, 1, 2, 3, 4].map((n) => <i key={n} className={n < (candidate ? 4 : 3) ? 'on' : ''} />)}</div>
    {module.proposals[0]
      ? <p>⬆ {module.proposals[0].proposed_experiment}</p>
      : <p>{module.state === 'locked' ? '打包、版控；僅能經審查版次升級' : '目前版本已啟用，保留回退指標'}</p>}
  </article>
}

export default function HermesUpgradeShip({ data, loading, onClose }: Props) {
  return <section className="hermes-upgrade-overlay" role="dialog" aria-modal="true" aria-label="Hermes 艦體升級控制面">
    <header className="hermes-upgrade-head">
      <span className="ship-mark">⬡</span><strong>艦體狀態 · HERMES 旗艦</strong>
      <small>6 個可視模塊 · 版本、候選、核准與回退</small><i />
      <span className="ship-projection">全息投影中</span><button type="button" onClick={onClose}>關閉 ×</button>
    </header>
    <div className="hermes-upgrade-body">
      {loading && !data ? <div className="ship-loading">讀取版本與提案…</div> : null}
      {data?.modules.map((module) => <ModuleCard key={module.id} module={module} />)}
      <div className="ship-wireframe" aria-hidden="true">
        <svg viewBox="0 0 480 200"><path d="M8 108 70 84 150 76 330 76 415 82 462 96 462 124 420 138 110 138 30 122Z" /><path d="M8 108H462M200 76 208 44H256L266 76M292 76 298 60H324L330 76M180 138 200 158H260L280 138" /></svg>
        <i />
      </div>
      <div className="ship-platform" aria-hidden="true" />
      <footer>
        <b>{data?.diagnostic.proposal_count ?? 0}</b> 個 sandbox 候選
        <span>診斷 → sandbox → 驗證 → 人工核准 → 指標切換 → 可回退</span>
        <em>禁止遞回升級 · 禁止自動部署 · Trust 核心不接受外層覆寫</em>
      </footer>
    </div>
  </section>
}
