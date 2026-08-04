import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import HermesTopBar from '../hermes/HermesTopBar'
import HermesHeroTagline from '../hermes/HermesHeroTagline'
import HermesLeftRail from '../hermes/HermesLeftRail'
import HermesRightRail from '../hermes/HermesRightRail'
import CurrencyGalaxy from '../hermes/CurrencyGalaxy'
import StageBar from '../hermes/StageBar'
import StageDrilldown from '../hermes/StageDrilldown'
import { buildGalaxyModel, COMPONENT_WEIGHTS, deriveSelected, HERMES_AMBER, HERMES_CYAN, HERMES_RED, tierOf, type GalaxyCoin, type GalaxyModel, type TrustComponent } from '../lib/hermesData'
import { getOverview, getCosts, getHealth, getAnalysisFlow, getAnalysisJourney, getAnalysisQuestionContext, getHermesUpgrades, type AnalysisFlowData, type AnalysisJourneyData, type AnalysisQuestionContext, type HermesUpgradeData } from '../lib/endpoints'
import '../hermes/hermes.css'
import { useHermesI18n } from '../hermes/hermesI18n'
import HermesModuleDeck, { type HermesWorkspaceModule } from '../hermes/HermesModuleDeck'
import type { BridgeHologramData } from '../components/BridgeHologramContext'
import HermesUpgradeShip from '../hermes/HermesUpgradeShip'
import HermesOnboarding from '../hermes/HermesOnboarding'
import HermesBeginnerNarrative from '../components/HermesBeginnerNarrative'
import HermesMobileDivergenceEntry from '../hermes/HermesMobileDivergenceEntry'
import TrainingStatusCard from '../components/TrainingStatusCard'
import AgentCoreStatusBadge from '../components/AgentCoreStatusBadge'
import { getWhaleSummary } from '../lib/endpoints'
import type { WhaleSummary } from '../components/WhaleActivityPanel'
import { defaultQuestionTypeForFocus, isAnalysisFocusId, isQuestionTypeId, type AnalysisFocusId, type QuestionTypeId } from '../lib/analysisTaxonomy'
import { recommendAnalysisMode, rememberHermesOnboarding, shouldShowHermesOnboarding, type AnalysisModeId } from '../lib/beginnerExperience'
import HermesFirstRun from '../hermes/HermesFirstRun'
import { useReducedMotion } from '../lib/useReducedMotion'
import { useAdaptiveQuality } from '../hermes/useAdaptiveQuality'
import FpsMeter from '../hermes/FpsMeter'
import WorkspaceStageDrilldown from '../hermes/WorkspaceStageDrilldown'
import { buildWorkspaceStageDetails } from '../hermes/workspaceStageDetails'
import DiandianOnboarding from '../components/DiandianOnboarding'

export type ServiceMonitorState = 'checking' | 'ok' | 'empty' | 'stale' | 'error'

// N30: below this width the CSS in hermes.css (`@media (max-width:900px)`)
// already fully hides `.hermes-right-rail` (display:none) in favour of
// `HermesMobileDivergenceEntry`, but both stayed mounted in the DOM. That
// left two interactive controls sharing the same accessible name ("跨來源
// 分歧") — one real, one display:none. A locator resolving in DOM order
// (right rail is mounted first) picks the hidden one and hangs waiting for
// it to become visible, a real reproducible click-timeout on real/CEO
// hardware, not a probe false positive. Unmounting the right rail below the
// same breakpoint the CSS already uses removes the duplicate outright.
const HERMES_RIGHT_RAIL_BREAKPOINT = 900

function useIsNarrowViewport(maxWidth: number): boolean {
  const [isNarrow, setIsNarrow] = useState(() =>
    typeof window === 'undefined' ? false : window.matchMedia(`(max-width: ${maxWidth}px)`).matches,
  )
  useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${maxWidth}px)`)
    const handler = (event: MediaQueryListEvent | MediaQueryList) => setIsNarrow(event.matches)
    handler(mql)
    mql.addEventListener('change', handler)
    return () => mql.removeEventListener('change', handler)
  }, [maxWidth])
  return isNarrow
}

export default function HermesDashboard() {
  const { locale, t } = useHermesI18n()
  // PLAN 方案 B：狀態改存 id（官方題型 + 分析角度），不再存翻譯後的 label
  // 再用 indexOf 反查。原本 ['risk','sentiment',…] 這組平行陣列散在 5 個地方，
  // 語系一切換 label 就變，任何一處漏改就對不上。見 lib/analysisTaxonomy.ts。
  const [searchParams, setSearchParams] = useSearchParams()
  const qaMode = searchParams.get('qa') === '1' || searchParams.get('reducedMotion') === '1'
  const requestedCoin = searchParams.get('coin')?.toLowerCase()
  const [model, setModel] = useState<GalaxyModel>(() => buildGalaxyModel(null))
  const [, setOverviewRevision] = useState('boot')
  const [selectedId, setSelectedId] = useState(() =>
    requestedCoin && ['btc', 'eth', 'sol', 'bnb', 'xrp'].includes(requestedCoin) ? requestedCoin : 'btc',
  )
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [selectedStage, setSelectedStage] = useState<string | null>(null)
  const [phase, setPhase] = useState<'ready' | 'loading'>('ready')
  // N13 fix: bumped on every explicit left-rail submit click, independent of
  // whether the coin/type/q/mode URL params actually changed. Forwarded to
  // AnalyzePage (via HermesModuleDeck) so it can tell "user explicitly
  // clicked 立即重新分析 with the same question" apart from a plain reload —
  // see AnalyzePage's `resubmitSignal` effect for the full rationale.
  const [resubmitSignal, setResubmitSignal] = useState(0)
  const [lastOrder, setLastOrder] = useState(false)
  const [startupStep, setStartupStep] = useState(0)
  const [moduleTelemetry, setModuleTelemetry] = useState<BridgeHologramData | null>(null)
  const [analysisFlow, setAnalysisFlow] = useState<AnalysisFlowData | null>(null)
  const [analysisJourney, setAnalysisJourney] = useState<AnalysisJourneyData | null>(null)
  const [questionContext, setQuestionContext] = useState<AnalysisQuestionContext | null>(null)
  const [shipOpen, setShipOpen] = useState(false)
  const [onboardingOpen, setOnboardingOpen] = useState(false)
  const [diandianOnboardingOpen, setDiandianOnboardingOpen] = useState(() => {
    try { return !document.cookie.split('; ').some(c => c.startsWith('diandian_onboarding_done=1')) } catch { return true }
  })
  const [firstRunOpen, setFirstRunOpen] = useState(() => !qaMode && shouldShowHermesOnboarding() && searchParams.get('tour') !== '1')
  const [beginnerMode, setBeginnerMode] = useState(() => !document.cookie.split('; ').some((item) => item === 'trustforge_hermes_experience=full'))
  // #847：把新手模式掛到 <html> 上。名詞解釋的小卡是 portal 到 <body> 的
  // （見 GlossaryTerm 的 N51 註解），儀表板容器上的 class 傳不進去，只有根節點
  // 兩邊都看得到。除了旗標本身，沒有任何行為綁在這裡。
  useEffect(() => {
    const root = document.documentElement
    if (beginnerMode) root.dataset.tfBeginner = '1'
    else delete root.dataset.tfBeginner
  }, [beginnerMode])
  const [upgradeData, setUpgradeData] = useState<HermesUpgradeData | null>(null)
  const [upgradeLoading, setUpgradeLoading] = useState(false)
  const [whaleSummary, setWhaleSummary] = useState<WhaleSummary | null>(null)
  const [urlQuestionType, setUrlQuestionType] = useState<QuestionTypeId | null>(null)
  // N69：question_type 不再是使用者選的一顆下拉（原因見 HermesLeftRail 的註解——
  // 官方文件那三種是「範例題型」不是限制），改由分析角度推導。這個對應表跟後端
  // `analysis_flow.MODES` 是同一套：fundamentals/catalyst → hypothesis，其餘
  // → multi_source，所以前端顯示的題型跟後端實際跑的一定一致，不會各說各話。
  // URL 的 ?type= 仍然被尊重（深連結／既有測試／從 /analyze 帶回來的狀態）。
  const [query, setQuery] = useState(t('defaultQuery'))
  const skipQuestionContextRef = useRef(false)
  // N70（CEO：「分析角度 也不給使用者選」）：角度不再是使用者狀態，改成推導值。
  // 優先序：URL 的 ?mode=（深連結、排程回連、既有測試都靠它）> 由題目關鍵字
  // 判定（`recommendAnalysisMode`，beginnerExperience.ts:26，已有測試）。
  // 後端完全沒動：`mode` 仍是必填且白名單化（analysis_flow.py:459-462），我們
  // 送的值一樣落在那五個裡面，只是不再要求使用者自己挑。
  const urlFocus = searchParams.get('mode')
  const focus: AnalysisFocusId = isAnalysisFocusId(urlFocus) ? urlFocus : recommendAnalysisMode(query)
  const questionType = urlQuestionType ?? defaultQuestionTypeForFocus(focus)
  const [typedLen, setTypedLen] = useState(0)
  const [focusPulse, setFocusPulse] = useState(false)
  const [displayScore, setDisplayScore] = useState(0)
  const displayScoreRef = useRef(0)
  // 版號要等 getHealth() 回來（實測約 4 秒）才有值。初值原本是 'snapshot'，
  // 那看起來像一個真的版號，使用者在那 4 秒內截到的畫面會誤以為系統版號叫
  // snapshot。改成 '…' 讓「還沒載到」跟「真的版號」在視覺上分得開。
  const [runtimeVersion, setRuntimeVersion] = useState('…')
  const [costLedger, setCostLedger] = useState<number | null>(null)
  const [startupComplete, setStartupComplete] = useState(qaMode)
  const { reducedMotion, toggle: toggleReducedMotion } = useReducedMotion()
  const { fps, quality, measuring } = useAdaptiveQuality()
  const isRightRailCollapsed = useIsNarrowViewport(HERMES_RIGHT_RAIL_BREAKPOINT)
  const [serviceMonitor, setServiceMonitor] = useState<Record<string, ServiceMonitorState>>({
    overview: 'checking', health: 'checking', sources: 'checking', history: 'checking', costs: 'checking',
  })
  const [boot, setBoot] = useState({ topbar: false, left: false, galaxy: false, right: false, bottom: false })
  const [loadError, setLoadError] = useState<string | null>(null)
  const requestedModule = searchParams.get('workspace')
  const activeModule: HermesWorkspaceModule | null =
    requestedModule === 'analyze' || requestedModule === 'compare' || requestedModule === 'history' || requestedModule === 'status' || requestedModule === 'costs' || requestedModule === 'whale'
      ? requestedModule : null
  const activeQuestionMode = focus
  const workspaceStageDetails = activeModule
    ? buildWorkspaceStageDetails(activeModule, locale, moduleTelemetry)
    : []
  const selectedWorkspaceStage = workspaceStageDetails.find((stage) => stage.id === selectedStage)

  // N70（CEO：「新手模式不要動選單，動作是切回首頁、中間顯示新手板」）：
  // 新手模式原本的作用是把左軌導覽砍到只剩「分析」——把功能藏起來，新手反而
  // 沒有比較的入口。選單一律不動；切進新手模式改成回首頁，中間才顯示新手板
  // （`beginnerMode && !activeModule` 才會 render HermesBeginnerNarrative，
  // 停在 /compare 之類的工作區時切過去是完全沒有畫面反應的）。
  const setExperienceMode = useCallback((enabled: boolean) => {
    setBeginnerMode(enabled)
    document.cookie = `trustforge_hermes_experience=${enabled ? 'beginner' : 'full'}; Max-Age=31536000; Path=/; SameSite=Lax`
    if (enabled) {
      const next = new URLSearchParams(searchParams)
      next.delete('workspace')
      setSearchParams(next)
    }
  }, [searchParams, setSearchParams])

  // N70：意圖卡只負責把題目填進去。角度由題目推導（見上面的 `focus`），題型再由
  // 角度推導，所以這裡不必也不該再寫任何一顆狀態；`mode` 參數保留是為了讓呼叫端
  // （BEGINNER_INTENTS）不必改形狀，但刻意不使用。
  const chooseIntent = useCallback((_mode: AnalysisModeId, question: string) => {
    setUrlQuestionType(null)
    setQuery(question)
  }, [])

  useEffect(() => {
    if (searchParams.get('tour') === '1') setOnboardingOpen(true)
    // Onboarding is intentionally evaluated once per page entry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const leaveFirstRun = useCallback(() => {
    rememberHermesOnboarding()
    setFirstRunOpen(false)
  }, [])

  const startFirstRun = useCallback((coin: string, mode: AnalysisModeId, question: string) => {
    rememberHermesOnboarding()
    setFirstRunOpen(false)
    setPhase('loading')
    setLastOrder(true)
    setSelectedId(coin.toLowerCase())
    // N70：原本這行漏了 catalyst（catalyst 在後端是 HYPOTHESIS，
    // analysis_flow.py:61），跟 `defaultQuestionTypeForFocus` 各說各話。改成直接
    // 用同一個函式，兩邊不可能再分岔。
    const type = defaultQuestionTypeForFocus(mode)
    setSearchParams({ coin, type, q: question, mode, workspace: 'analyze' })
  }, [setSearchParams])

  const toggleShip = useCallback(() => {
    if (shipOpen) { setShipOpen(false); return }
    setShipOpen(true); setUpgradeLoading(true)
    void getHermesUpgrades().then((result) => { if (result.ok) setUpgradeData(result.data) }).finally(() => setUpgradeLoading(false))
  }, [shipOpen])

  const refreshUpgrades = useCallback(() => {
    setUpgradeLoading(true)
    void getHermesUpgrades().then((result) => { if (result.ok) setUpgradeData(result.data) }).finally(() => setUpgradeLoading(false))
  }, [])

  const byIdRef = useRef<Record<string, GalaxyCoin>>(model.byId)

  useEffect(() => {
    if (requestedCoin && model.byId[requestedCoin] && requestedCoin !== selectedId) {
      setSelectedId(requestedCoin)
    }
  }, [model.byId, requestedCoin, selectedId])

  useEffect(() => {
    // N70：?mode= 不再同步進 state（focus 直接從 searchParams 推導），這裡只剩
    // ?type= 與 ?q= 要落回表單。
    const requestedType = searchParams.get('type')
    setUrlQuestionType(isQuestionTypeId(requestedType) ? requestedType : null)
    const nextQuery = searchParams.get('q')
    setQuery(nextQuery ?? `分析${(requestedCoin ?? 'btc').toUpperCase()}近期市場狀況，整合多源資料`)
  }, [requestedCoin, searchParams])

  const selectCoin = useCallback((id: string) => {
    setSelectedId(id)
    const next = new URLSearchParams(searchParams)
    next.set('coin', id.toUpperCase())
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  // Boot sequence is presentation, never an availability gate. Slow or failed
  // channels continue reporting through the bridge monitor after entry.
  useEffect(() => {
    if (qaMode) {
      setStartupComplete(true)
      setStartupStep(5)
      return
    }
    const timers = [1, 2, 3, 4, 5].map((step) => window.setTimeout(() => setStartupStep(step), step * 280))
    timers.push(window.setTimeout(() => setStartupComplete(true), 1800))
    return () => timers.forEach(window.clearTimeout)
  }, [qaMode])

  useEffect(() => {
    if (skipQuestionContextRef.current) {
      skipQuestionContextRef.current = false
      setQuestionContext(null)
      return
    }
    if (!query.trim()) {
      setQuestionContext(null)
      return
    }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      void getAnalysisQuestionContext(selectedId.toUpperCase(), activeQuestionMode, query.trim(), controller.signal).then((result) => {
        if (!controller.signal.aborted && result.ok) setQuestionContext(result.data)
      })
    }, 250)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [activeQuestionMode, query, selectedId])

  const fillCompetitionQuestion = useCallback((question: string) => {
    if (question === query) return
    skipQuestionContextRef.current = true
    setQuery(question)
  }, [query])

  useEffect(() => {
    let active = true
    let timer: number | undefined
    let controller: AbortController | null = null
    const poll = async () => {
      controller = new AbortController()
      const result = await getAnalysisJourney(controller.signal)
      if (active && result.ok) setAnalysisJourney(result.data)
      if (active) timer = window.setTimeout(() => void poll(), activeModule ? 15_000 : 5000)
    }
    void poll()
    return () => { active = false; if (timer !== undefined) window.clearTimeout(timer); controller?.abort() }
  }, [activeModule])

  useEffect(() => {
    let active = true
    let timer: number | undefined
    let controller: AbortController | null = null
    const poll = async () => {
      controller = new AbortController()
      const result = await getAnalysisFlow(controller.signal)
      if (active && result.ok) setAnalysisFlow(result.data)
      if (active) timer = window.setTimeout(() => void poll(), activeModule ? 10_000 : 1500)
    }
    void poll()
    return () => { active = false; if (timer !== undefined) window.clearTimeout(timer); controller?.abort() }
  }, [activeModule])

  const buildHermesMessage = useCallback((sel: GalaxyCoin, ph: 'ready' | 'loading'): string => {
    const scanned = Math.round(60 + sel.econ * 0.9)
    const tierText = sel.tier === 'healthy' ? t('highTrust') : sel.tier === 'moderate' ? t('moderateTrust') : t('lowTrust')
    if (locale === 'zh-TW') return ph === 'loading'
      ? `正在分析 ${sel.full}，交叉核對 ${scanned} 個來源…`
      : `正在追蹤 ${sel.full}。綜合信任分數 ${sel.score}/100，${tierText}。${sel.tier === 'healthy' ? '訊號乾淨，目前不需處置。' : sel.tier === 'moderate' ? '建議在增加曝險前持續監控來源分歧。' : '完整性訊號下降，建議謹慎。'}`
    return ph === 'loading'
      ? `Analyzing ${sel.full}… cross-referencing ${scanned} sources.`
      : `Tracking ${sel.full}. Composite trust score ${sel.score}/100 — ${tierText}. ` +
      (sel.tier === 'healthy' ? 'Signal is clean; no action required.'
        : sel.tier === 'moderate' ? 'Recommend monitoring divergence before increasing exposure.'
          : 'Advise caution — integrity signals are degraded.')
  }, [locale, t])

  const typeTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const scoreTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const pulseTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const bootTimers = useRef<ReturnType<typeof setTimeout>[]>([])

  const animateScoreTo = useCallback((target: number) => {
    if (scoreTimer.current) clearInterval(scoreTimer.current)
    const start = displayScoreRef.current
    const t0 = Date.now()
    const dur = 500
    scoreTimer.current = setInterval(() => {
      const t = Math.min(1, (Date.now() - t0) / dur)
      const next = Math.round(start + (target - start) * t)
      displayScoreRef.current = next
      setDisplayScore(next)
      if (t >= 1 && scoreTimer.current) clearInterval(scoreTimer.current)
    }, 25)
  }, [])

  const triggerFocusPulse = useCallback(() => {
    setFocusPulse(true)
    if (pulseTimer.current) clearTimeout(pulseTimer.current)
    pulseTimer.current = setTimeout(() => setFocusPulse(false), 500)
  }, [])

  // 常駐系統永遠先顯示 last-known-good/fallback，再於背景持續刷新。
  useEffect(() => {
    const fallback = buildGalaxyModel(null)
    byIdRef.current = fallback.byId
    setModel(fallback)
    const controllers = new Set<AbortController>()
    const refresh = () => {
      const controller = new AbortController()
      controllers.add(controller)
      void getOverview(controller.signal).then((env) => {
        controllers.delete(controller)
        if (controller.signal.aborted) return
        if (!env.ok) {
          setServiceMonitor((current) => ({ ...current, overview: 'error' }))
          setLoadError(env.error.message)
          return
        }
        const m = buildGalaxyModel(env.data)
        setOverviewRevision(env.data.coins.map((coin) => `${coin.coin}:${coin.generated_at}`).join('|'))
        byIdRef.current = m.byId
        setModel(m)
        setServiceMonitor((current) => ({ ...current, overview: 'ok' }))
        setLoadError(null)
        const initialScore = m.byId[selectedId]?.score ?? 0
        displayScoreRef.current = initialScore
        setDisplayScore(initialScore)
      }).catch(() => {
        setServiceMonitor((current) => ({ ...current, overview: 'error' }))
        setLoadError('overview uplink unavailable')
      })
    }
    refresh()
    const timer = window.setInterval(refresh, 30_000)
    return () => {
      window.clearInterval(timer)
      controllers.forEach((controller) => controller.abort())
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /*
   * 版本與成本同樣背景輪詢；失敗時保留上一筆，不把 HUD 退回 loading。
   */
  useEffect(() => {
    const controllers = new Set<AbortController>()
    const refresh = () => {
      const controller = new AbortController()
      controllers.add(controller)
      void Promise.all([getHealth(controller.signal), getCosts(controller.signal)]).then(([health, costs]) => {
        controllers.delete(controller)
        if (controller.signal.aborted) return
        if (health.ok) setRuntimeVersion(health.data.version)
        if (costs.ok) setCostLedger(costs.data.total_cost_usd)
        setServiceMonitor((current) => ({
          ...current,
          health: health.ok ? 'ok' : 'error',
          costs: costs.ok ? 'ok' : 'error',
        }))
        if (!health.ok || !costs.ok) {
          setLoadError(!health.ok ? health.error.message : !costs.ok ? costs.error.message : 'service uplink unavailable')
        }
      }).catch(() => {
        setServiceMonitor((current) => ({ ...current, health: 'error', costs: 'error' }))
        setLoadError('service uplink unavailable')
      })
    }
    refresh()
    const timer = window.setInterval(refresh, 15_000)
    return () => {
      window.clearInterval(timer)
      controllers.forEach((controller) => controller.abort())
    }
  }, [])

  // Whale Alert 鯨魚動態背景輪詢（30 秒）
  useEffect(() => {
    let active = true
    let timer: number | undefined
    let controller: AbortController | null = null
    const poll = async () => {
      controller = new AbortController()
      const result = await getWhaleSummary(selectedId.toUpperCase(), controller.signal)
      if (active && result.ok) setWhaleSummary(result.data)
      if (active) timer = window.setTimeout(() => void poll(), 30_000)
    }
    void poll()
    return () => { active = false; if (timer !== undefined) window.clearTimeout(timer); controller?.abort() }
  }, [selectedId])

  // 啟動時完整自檢；進入艦橋後持續監控所有唯讀系統通道。
  useEffect(() => {
    const controllers = new Set<AbortController>()
    const inspect = () => {
      const controller = new AbortController()
      controllers.add(controller)
      const checks: Record<string, string> = {
        sources: '/api/status',
      }
      if (activeModule !== 'history') checks.history = '/api/history?coin=BTC&days=30'
      void Promise.all(Object.entries(checks).map(async ([name, url]) => {
        try {
          const response = await fetch(url, { signal: controller.signal, cache: 'no-store', headers: { Accept: 'application/json' } })
          const envelope: unknown = await response.json()
          if (!response.ok || typeof envelope !== 'object' || envelope === null || !('ok' in envelope) || envelope.ok !== true || !('data' in envelope)) {
            return [name, 'error'] as const
          }
          const data = envelope.data
          if (typeof data !== 'object' || data === null) return [name, 'error'] as const
          if (name === 'sources' && 'freshness' in data && typeof data.freshness === 'object' && data.freshness !== null) {
            const freshness = data.freshness as Record<string, unknown>
            const fresh = typeof freshness.fresh === 'number' ? freshness.fresh : 0
            const stale = typeof freshness.stale === 'number' ? freshness.stale : 0
            const missing = typeof freshness.missing === 'number' ? freshness.missing : 0
            if (fresh === 0 && stale === 0 && missing > 0) return [name, 'empty'] as const
            if (stale > 0 || missing > 0) return [name, 'stale'] as const
          }
          if (name === 'history' && 'history' in data && Array.isArray(data.history) && data.history.length === 0) {
            return [name, 'empty'] as const
          }
          return [name, 'ok'] as const
        } catch {
          return [name, 'error'] as const
        }
      })).then((entries) => {
        controllers.delete(controller)
        if (controller.signal.aborted) return
        setServiceMonitor((current) => ({ ...current, ...Object.fromEntries(entries) }))
      })
    }
    inspect()
    const timer = window.setInterval(inspect, activeModule ? 30_000 : 10_000)
    return () => {
      window.clearInterval(timer)
      controllers.forEach((controller) => controller.abort())
    }
  }, [activeModule])

  // ── boot 進場動畫 ──
  useEffect(() => {
    const timers = bootTimers.current
    const stage = (key: keyof typeof boot, delay: number) =>
      timers.push(setTimeout(() => setBoot((b) => ({ ...b, [key]: true })), delay))
    stage('topbar', 0); stage('left', 150); stage('galaxy', 320); stage('right', 620); stage('bottom', 880)
    return () => { timers.forEach(clearTimeout) }
  }, [])

  // ── 切幣 / 階段變化 → 重算分數動畫 ──
  useEffect(() => {
    if (!model) return
    const sel = model.byId[selectedId]
    if (!sel) return
    animateScoreTo(sel.score)
    triggerFocusPulse()
  }, [selectedId, model, animateScoreTo, triggerFocusPulse])

  // ── 打字機動畫：以 buildHermesMessage 的結果為唯一真相來源。
  // buildHermesMessage 依 locale 重新產生，任何導致訊息內容改變的因素
  // （切幣、phase、語言切換）都會讓這個 effect 重跑，避免 typedLen 與
  // 實際渲染字串脫鉤而截斷在半個字。
  useEffect(() => {
    const sel = model.byId[selectedId]
    if (!sel) return
    const full = buildHermesMessage(sel, phase)
    if (typeTimer.current) { clearInterval(typeTimer.current); typeTimer.current = null }
    if (reducedMotion) {
      setTypedLen(full.length)
      return
    }
    setTypedLen(0)
    typeTimer.current = setInterval(() => {
      setTypedLen((prev) => {
        const next = Math.min(full.length, prev + 2)
        if (next >= full.length && typeTimer.current) {
          clearInterval(typeTimer.current)
          typeTimer.current = null
        }
        return next
      })
    }, 18)
    return () => {
      if (typeTimer.current) { clearInterval(typeTimer.current); typeTimer.current = null }
    }
  }, [model, selectedId, phase, buildHermesMessage, reducedMotion])

  useEffect(() => {
    return () => {
      if (typeTimer.current) clearInterval(typeTimer.current)
      if (scoreTimer.current) clearInterval(scoreTimer.current)
      if (pulseTimer.current) clearTimeout(pulseTimer.current)
    }
  }, [])

  useEffect(() => {
    if (!selectedStage) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSelectedStage(null)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [selectedStage])

  const onSubmit = useCallback(() => {
    if (!query.trim()) return
    setPhase('loading')
    setLastOrder(true)
    const search = new URLSearchParams({
      coin: selectedId.toUpperCase(), type: questionType, q: query.trim(),
    })
    search.set('mode', focus)
    // PLAN §7：比較分析要導向雙幣比較頁，不能被當成單幣分析送出去。
    search.set('workspace', questionType === 'comparison' ? 'compare' : 'analyze')
    setSearchParams(search)
    setResubmitSignal((value) => value + 1)
  }, [questionType, focus, query, selectedId, setSearchParams])

  useEffect(() => {
    if (moduleTelemetry) setPhase('ready')
  }, [moduleTelemetry])

  // N2 fix: the effect above only cleared 'loading' on a *successful*
  // telemetry payload. On an analysis error, AnalyzePage never produces
  // telemetry, so `phase` (and thus the left-rail submit button's label/
  // disabled state) stayed stuck on "loading" forever. AnalyzePage now
  // reports its own internal busy state directly regardless of outcome.
  const handleModuleBusyChange = useCallback((busy: boolean) => {
    setPhase(busy ? 'loading' : 'ready')
  }, [])

  const openModule = useCallback((module: HermesWorkspaceModule) => {
    if (activeModule === module) {
      const next = new URLSearchParams(searchParams)
      next.delete('workspace')
      setSearchParams(next)
      return
    }
    // Top-bar navigation opens a clean workspace. Analysis parameters from a
    // previous module must never leak into another module and trigger work.
    const next = new URLSearchParams({ workspace: module, coin: selectedId.toUpperCase() })
    setSearchParams(next)
  }, [activeModule, searchParams, selectedId, setSearchParams])

  const closeModule = useCallback(() => {
    const next = new URLSearchParams(searchParams)
    next.delete('workspace')
    setSearchParams(next)
  }, [searchParams, setSearchParams])

  useEffect(() => {
    setSelectedStage(null)
  }, [activeModule])

  const selCoin = model.byId[selectedId]
  const derivation = deriveSelected(selCoin)
  const telemetryScore = moduleTelemetry?.trustScore == null
    ? null
    : Math.round((moduleTelemetry.trustScore <= 1 ? moduleTelemetry.trustScore * 100 : moduleTelemetry.trustScore))
  const hudCoin: GalaxyCoin = telemetryScore == null ? selCoin : {
    ...selCoin,
    name: moduleTelemetry?.primaryLabel || selCoin.name,
    full: moduleTelemetry?.primaryLabel || selCoin.full,
    score: telemetryScore,
    tier: tierOf(telemetryScore),
  }
  const hudDerivation = deriveSelected(hudCoin)
  const componentColor = (score: number) => score >= 75 ? HERMES_CYAN : score >= 50 ? HERMES_AMBER : HERMES_RED
  const rawComponents = moduleTelemetry?.componentScores
  const hudComponents: TrustComponent[] = rawComponents ? [
    ['Reputation', rawComponents.reputation, COMPONENT_WEIGHTS[0]],
    ['Corroboration', rawComponents.corroboration, COMPONENT_WEIGHTS[1]],
    ['Recency', rawComponents.recency, COMPONENT_WEIGHTS[2]],
    ['Manipulation resistance', rawComponents.resistance, COMPONENT_WEIGHTS[3]],
  ].map(([label, value, weight]) => {
    const score = value == null ? 0 : Math.round(Number(value) * 100)
    return { label: String(label), score, weight: Number(weight), barColor: componentColor(score) }
  }) : derivation.components

  const hermesFull = buildHermesMessage(selCoin, phase)
  const telemetryMessage = telemetryScore == null
    ? null
    : locale === 'zh-TW'
      ? `${hudCoin.full} 本次執行完成。綜合信任分數 ${telemetryScore}/100，${hudCoin.tier === 'healthy' ? t('highTrust') : hudCoin.tier === 'moderate' ? t('moderateTrust') : t('lowTrust')}。右側拆解與下方能量管線已鎖定本次 run。`
      : `${hudCoin.full} run complete. Composite trust score ${telemetryScore}/100. The right breakdown and engine pipeline are locked to this run.`
  const hermesMessage = telemetryMessage ?? hermesFull.slice(0, typedLen)
  const failedServices = Object.entries(serviceMonitor).filter(([, state]) => state === 'error').map(([name]) => name)
  const globalError = failedServices.length ? `${failedServices.join(', ')} uplink unavailable` : loadError

  if (!startupComplete) {
    const bootLabels = locale === 'zh-TW'
      ? ['核心初始化', '快照載入', '工作流掛載', '遙測介面', '艦橋就緒']
      : ['CORE INIT', 'SNAPSHOT LOAD', 'WORKFLOW MOUNT', 'TELEMETRY UI', 'BRIDGE READY']
    return (
      <div className="hermes-startup" role="status" aria-live="polite">
        <div className="hermes-startup-core"><i /><b /></div>
        <strong>TRUSTFORGE HERMES</strong>
        <span>{locale === 'zh-TW' ? '系統啟動與模組載入' : 'SYSTEM STARTUP & MODULE LOAD'}</span>
        <div className="hermes-startup-progress"><i style={{ width: `${(startupStep / 5) * 100}%` }} /></div>
        <small>{startupStep} / 5 · {bootLabels[Math.max(0, startupStep - 1)]}</small>
      </div>
    )
  }

  if (firstRunOpen) return <HermesFirstRun onStart={startFirstRun} onSkip={leaveFirstRun} />

  return (
    <div className={`hermes-root hermes-dashboard${activeModule ? ' is-module-open' : ''}${qaMode ? ' is-qa-mode' : ''}`} style={{ width: '100vw', height: '100dvh', overflow: 'hidden', background: '#02040a' }}>
      <div className="hermes-frame" style={{ width: '100%', height: '100%', position: 'relative', overflow: 'hidden', background: 'radial-gradient(ellipse at 50% 30%,var(--color-hermes-bg-hero) 0%,#02040a 72%)', color: 'var(--color-hermes-tx)', border: '1px solid rgba(140,190,210,.08)', boxShadow: '0 60px 160px rgba(0,0,0,.7)' }}>
        {/* scanline + vignette */}
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 2, background: 'repeating-linear-gradient(rgba(255,255,255,.015) 0px,rgba(255,255,255,.015) 1px,transparent 1px,transparent 3px)' }} />
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 2, boxShadow: 'inset 0 0 160px rgba(0,0,0,.65)' }} />
        {/* corner brackets */}
        <div style={{ position: 'absolute', left: 6, top: 6, width: 34, height: 34, pointerEvents: 'none', zIndex: 11, borderTop: '2px solid rgba(77,216,224,.55)', borderLeft: '2px solid rgba(77,216,224,.55)', boxShadow: '-2px -2px 10px rgba(77,216,224,.2)' }} />
        <div style={{ position: 'absolute', right: 6, top: 6, width: 34, height: 34, pointerEvents: 'none', zIndex: 11, borderTop: '2px solid rgba(232,179,77,.5)', borderRight: '2px solid rgba(232,179,77,.5)', boxShadow: '2px -2px 10px rgba(232,179,77,.18)' }} />
        <div style={{ position: 'absolute', left: 6, bottom: 6, width: 34, height: 34, pointerEvents: 'none', zIndex: 11, borderBottom: '2px solid rgba(232,179,77,.5)', borderLeft: '2px solid rgba(232,179,77,.5)', boxShadow: '-2px 2px 10px rgba(232,179,77,.18)' }} />
        <div style={{ position: 'absolute', right: 6, bottom: 6, width: 34, height: 34, pointerEvents: 'none', zIndex: 11, borderBottom: '2px solid rgba(77,216,224,.55)', borderRight: '2px solid rgba(77,216,224,.55)', boxShadow: '2px 2px 10px rgba(77,216,224,.2)' }} />

        <div className="hermes-boot-layer" style={{ opacity: boot.topbar ? 1 : 0, transition: 'opacity .5s ease-out' }}>
          <HermesTopBar costLedger={costLedger} version={runtimeVersion} degradedMessage={globalError} beginnerMode={beginnerMode} trackedCount={model.coins.length} tierCounts={model.tierCounts} serviceMonitor={serviceMonitor} runtimeStatus={<AgentCoreStatusBadge locale={locale} />} />
        </div>

        <HermesHeroTagline />

        {beginnerMode && !activeModule && <HermesBeginnerNarrative />}


        <div className="hermes-boot-layer" style={{ opacity: boot.left ? 1 : 0, transition: 'opacity .5s ease-out' }}>
          <HermesLeftRail
            hermesMessage={hermesMessage}
            hasOrder={lastOrder}
            focus={focus}
            coin={selectedId.toUpperCase() as 'BTC' | 'ETH' | 'SOL' | 'BNB' | 'XRP'}
            query={query}
            submitLabel={phase === 'loading' ? t('analyzingNow') : t('reAnalyze')}
            onQuery={setQuery}
            onPickCompetitionQuestion={fillCompetitionQuestion}
            onSubmit={onSubmit}
            disabled={!query.trim() || phase === 'loading'}
            questionContext={questionContext}
            onRecallQuestion={setQuery}
            beginnerMode={beginnerMode}
            onChooseIntent={chooseIntent}
            /* N70：頂欄只留顯示，所有可按的都在左軌。 */
            activeModule={activeModule}
            onModuleSelect={openModule}
            onHome={closeModule}
            onBeginnerModeChange={setExperienceMode}
            reducedMotion={reducedMotion}
            onReducedMotionToggle={toggleReducedMotion}
            onHelp={() => setOnboardingOpen(true)}
            onToggleShip={toggleShip}
            diandianAnalyzing={phase === 'loading'}
            onDiandianClick={() => setDiandianOnboardingOpen(true)}
          />
        </div>

        <div
          className={`hermes-boot-layer${activeModule ? ' hermes-galaxy-background' : ''}`}
          data-region="galaxy"
          aria-hidden={activeModule ? true : undefined}
          inert={activeModule ? true : undefined}
          style={{
            opacity: boot.galaxy ? (activeModule ? 0.28 : 1) : 0,
            transition: 'opacity .6s ease-out',
          }}
        >
          <CurrencyGalaxy
            model={model}
            selectedId={selectedId}
            hoveredId={hoveredId}
            focusPulse={focusPulse}
            onSelect={selectCoin}
            onHover={setHoveredId}
          />
        </div>

        {!isRightRailCollapsed && (
          <div className="hermes-boot-layer" style={{ opacity: boot.right ? 1 : 0, transition: 'opacity .5s ease-out' }}>
            <HermesRightRail
              selCoin={hudCoin}
              components={hudComponents}
              displayScore={telemetryScore ?? displayScore}
              derived={!rawComponents}
              flow={analysisFlow}
              journey={analysisJourney}
              crossSignal={moduleTelemetry?.analysis?.report.cross_source_signal}
              derivation={hudDerivation}
              trainingStatus={<TrainingStatusCard />}
              whaleSummary={whaleSummary}
              onOpenComposite={() => setSelectedStage('composite')}
              onOpenDivergence={() => setSelectedStage('divergence')}
            />
          </div>
        )}

        {/* N75：這張卡 z-index 7，工作區面板是 18——模組一開它就整張被蓋住，
            但仍留在 DOM 裡可聚焦、可被輔具讀到（實測 900x760 六個模組全中，
            「跨來源分歧?」與「點擊查看 →」兩顆鈕的點擊點都被面板攔走）。
            前景是工作區時就不該有這個看不見的陷阱，直接不 render。 */}
        {!activeModule && (
          <HermesMobileDivergenceEntry
            derivation={hudDerivation}
            crossSignal={moduleTelemetry?.analysis?.report.cross_source_signal}
            onOpen={() => setSelectedStage('divergence')}
          />
        )}

        <div className="hermes-boot-layer" style={{ opacity: boot.bottom ? 1 : 0, transition: 'opacity .5s ease-out' }}>
          <StageBar flow={analysisFlow} mode={activeModule} telemetry={moduleTelemetry} activity={{ status: phase, coin: selectedId.toUpperCase(), mode: focus, question: query.trim() }} selCoin={hudCoin} derivation={hudDerivation} selectedStage={selectedStage} onSelectStage={(id) => setSelectedStage((s) => (s === id ? null : id))} />
        </div>

        {selectedStage && (
          <>
            <button className="hermes-drilldown-scrim" type="button" aria-label={t('close')} onClick={() => setSelectedStage(null)} />
            {selectedWorkspaceStage
              ? <WorkspaceStageDrilldown detail={selectedWorkspaceStage} onClose={() => setSelectedStage(null)} />
              : <StageDrilldown telemetry={moduleTelemetry} journey={analysisJourney} flow={analysisFlow} selCoin={hudCoin} derivation={hudDerivation} selectedStage={selectedStage} onClose={() => setSelectedStage(null)} />}
          </>
        )}

        {activeModule && <HermesModuleDeck module={activeModule} onClose={closeModule} onTelemetry={setModuleTelemetry} onBusyChange={handleModuleBusyChange} resubmitSignal={resubmitSignal} />}

        {/* N72（CEO：「這把畫面擋住了，而且沒有疊層的感覺，使用者會誤會」）：
            升級控制台原本只有面板、沒有背幕，滿版蓋上去像換頁。補上跟
            StageDrilldown 同一套的背幕（暗化＋點擊關閉），面板也退到左軌
            之後，只蓋右邊工作區。 */}
        {shipOpen && (
          <>
            <button className="hermes-upgrade-scrim" type="button" aria-label={t('close')} onClick={() => setShipOpen(false)} />
            <HermesUpgradeShip data={upgradeData} loading={upgradeLoading} onClose={() => setShipOpen(false)} onRefresh={refreshUpgrades} />
          </>
        )}

        <HermesOnboarding open={onboardingOpen} onClose={() => setOnboardingOpen(false)} />

        {/* 點點助手 — 在左欄面板內部右上角 (#1198) */}
        {/* DiandianAvatar moved into left rail below */}

        {/* 點點新手引導 (#1199) */}
        {diandianOnboardingOpen && (
          <DiandianOnboarding onClose={() => setDiandianOnboardingOpen(false)} />
        )}

        {/* FPS 與自適應品質是使用者判斷動態是否被降級的即時狀態，常駐顯示。
            定位完全交由 hermes.css：桌面固定於右上角空白區域，≤560px
            收成小 badge。 */}
        <FpsMeter
          fps={fps}
          quality={quality}
          measuring={measuring}
          labels={locale === 'zh-TW'
            ? { high: '高畫質', medium: '中畫質', low: '低畫質', detecting: '偵測畫質中…' }
            : { high: 'HIGH', medium: 'MED', low: 'LOW', detecting: 'DETECTING…' }}
        />

      </div>
    </div>
  )
}
