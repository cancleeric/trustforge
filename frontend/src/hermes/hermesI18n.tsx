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
    tagline: '不只是再問 AI 一次——我們對每一條市場主張做多源裁判，附證據、血統與反方意見。',
    badge: '多源 × 5 模式 × 不可竄改血統',
    focused: '目前焦點', trustScore: '信任分數', trustBreakdown: '信任拆解', overviewProxy: '總覽代理值', proxyTitle: '由總覽分數推導；執行正式分析後才會產生證據綁定組件',
    fullBreakdown: '查看完整拆解與推理', divergence: '跨來源分歧', alignment: '來源一致性正常', monitor: '持續監控分歧', conflict: '偵測到來源衝突', tapReview: '點擊查看', monitoring: '持續監控中',
    highTrust: '高信任', moderateTrust: '中度信任', lowTrust: '低信任', economicWeight: '市場權重', refocus: '再次點擊可重新聚焦', focusHint: '點選幣種，讓 Hermes 聚焦該市場', focus: '聚焦',
    stage: '節點', scan: '來源掃描', filter: '可信過濾', crossverify: '交叉驗證', manipulation: '操縱偵測', composite: '綜合信任分數',
    scanned: '已掃描', passed: '已通過', divergenceUnit: '分歧', flagged: '已標記', close: '關閉', proxyTrace: '總覽代理值 · 執行正式分析以取得證據綁定軌跡',
    dropped: '已標記／排除', flaggedChannel: '異常通道', reasoningTrace: '推理軌跡', weight: '評分係數',
    reputation: '來源信譽', corroboration: '交叉佐證', recency: '資料時效', resistance: '抗操縱能力', facts: '事實', inference: '推論', conclusion: '結論',
    liveTelemetry: '即時遙測', sourceCount: '可用來源', integrity: '完整性', signalState: '訊號狀態', stable: '穩定', watch: '監控', degradedState: '下降',
    agentOutput: 'Hermes 主動報告',
    degraded: '連線品質下降，正在顯示可用快照',
    help: '說明', settings: '設定', notifications: '通知',
    beginnerModeOn: '新手模式', beginnerModeOff: '完整模式',
    dynamicOn: '▶ 動態', dynamicOff: '⏸ 低動態',
    helpToggle: '？ 說明', shipToggle: '⬡ 艦體升級',
    whatToDo: '我想做什麼？', chooseGoal: '選一個目的，Hermes 會替你準備問題與分析方式。',
    conversationMemory: '對話記憶 · SQLITE',
    similarQuestions: '相似歷史題目',
    historyDisclaimer: '歷史參考 · 非本次 Evidence，不參與信任評分',
    noSimilarQuestions: '尚無相似歷史題目',
    analysisExpectationPrefix: '將使用目前可用的多來源資料進行「', analysisExpectationSuffix: '」，整理可信程度、主要原因與不確定性。',
    suggestSwitchToPrefix: '建議改用「', suggestSwitchToSuffix: '」分析',
    reAnalyze: '立即重新分析', analyzingNow: 'Hermes 自動分析中…',
    queued: '排隊', processing: '處理中', standby: '待命', retryLabel: '重試', retryIn: '後重試', waitingSnapshot: '等待此幣快照',
    beginnerNarrativeTitle: '新手脈絡 · 3 步看懂一個代幣',
    beginnerNarrativeClose: '關閉 ✕', beginnerNarrativeCloseLabel: '關閉新手脈絡引導',
    beginnerStep1Title: '查代幣定位', beginnerStep1Desc: '輸入代幣（例：$ARB）→ 自動輸出 [Layer 2] 層級、結算鏈、Gas 代幣與上下游依賴。', beginnerStep1Cta: '查資產脈絡 →',
    beginnerStep2Title: '名詞解釋 + 風險提示', beginnerStep2Desc: 'FDV / TVL / Gas Fee / 解鎖賣壓等專有名詞自動標註，點擊看白話定義與 ⚠️ 風險提示。', beginnerStep2Cta: '看名詞標註 →',
    beginnerStep3Title: '同層比較 + 生態聯動', beginnerStep3Desc: '同層資產 TPS/TVL/Gas 橫向比較；官方升級事件對依賴資產的可能影響路徑。', beginnerStep3CtaPeer: '同層比較 →', beginnerStep3CtaEco: '生態聯動 →',
    trainingData: '訓練資料', trainingDataNotEnabled: '訓練資料未啟用', trainingDataUnavailable: '訓練狀態暫不可用',
    directionProgress: '方向標註進度', metThresholdPrefix: '（已達門檻 ', metThresholdSuffix: '）',
    thresholdMet: '✓ 已達升級門檻', thresholdGapPrefix: '差 ', thresholdGapSuffix: ' 筆達標',
    totalRecords: '總筆數', directionRatio: '有方向比例', backfillStatus: '回填狀態',
    backfillRunningPrefix: '進行中 ', backfillDoneLabel: '完成',
    trainingLoading: '載入中…',
    errorTitleAutomationPaused: '自動工作已暫停', errorTitleNetwork: '連線異常', errorTitleTimeout: '回應逾時',
    errorTitleParse: '資料格式異常', errorTitleBusy: '伺服器忙碌', errorTitleAnalysisTimeout: '分析逾時',
    errorTitleGeneric: '服務異常', errorRetry: '↻ 重新嘗試', errorRetryTitle: '重試',
    errorDescAutomationPaused: '排程分析已暫停，僅接受手動送出的請求。',
    errorDescNetwork: '無法連線到伺服器，請確認網路連線後再試一次。',
    errorDescTimeout: '請求已送出，但伺服器在時限內沒有回應。',
    errorDescParse: '伺服器回應的資料格式不符預期。',
    errorDescBusy: '伺服器目前請求量較高，請稍候再試。',
    errorDescAnalysisTimeout: '分析工作長時間沒有回應，後端可能已中斷；工作仍可能在背景執行，請稍後再確認狀態，不要重新送出。',
    errorDescGeneric: '發生非預期的錯誤，請重試或稍後再試一次。',
    analyzeWorkspaceTitle: '分析工作區', analyzeWorkspaceSubtitle: '每次執行固定一個 run，保留來源、節點、證據與輸出供後續稽核。',
    manualPriorityPrefix: '手動優先處理中：', queuePositionPrefix: '（佇列第 ', queuePositionSuffix: ' 位）',
    creatingManualJob: 'Hermes 正在建立 {coin} 的手動分析工作…',
    analyzingNewSnapshot: 'Hermes 正在分析新的資料快照；目前保留顯示上一個完整結果，完成後會一次切換。',
    noAnalysisData: '尚無分析資料',
    noAnalysisDataPendingHint: '目前尚未產生此題的分析快照，請稍候或重新送出分析。',
    noAnalysisDataEmptyHint: '請在左側面板選擇幣種與題型後送出分析，完成後報告、證據與執行紀錄將顯示於此。',
    firstRunEyebrow: '首次試用', firstRunTitle: '先完成一次清楚的市場判讀',
    firstRunStep1: '1. 選擇想了解的資產', firstRunStep2: '2. 選擇想解決的問題',
    firstRunStep3: '3. 開始後會整理近期資料，給你一句結論、三個原因、目前限制與下一步。',
    firstRunStart: '開始第一次分析',
    beginnerResultEyebrow: '首次分析結果', beginnerResultCurrentState: '目前狀態：',
    beginnerResultInsufficientData: '資料不足，暫不判定', beginnerResultNeedsWatching: '需要持續觀察',
    beginnerResultReasonsTitle: '三個原因', beginnerResultLimitLabel: '限制：', beginnerResultNextStepLabel: '下一步：',
    beginnerResultDefaultLimit: '目前資料仍可能不足，請把結論視為需要持續追蹤的判讀。',
    beginnerResultDefaultNextStep: '保留觀察，等更多來源更新後再重新分析。',
    beginnerResultShowFull: '查看完整分析', beginnerResultRedo: '再做一次簡化分析',
    qcModeRisk: '風險評估', qcModeSentiment: '市場情緒', qcModeFundamentals: '基本面驗證', qcModeNews: '事件與新聞', qcModeCatalyst: '催化因素',
    qcHeading: '設定本次分析', qcSubtext: '送出後會建立獨立 run，來源與執行紀錄不會覆蓋既有結果。',
    qcModeLabel: '分析模式', qcComparePrefix: '雙幣比較請至', qcCompareLinkLabel: '比較頁', qcComparePeriod: '。',
    qcQuestionLabel: '問題', qcSubmit: '立即重新分析',
    intentTrustLabel: '這個幣現在可信嗎？', intentTrustDesc: '整理整體可信程度與主要風險',
    intentManipulationLabel: '有沒有操縱風險？', intentManipulationDesc: '檢查異常訊號與來源分歧',
    intentNewsLabel: '這則消息可信嗎？', intentNewsDesc: '比對新聞與其他可靠來源',
    intentCompareLabel: '什麼正在影響價格？', intentCompareDesc: '找出近期可能的價格催化因素',
    intentDeclineLabel: '為什麼信任分數下降？', intentDeclineDesc: '找出下降來源與變化原因',
    firstRunSkip: '我熟悉產品，進入完整模式',
    firstRunEyebrowShort: '第一次使用', firstRunHeading: '你想了解哪個資產？', firstRunLede: '不用先理解分析模式或信任模型，只要選擇目的即可。',
    firstRunChooseCoin: '1. 選擇資產', firstRunChooseIntent: '2. 你最想知道什麼？',
    firstRunReady: '3. 準備開始', firstRunSummaryPrefix: '分析 ', firstRunSummarySeparator: '：',
    firstRunSummaryHint: '系統會比對多個來源，整理一句結論、三個主要原因與資料限制。',
    firstRunCta: '開始第一次分析 →',
    firstRunFooter: '信任分析衡量資料與證據的可信程度，不預測價格，也不是投資建議。',
  },
  en: {
    analyze: 'ANALYZE', compare: 'COMPARE', history: 'HISTORY', sources: 'SOURCES', costs: 'COSTS', galaxy: 'GALAXY',
    liveUplink: 'LIVE UPLINK', active: 'ACTIVE', costLedger: 'COST LEDGER', syncing: 'SYNCING', navigation: 'Hermes workspace navigation', language: 'Switch language',
    telemetry: 'GALAXY TELEMETRY', tracked: 'Assets tracked', healthy: 'Healthy', moderate: 'Moderate', danger: 'Danger', latency: 'Uplink latency', online: 'ONLINE',
    transmitted: 'ORDER TRANSMITTED', analysisMode: 'ANALYSIS MODE', order: 'ORDER TO HERMES', transmitting: 'TRANSMITTING…', transmit: 'TRANSMIT TO HERMES',
    risk: 'Risk assessment', sentiment: 'Market sentiment', fundamentals: 'Fundamentals', news: 'News verification', catalyst: 'Price catalyst',
    defaultQuery: 'Assess overall trust posture and flag any emerging manipulation risk.', initializing: 'INITIALIZING HERMES BRIDGE…',
    tagline: 'Not another AI summary. We adjudicate every market claim across sources — with evidence, lineage, and dissent.',
    badge: 'Multi-source × 5 modes × immutable lineage',
    focused: 'FOCUSED', trustScore: 'TRUST SCORE', trustBreakdown: 'TRUST BREAKDOWN', overviewProxy: 'OVERVIEW PROXY', proxyTitle: 'Derived from overview score; run analysis for evidence-bound components',
    fullBreakdown: 'FULL BREAKDOWN + REASONING', divergence: 'CROSS-SOURCE DIVERGENCE', alignment: 'Alignment nominal', monitor: 'Monitor divergence', conflict: 'Conflict detected', tapReview: 'tap to review', monitoring: 'monitoring',
    highTrust: 'HIGH TRUST', moderateTrust: 'MODERATE TRUST', lowTrust: 'LOW TRUST', economicWeight: 'Economic weight', refocus: 'click again to re-focus', focusHint: 'click a planet to focus the bridge on that currency', focus: 'Focus',
    stage: 'STAGE', scan: 'Source Scan', filter: 'Filter', crossverify: 'Cross-Verify', manipulation: 'Manipulation', composite: 'Composite Score',
    scanned: 'scanned', passed: 'passed', divergenceUnit: 'divergence', flagged: 'flagged', close: 'CLOSE', proxyTrace: 'OVERVIEW PROXY · RUN ANALYSIS FOR EVIDENCE-BOUND TRACE',
    dropped: 'FLAGGED / DROPPED', flaggedChannel: 'Flagged channel', reasoningTrace: 'REASONING TRACE', weight: 'coefficient', degraded: 'UPLINK DEGRADED — showing available snapshot',
    reputation: 'Reputation', corroboration: 'Corroboration', recency: 'Recency', resistance: 'Manipulation resistance', facts: 'FACTS', inference: 'INFERENCE', conclusion: 'CONCLUSION',
    liveTelemetry: 'LIVE TELEMETRY', sourceCount: 'Sources', integrity: 'Integrity', signalState: 'Signal state', stable: 'STABLE', watch: 'WATCH', degradedState: 'DEGRADED',
    agentOutput: 'HERMES ACTIVE REPORT',
    help: 'HELP', settings: 'SETTINGS', notifications: 'NOTIFICATIONS',
    beginnerModeOn: 'Beginner mode', beginnerModeOff: 'Full mode',
    dynamicOn: '▶ Motion', dynamicOff: '⏸ Reduced motion',
    helpToggle: '? Help', shipToggle: '⬡ Ship upgrade',
    whatToDo: 'What do you want to do?', chooseGoal: 'Pick a goal and Hermes will prepare the question and analysis mode for you.',
    conversationMemory: 'CONVERSATION MEMORY · SQLITE',
    similarQuestions: 'Similar past questions',
    historyDisclaimer: 'Historical reference · not this run’s evidence, does not affect the trust score',
    noSimilarQuestions: 'No similar past questions yet',
    analysisExpectationPrefix: 'Will use the currently available multi-source data for "', analysisExpectationSuffix: '", summarizing credibility, key reasons, and uncertainty.',
    suggestSwitchToPrefix: 'Suggest switching to "', suggestSwitchToSuffix: '" analysis',
    reAnalyze: 'Re-run analysis', analyzingNow: 'Hermes is analyzing…',
    queued: 'Queued', processing: 'Processing', standby: 'Standby', retryLabel: 'Retry', retryIn: 'retry in', waitingSnapshot: 'Waiting for this asset’s snapshot',
    beginnerNarrativeTitle: 'BEGINNER GUIDE · 3 STEPS TO UNDERSTAND A TOKEN',
    beginnerNarrativeClose: 'Close ✕', beginnerNarrativeCloseLabel: 'Close beginner guide',
    beginnerStep1Title: 'Locate the token', beginnerStep1Desc: 'Enter a token (e.g. $ARB) → auto-outputs its Layer 2 tier, settlement chain, gas token, and up/downstream dependencies.', beginnerStep1Cta: 'View asset context →',
    beginnerStep2Title: 'Glossary + risk hints', beginnerStep2Desc: 'Terms like FDV / TVL / Gas Fee / unlock sell pressure are auto-annotated — click for plain definitions and ⚠️ risk hints.', beginnerStep2Cta: 'View glossary →',
    beginnerStep3Title: 'Peer comparison + ecosystem links', beginnerStep3Desc: 'Compare peer assets by TPS/TVL/Gas; trace how official upgrades may ripple to dependent assets.', beginnerStep3CtaPeer: 'Compare peers →', beginnerStep3CtaEco: 'Ecosystem links →',
    trainingData: 'TRAINING DATA', trainingDataNotEnabled: 'Training data not enabled', trainingDataUnavailable: 'Training status unavailable',
    directionProgress: 'Direction label progress', metThresholdPrefix: ' (threshold met at ', metThresholdSuffix: ')',
    thresholdMet: '✓ Upgrade threshold met', thresholdGapPrefix: 'Need ', thresholdGapSuffix: ' more to reach threshold',
    totalRecords: 'Total records', directionRatio: 'Direction ratio', backfillStatus: 'Backfill status',
    backfillRunningPrefix: 'Running ', backfillDoneLabel: 'Done',
    trainingLoading: 'Loading…',
    errorTitleAutomationPaused: 'Automation paused', errorTitleNetwork: 'Connection error', errorTitleTimeout: 'Response timeout',
    errorTitleParse: 'Malformed response', errorTitleBusy: 'Server busy', errorTitleAnalysisTimeout: 'Analysis timeout',
    errorTitleGeneric: 'Service error', errorRetry: '↻ Retry', errorRetryTitle: 'Retry',
    errorDescAutomationPaused: 'Scheduled analysis is paused; only manually submitted requests are accepted.',
    errorDescNetwork: 'Could not reach the server. Check your connection and try again.',
    errorDescTimeout: 'The request was sent, but the server did not respond in time.',
    errorDescParse: 'The server response was not in the expected format.',
    errorDescBusy: 'The server is currently under heavy load. Please try again shortly.',
    errorDescAnalysisTimeout: 'The analysis job has not responded for a while and the backend may be down. It may still be running in the background — please check its status again later instead of resubmitting.',
    errorDescGeneric: 'An unexpected error occurred. Please retry or try again later.',
    analyzeWorkspaceTitle: 'Analysis workspace', analyzeWorkspaceSubtitle: 'Each run is a single, fixed execution — sources, nodes, evidence, and output are retained for later audit.',
    manualPriorityPrefix: 'Manual priority in progress: ', queuePositionPrefix: ' (queue position ', queuePositionSuffix: ')',
    creatingManualJob: 'Hermes is creating a manual analysis job for {coin}…',
    analyzingNewSnapshot: 'Hermes is analyzing a new data snapshot; the previous full result stays shown until it switches over.',
    noAnalysisData: 'No analysis data yet',
    noAnalysisDataPendingHint: 'No analysis snapshot has been produced for this question yet — please wait or resubmit.',
    noAnalysisDataEmptyHint: 'Choose a coin and question type in the left panel and submit — the report, evidence, and execution log will appear here.',
    firstRunEyebrow: 'FIRST RUN', firstRunTitle: 'Complete one clear market read first',
    firstRunStep1: '1. Choose the asset you want to understand', firstRunStep2: '2. Choose the question you want answered',
    firstRunStep3: '3. Once started, recent data is compiled into a single conclusion, three reasons, current limits, and a next step.',
    firstRunStart: 'Start first analysis',
    beginnerResultEyebrow: 'FIRST ANALYSIS RESULT', beginnerResultCurrentState: 'Current state: ',
    beginnerResultInsufficientData: 'Insufficient data, not yet determined', beginnerResultNeedsWatching: 'Needs continued observation',
    beginnerResultReasonsTitle: 'Three reasons', beginnerResultLimitLabel: 'Limits: ', beginnerResultNextStepLabel: 'Next step: ',
    beginnerResultDefaultLimit: 'Current data may still be insufficient — treat this conclusion as needing continued tracking.',
    beginnerResultDefaultNextStep: 'Hold and observe; re-run analysis once more sources update.',
    beginnerResultShowFull: 'View full analysis', beginnerResultRedo: 'Run another simplified analysis',
    qcModeRisk: 'Risk assessment', qcModeSentiment: 'Market sentiment', qcModeFundamentals: 'Fundamentals check', qcModeNews: 'Events & news', qcModeCatalyst: 'Catalysts',
    qcHeading: 'Configure this analysis', qcSubtext: 'Submitting creates an independent run; sources and execution logs never overwrite existing results.',
    qcModeLabel: 'Analysis mode', qcComparePrefix: 'For two-coin comparison, go to', qcCompareLinkLabel: 'the compare page', qcComparePeriod: '.',
    qcQuestionLabel: 'Question', qcSubmit: 'Re-run analysis now',
    intentTrustLabel: 'Is this coin trustworthy right now?', intentTrustDesc: 'Summarize overall credibility and key risks',
    intentManipulationLabel: 'Any manipulation risk?', intentManipulationDesc: 'Check anomalous signals and source divergence',
    intentNewsLabel: 'Is this news credible?', intentNewsDesc: 'Cross-check the news against other reliable sources',
    intentCompareLabel: "What's moving the price?", intentCompareDesc: 'Find likely near-term price catalysts',
    intentDeclineLabel: 'Why did the trust score drop?', intentDeclineDesc: 'Find the source and cause of the decline',
    firstRunSkip: "I'm familiar with the product, skip to full mode",
    firstRunEyebrowShort: 'FIRST TIME HERE', firstRunHeading: 'Which asset do you want to understand?', firstRunLede: 'No need to learn analysis modes or the trust model first — just pick your goal.',
    firstRunChooseCoin: '1. Choose an asset', firstRunChooseIntent: '2. What do you most want to know?',
    firstRunReady: '3. Ready to start', firstRunSummaryPrefix: 'Analyze ', firstRunSummarySeparator: ': ',
    firstRunSummaryHint: 'The system cross-checks multiple sources and produces one conclusion, three key reasons, and data limits.',
    firstRunCta: 'Start first analysis →',
    firstRunFooter: 'Trust analysis measures the credibility of data and evidence — it does not predict price and is not investment advice.',
  },
} as const

type MessageKey = keyof typeof messages.en
type I18nParams = Record<string, string | number>
type I18nValue = { locale: HermesLocale; setLocale: (locale: HermesLocale) => void; t: (key: MessageKey, params?: I18nParams) => string }
const HermesI18nContext = createContext<I18nValue | null>(null)

// N7: some workspace copy embeds runtime values (coin symbol, pipeline stage
// name, queue position). Rather than string-concatenating localized
// fragments (which breaks word order between zh-TW/EN), templates use
// `{param}` placeholders substituted here.
function interpolate(template: string, params?: I18nParams): string {
  if (!params) return template
  return template.replace(/\{(\w+)\}/g, (match, key: string) => (key in params ? String(params[key]) : match))
}

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
    t: (key, params) => interpolate(messages[locale][key], params),
  }), [locale])
  return <HermesI18nContext.Provider value={value}>{children}</HermesI18nContext.Provider>
}

export function useHermesI18n(): I18nValue {
  const value = useContext(HermesI18nContext)
  if (!value) throw new Error('useHermesI18n must be used within HermesI18nProvider')
  return value
}
