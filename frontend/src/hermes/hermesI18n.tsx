// oxlint-disable react/only-export-components
import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'

export type HermesLocale = 'zh-TW' | 'en'

const messages = {
  'zh-TW': {
    analyze: '分析', compare: '比較', history: '歷史趨勢', sources: '來源狀態', costs: '成本', galaxy: '市場星系',
    liveUplink: '即時連線', active: '運作中', costLedger: '成本帳本', syncing: '同步中', navigation: 'Hermes 工作區導覽', language: '切換語言',
    telemetry: '市場遙測', tracked: '追蹤資產', healthy: '健康', moderate: '注意', danger: '警示', latency: '連線延遲', online: '在線',
    transmitted: '指令已送出', analysisMode: '分析模式', order: '交付 Hermes 的任務', transmitting: '傳送中…', transmit: '交付 Hermes 執行',
    risk: '風險評估', sentiment: '市場情緒', fundamentals: '基本面', news: '新聞驗證', catalyst: '價格催化因子',
    defaultQuery: '評估整體信任狀態，並標記任何正在形成的操縱風險。', initializing: '正在初始化 Hermes 工作台…',
    focused: '目前焦點', trustScore: '信任分數', trustBreakdown: '信任拆解', overviewProxy: '總覽代理值', proxyTitle: '由總覽分數推導；執行正式分析後才會產生證據綁定組件',
    fullBreakdown: '查看完整拆解與推理', divergence: '跨來源分歧', alignment: '來源一致性正常', monitor: '持續監控分歧', conflict: '偵測到來源衝突', tapReview: '點擊查看',
    highTrust: '高信任', moderateTrust: '中度信任', lowTrust: '低信任', economicWeight: '市場權重', refocus: '再次點擊可重新聚焦', focusHint: '點選幣種，讓 Hermes 聚焦該市場', focus: '聚焦',
    stage: '節點', scan: '來源掃描', filter: '可信過濾', crossverify: '交叉驗證', manipulation: '操縱偵測', composite: '綜合信任分數',
    scanned: '已掃描', passed: '已通過', divergenceUnit: '分歧', flagged: '已標記', close: '關閉', proxyTrace: '總覽代理值 · 執行正式分析以取得證據綁定軌跡',
    dropped: '已標記／排除', flaggedChannel: '異常通道', reasoningTrace: '推理軌跡', weight: '評分係數',
    reputation: '來源信譽', corroboration: '交叉佐證', recency: '資料時效', resistance: '抗操縱能力', facts: '事實', inference: '推論', conclusion: '結論',
    liveTelemetry: '即時遙測', sourceCount: '可用來源', integrity: '完整性', signalState: '訊號狀態', stable: '穩定', watch: '監控', degradedState: '下降',
    agentOutput: 'Hermes 主動報告',
    degraded: '連線品質下降，正在顯示可用快照',
    help: '說明', settings: '設定', notifications: '通知',
  },
  en: {
    analyze: 'ANALYZE', compare: 'COMPARE', history: 'HISTORY', sources: 'SOURCES', costs: 'COSTS', galaxy: 'GALAXY',
    liveUplink: 'LIVE UPLINK', active: 'ACTIVE', costLedger: 'COST LEDGER', syncing: 'SYNCING', navigation: 'Hermes workspace navigation', language: 'Switch language',
    telemetry: 'GALAXY TELEMETRY', tracked: 'Assets tracked', healthy: 'Healthy', moderate: 'Moderate', danger: 'Danger', latency: 'Uplink latency', online: 'ONLINE',
    transmitted: 'ORDER TRANSMITTED', analysisMode: 'ANALYSIS MODE', order: 'ORDER TO HERMES', transmitting: 'TRANSMITTING…', transmit: 'TRANSMIT TO HERMES',
    risk: 'Risk assessment', sentiment: 'Market sentiment', fundamentals: 'Fundamentals', news: 'News verification', catalyst: 'Price catalyst',
    defaultQuery: 'Assess overall trust posture and flag any emerging manipulation risk.', initializing: 'INITIALIZING HERMES BRIDGE…',
    focused: 'FOCUSED', trustScore: 'TRUST SCORE', trustBreakdown: 'TRUST BREAKDOWN', overviewProxy: 'OVERVIEW PROXY', proxyTitle: 'Derived from overview score; run analysis for evidence-bound components',
    fullBreakdown: 'FULL BREAKDOWN + REASONING', divergence: 'CROSS-SOURCE DIVERGENCE', alignment: 'Alignment nominal', monitor: 'Monitor divergence', conflict: 'Conflict detected', tapReview: 'tap to review',
    highTrust: 'HIGH TRUST', moderateTrust: 'MODERATE TRUST', lowTrust: 'LOW TRUST', economicWeight: 'Economic weight', refocus: 'click again to re-focus', focusHint: 'click a planet to focus the bridge on that currency', focus: 'Focus',
    stage: 'STAGE', scan: 'Source Scan', filter: 'Filter', crossverify: 'Cross-Verify', manipulation: 'Manipulation', composite: 'Composite Score',
    scanned: 'scanned', passed: 'passed', divergenceUnit: 'divergence', flagged: 'flagged', close: 'CLOSE', proxyTrace: 'OVERVIEW PROXY · RUN ANALYSIS FOR EVIDENCE-BOUND TRACE',
    dropped: 'FLAGGED / DROPPED', flaggedChannel: 'Flagged channel', reasoningTrace: 'REASONING TRACE', weight: 'coefficient', degraded: 'UPLINK DEGRADED — showing available snapshot',
    reputation: 'Reputation', corroboration: 'Corroboration', recency: 'Recency', resistance: 'Manipulation resistance', facts: 'FACTS', inference: 'INFERENCE', conclusion: 'CONCLUSION',
    liveTelemetry: 'LIVE TELEMETRY', sourceCount: 'Sources', integrity: 'Integrity', signalState: 'Signal state', stable: 'STABLE', watch: 'WATCH', degradedState: 'DEGRADED',
    agentOutput: 'HERMES ACTIVE REPORT',
    help: 'HELP', settings: 'SETTINGS', notifications: 'NOTIFICATIONS',
  },
} as const

type MessageKey = keyof typeof messages.en
type I18nValue = { locale: HermesLocale; setLocale: (locale: HermesLocale) => void; t: (key: MessageKey) => string }
const HermesI18nContext = createContext<I18nValue | null>(null)

function initialLocale(): HermesLocale {
  const saved = document.cookie.split('; ').find((item) => item.startsWith('trustforge_hermes_locale='))?.split('=')[1]
  if (saved === 'zh-TW' || saved === 'en') return saved
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh-TW' : 'zh-TW'
}

export function HermesI18nProvider({ children }: { children: ReactNode }) {
  const [locale, updateLocale] = useState<HermesLocale>(initialLocale)
  const value = useMemo<I18nValue>(() => ({
    locale,
    setLocale: (next) => {
      document.cookie = `trustforge_hermes_locale=${next}; Max-Age=31536000; Path=/; SameSite=Lax`
      updateLocale(next)
    },
    t: (key) => messages[locale][key],
  }), [locale])
  return <HermesI18nContext.Provider value={value}>{children}</HermesI18nContext.Provider>
}

export function useHermesI18n(): I18nValue {
  const value = useContext(HermesI18nContext)
  if (!value) throw new Error('useHermesI18n must be used within HermesI18nProvider')
  return value
}
