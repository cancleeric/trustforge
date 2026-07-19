import { useMemo, useState, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import Header from './Header'
import { useHermesI18n } from '../hermes/hermesI18n'
import { BridgeHologramProvider, type BridgeHologramData } from './BridgeHologramContext'

const WORKSPACES: Record<string, { code: string; zh: string; en: string; channel: string }> = {
  '/analyze': { code: 'ANL-01', zh: '分析作戰台', en: 'ANALYSIS STATION', channel: 'EVIDENCE' },
  '/compare': { code: 'CMP-02', zh: '平行比較台', en: 'PARALLEL COMPARE', channel: 'CROSS-VERIFY' },
  '/history': { code: 'HST-03', zh: '歷史回放台', en: 'HISTORY REPLAY', channel: 'TIMELINE' },
  '/status': { code: 'SRC-04', zh: '來源監控台', en: 'SOURCE CONTROL', channel: 'UPLINK' },
  '/costs': { code: 'CST-05', zh: '成本帳本台', en: 'COST LEDGER', channel: 'LEDGER' },
  '/admin': { code: 'ADM-09', zh: '管理控制台', en: 'ADMIN CONTROL', channel: 'RESTRICTED' },
  '/settings': { code: 'SET-08', zh: '系統設定', en: 'CONTROL PANEL', channel: 'CONFIG' },
  '/help': { code: 'HLP-10', zh: '說明中心', en: 'HELP CENTER', channel: 'HELP CENTER' },
  '/notifications': { code: 'NTF-11', zh: '通知中心', en: 'NOTIFICATION CENTER', channel: 'NOTIFICATION' },
}

const ENERGY_STAGES = ['SOURCE', 'FILTER', 'VERIFY', 'TRUST', 'REPORT']

function HologramDisplay({ pathname, search, data }: { pathname: string; search: string; data: BridgeHologramData | null }) {
  const params = new URLSearchParams(search)
  const primary = data?.primaryLabel || params.get('coin') || 'BTC'
  const secondary = data?.secondaryLabel || params.get('coin2') || 'ETH'
  const mode = pathname.slice(1) || 'analyze'
  const sceneState = data ? 'LIVE DATA' : 'SNAPSHOT MODE'

  return (
    <div className={`bridge-holo-display bridge-holo-${mode}`} aria-hidden="true">
      <span className="bridge-holo-scene-state"><i />{sceneState}</span>
      <span className="bridge-holo-beam" />
      <span className="bridge-holo-platform" />
      {pathname === '/compare' ? (
        <>
          <span className="bridge-holo-link" />
          <span className="bridge-holo-entity bridge-holo-entity-a"><i /> <b>{primary}<em>{data?.primaryValue != null ? `${Math.round(data.primaryValue * 100)}%` : ''}</em></b></span>
          <span className="bridge-holo-entity bridge-holo-entity-b"><i /> <b>{secondary}<em>{data?.secondaryValue != null ? `${Math.round(data.secondaryValue * 100)}%` : ''}</em></b></span>
          <span className="bridge-holo-readout">PARALLEL TRUST VECTOR</span>
        </>
      ) : pathname === '/history' ? (
        <>
          <span className="bridge-holo-timeline" />
          {[0, 1, 2, 3, 4].map((node) => <i className={`bridge-history-node bridge-history-node-${node}`} key={node} />)}
          <span className="bridge-holo-readout">{primary} · {data?.total ?? 0} SNAPSHOTS · REPLAY TRAJECTORY</span>
        </>
      ) : pathname === '/status' ? (
        <>
          <span className="bridge-source-core" />
          {[0, 1, 2, 3, 4, 5].map((node) => <i className={`bridge-source-node bridge-source-node-${node}`} key={node} />)}
          <span className="bridge-holo-readout">{data?.status || 'SOURCE UPLINK MESH'} · {data?.total ?? 0} CHANNELS</span>
        </>
      ) : pathname === '/costs' ? (
        <>
          <span className="bridge-ledger-ring bridge-ledger-ring-a" />
          <span className="bridge-ledger-ring bridge-ledger-ring-b" />
          <span className="bridge-ledger-value">${(data?.primaryValue ?? 0).toFixed(4)}</span>
          <span className="bridge-holo-readout">{data?.total ?? 0} RUNS · TOKEN · MODEL LEDGER</span>
        </>
      ) : (
        <>
          <span className="bridge-holo-orbit bridge-holo-orbit-a" />
          <span className="bridge-holo-orbit bridge-holo-orbit-b" />
          <span className="bridge-holo-core" />
          <span className="bridge-evidence-node bridge-evidence-node-a" />
          <span className="bridge-evidence-node bridge-evidence-node-b" />
          <span className="bridge-holo-readout">{primary} · {data?.primaryValue != null ? `${Math.round(data.primaryValue * 100)}% · ` : ''}{data?.total ?? 0} EVIDENCE</span>
        </>
      )}
      <span className="bridge-holo-depth-mark bridge-holo-depth-mark-a">Z-01</span>
      <span className="bridge-holo-depth-mark bridge-holo-depth-mark-b">Z-03</span>
    </div>
  )
}

export default function BridgeWorkspaceShell({ children }: { children: ReactNode }) {
  const { pathname, search } = useLocation()
  const { locale } = useHermesI18n()
  const [hologramData, setHologramData] = useState<BridgeHologramData | null>(null)
  const hologramValue = useMemo(() => ({ data: hologramData, setData: setHologramData }), [hologramData])
  const meta = WORKSPACES[pathname] ?? { code: 'SYS-00', zh: '艦橋工作區', en: 'BRIDGE WORKSPACE', channel: 'HERMES' }

  return (
    <BridgeHologramProvider value={hologramValue}>
    <div className="bridge-workspace">
      <Header />
      <div className="bridge-workspace-grid">
        <aside className="bridge-side-rail bridge-side-rail-left" aria-label="艦橋系統狀態">
          <span>SYSTEM BUS</span><strong>{meta.code}</strong><i /><span>HERMES CORE</span><b className="bridge-status-dot">ONLINE</b>
        </aside>
        <section className="bridge-hologram-bay" aria-label={locale === 'zh-TW' ? meta.zh : meta.en}>
          <HologramDisplay pathname={pathname} search={search} data={hologramData} />
          <div className="bridge-module-heading"><span>{meta.channel} CHANNEL</span><strong>{locale === 'zh-TW' ? meta.zh : meta.en}</strong></div>
          <div className="bridge-display-mode" aria-hidden="true"><span>HOLOGRAPHIC DISPLAY</span><b>{hologramData ? 'TELEMETRY LOCKED' : 'AWAITING DATA'}</b></div>
          <div className="bridge-route-viewport">{children}</div>
        </section>
        <aside className="bridge-side-rail bridge-side-rail-right" aria-label="艦橋遙測">
          <span>TELEMETRY</span><strong>LIVE</strong><i /><span>DATA INTEGRITY</span><b>MONITORED</b>
        </aside>
      </div>
      <footer className="bridge-engine-deck" aria-label="Hermes 工作流能量匯流">
        <div className="bridge-energy-line" aria-hidden="true" />
        {ENERGY_STAGES.map((stage, index) => <div className="bridge-energy-node" key={stage}><span>{String(index + 1).padStart(2, '0')}</span><b>{stage}</b></div>)}
        <div className="bridge-engine-core"><span /><b>HERMES ENGINE</b></div>
      </footer>
    </div>
    </BridgeHologramProvider>
  )
}
