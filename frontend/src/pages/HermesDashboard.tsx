import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import HermesTopBar from '../hermes/HermesTopBar'
import HermesLeftRail from '../hermes/HermesLeftRail'
import HermesRightRail from '../hermes/HermesRightRail'
import CurrencyGalaxy from '../hermes/CurrencyGalaxy'
import StageBar from '../hermes/StageBar'
import StageDrilldown from '../hermes/StageDrilldown'
import { buildGalaxyModel, deriveSelected, type GalaxyCoin, type GalaxyModel } from '../lib/hermesData'
import { getOverview, getCosts, getHealth } from '../lib/endpoints'
import '../hermes/hermes.css'
import { HermesI18nProvider, useHermesI18n } from '../hermes/hermesI18n'

export default function HermesDashboard() {
  return <HermesI18nProvider><HermesDashboardContent /></HermesI18nProvider>
}

function HermesDashboardContent() {
  const { locale, t } = useHermesI18n()
  const qtypes = [t('risk'), t('sentiment'), t('fundamentals'), t('news'), t('catalyst')]
  const [model, setModel] = useState<GalaxyModel | null>(null)
  const [selectedId, setSelectedId] = useState('btc')
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [selectedStage, setSelectedStage] = useState<string | null>(null)
  const [phase, setPhase] = useState<'ready' | 'loading'>('ready')
  const [lastOrder, setLastOrder] = useState(false)
  const [qtype, setQtype] = useState(t('risk'))
  const [query, setQuery] = useState(t('defaultQuery'))
  const [typedLen, setTypedLen] = useState(0)
  const [focusPulse, setFocusPulse] = useState(false)
  const [displayScore, setDisplayScore] = useState(0)
  const [runtimeVersion, setRuntimeVersion] = useState('loading')
  const [costLedger, setCostLedger] = useState<number | null>(null)
  const [scale, setScale] = useState(1)
  const [boot, setBoot] = useState({ topbar: false, left: false, galaxy: false, right: false, bottom: false })
  const [loadError, setLoadError] = useState<string | null>(null)

  const byIdRef = useRef<Record<string, GalaxyCoin>>({})
  const navigate = useNavigate()

  // ── 縮放（設計稿 1440×900 等比縮放置中） ──
  useEffect(() => {
    const compute = () => setScale(Math.min(window.innerWidth / 1440, window.innerHeight / 900, 1))
    compute()
    window.addEventListener('resize', compute)
    return () => window.removeEventListener('resize', compute)
  }, [])

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

  const startTyping = useCallback((sel: GalaxyCoin, ph: 'ready' | 'loading') => {
    if (typeTimer.current) clearInterval(typeTimer.current)
    setTypedLen(0)
    const full = buildHermesMessage(sel, ph)
    typeTimer.current = setInterval(() => {
      setTypedLen((prev) => {
        const next = Math.min(full.length, prev + 2)
        if (next >= full.length && typeTimer.current) clearInterval(typeTimer.current)
        return next
      })
    }, 18)
  }, [buildHermesMessage])

  const animateScoreTo = useCallback((target: number) => {
    if (scoreTimer.current) clearInterval(scoreTimer.current)
    const start = displayScore
    const t0 = Date.now()
    const dur = 500
    scoreTimer.current = setInterval(() => {
      const t = Math.min(1, (Date.now() - t0) / dur)
      setDisplayScore(Math.round(start + (target - start) * t))
      if (t >= 1 && scoreTimer.current) clearInterval(scoreTimer.current)
    }, 25)
  }, [displayScore])

  const triggerFocusPulse = useCallback(() => {
    setFocusPulse(true)
    if (pulseTimer.current) clearTimeout(pulseTimer.current)
    pulseTimer.current = setTimeout(() => setFocusPulse(false), 500)
  }, [])

  // ── 拉 overview，建 galaxy 模型 ──
  useEffect(() => {
    const controller = new AbortController()
    getOverview(controller.signal).then((env) => {
      if (controller.signal.aborted) return
      if (env.ok) {
        const m = buildGalaxyModel(env.data)
        byIdRef.current = m.byId
        setModel(m)
        setDisplayScore(m.byId[selectedId]?.score ?? 0)
      } else {
        // 後端未就緒：回退設計稿預設，畫面照樣成立
        const m = buildGalaxyModel(null)
        byIdRef.current = m.byId
        setModel(m)
        setLoadError(env.error.message)
      }
    }).catch(() => {
      const m = buildGalaxyModel(null)
      byIdRef.current = m.byId
      setModel(m)
    })
    return () => controller.abort()
  // overview 是一次性快照；切換焦點只讀本地 model，不重打 API/rate limit。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Top bar 不顯示設計稿假值：版本與累計成本都讀正式 API。
  useEffect(() => {
    const controller = new AbortController()
    Promise.all([getHealth(controller.signal), getCosts(controller.signal)]).then(([health, costs]) => {
      if (controller.signal.aborted) return
      setRuntimeVersion(health.ok ? health.data.version : 'unavailable')
      if (costs.ok) setCostLedger(costs.data.total_cost_usd)
    })
    return () => controller.abort()
  }, [])

  // ── boot 進場動畫 ──
  useEffect(() => {
    const timers = bootTimers.current
    const stage = (key: keyof typeof boot, delay: number) =>
      timers.push(setTimeout(() => setBoot((b) => ({ ...b, [key]: true })), delay))
    stage('topbar', 0); stage('left', 150); stage('galaxy', 320); stage('right', 620); stage('bottom', 880)
    timers.push(setTimeout(() => {
      if (byIdRef.current[selectedId]) startTyping(byIdRef.current[selectedId], phase)
    }, 1150))
    return () => { timers.forEach(clearTimeout) }
  }, [startTyping, phase, selectedId])

  // ── 切幣 / 階段變化 → 重算分數動畫 + typing ──
  useEffect(() => {
    if (!model) return
    const sel = model.byId[selectedId]
    if (!sel) return
    animateScoreTo(sel.score)
    triggerFocusPulse()
    startTyping(sel, phase)
  }, [selectedId, model, phase, animateScoreTo, triggerFocusPulse, startTyping])

  useEffect(() => {
    return () => {
      if (typeTimer.current) clearInterval(typeTimer.current)
      if (scoreTimer.current) clearInterval(scoreTimer.current)
      if (pulseTimer.current) clearTimeout(pulseTimer.current)
    }
  }, [])

  const onSubmit = useCallback(() => {
    if (!query.trim()) return
    setPhase('loading')
    setLastOrder(true)
    const type = qtype === t('fundamentals') ? 'hypothesis' : 'multi_source'
    const search = new URLSearchParams({
      coin: selectedId.toUpperCase(), type, q: query.trim(),
    })
    navigate(`/analyze?${search.toString()}`)
  }, [navigate, qtype, query, selectedId, t])

  if (!model) {
    return (
      <div className="hermes-root" style={{ width: '100vw', height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#02040a', color: 'var(--color-hermes-tx2)' }}>
        {t('initializing')}
      </div>
    )
  }

  const selCoin = model.byId[selectedId]
  const derivation = deriveSelected(selCoin)

  const hermesFull = buildHermesMessage(selCoin, phase)
  const hermesMessage = hermesFull.slice(0, typedLen)

  return (
    <div className="hermes-root hermes-dashboard" style={{ width: '100vw', height: '100vh', overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#02040a' }}>
      <div className="hermes-frame" style={{ width: 1440, height: 900, flexShrink: 0, position: 'relative', overflow: 'hidden', background: 'radial-gradient(ellipse at 50% 30%,#0b1420 0%,#02040a 72%)', color: 'var(--color-hermes-tx)', border: '1px solid rgba(140,190,210,.08)', boxShadow: '0 60px 160px rgba(0,0,0,.7)', transform: `scale(${scale})`, transformOrigin: 'center center' }}>
        {/* scanline + vignette */}
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 2, background: 'repeating-linear-gradient(rgba(255,255,255,.015) 0px,rgba(255,255,255,.015) 1px,transparent 1px,transparent 3px)' }} />
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 2, boxShadow: 'inset 0 0 160px rgba(0,0,0,.65)' }} />
        {/* corner brackets */}
        <div style={{ position: 'absolute', left: 6, top: 6, width: 34, height: 34, pointerEvents: 'none', zIndex: 11, borderTop: '2px solid rgba(77,216,224,.55)', borderLeft: '2px solid rgba(77,216,224,.55)', boxShadow: '-2px -2px 10px rgba(77,216,224,.2)' }} />
        <div style={{ position: 'absolute', right: 6, top: 6, width: 34, height: 34, pointerEvents: 'none', zIndex: 11, borderTop: '2px solid rgba(232,179,77,.5)', borderRight: '2px solid rgba(232,179,77,.5)', boxShadow: '2px -2px 10px rgba(232,179,77,.18)' }} />
        <div style={{ position: 'absolute', left: 6, bottom: 6, width: 34, height: 34, pointerEvents: 'none', zIndex: 11, borderBottom: '2px solid rgba(232,179,77,.5)', borderLeft: '2px solid rgba(232,179,77,.5)', boxShadow: '-2px 2px 10px rgba(232,179,77,.18)' }} />
        <div style={{ position: 'absolute', right: 6, bottom: 6, width: 34, height: 34, pointerEvents: 'none', zIndex: 11, borderBottom: '2px solid rgba(77,216,224,.55)', borderRight: '2px solid rgba(77,216,224,.55)', boxShadow: '2px 2px 10px rgba(77,216,224,.2)' }} />

        <div style={{ opacity: boot.topbar ? 1 : 0, transition: 'opacity .5s ease-out' }}>
          <HermesTopBar costLedger={costLedger} version={`${runtimeVersion} · ${t('galaxy')}`} />
        </div>

        <div style={{ opacity: boot.left ? 1 : 0, clipPath: boot.left ? 'inset(0 0 0% 0)' : 'inset(0 0 100% 0)', transition: 'opacity .5s ease-out, clip-path .5s ease-out' }}>
          <HermesLeftRail
            model={model}
            hermesMessage={hermesMessage}
            hasOrder={lastOrder}
            qtype={qtype}
            qtypes={qtypes}
            query={query}
            submitLabel={phase === 'loading' ? t('transmitting') : t('transmit')}
            onType={setQtype}
            onQuery={setQuery}
            onSubmit={onSubmit}
            disabled={!query.trim() || phase === 'loading'}
          />
        </div>

        <div style={{ opacity: boot.galaxy ? 1 : 0, transition: 'opacity .6s ease-out' }}>
          <CurrencyGalaxy
            model={model}
            selectedId={selectedId}
            hoveredId={hoveredId}
            focusPulse={focusPulse}
            onSelect={setSelectedId}
            onHover={setHoveredId}
          />
        </div>

        <div style={{ opacity: boot.right ? 1 : 0, clipPath: boot.right ? 'inset(0 0 0% 0)' : 'inset(0 0 100% 0)', transition: 'opacity .5s ease-out, clip-path .5s ease-out' }}>
          <HermesRightRail
            selCoin={selCoin}
            components={derivation.components}
            derived
            derivation={derivation}
            onOpenComposite={() => setSelectedStage('composite')}
            onOpenDivergence={() => setSelectedStage('divergence')}
          />
        </div>

        <div style={{ opacity: boot.bottom ? 1 : 0, transition: 'opacity .5s ease-out' }}>
          <StageBar selCoin={selCoin} derivation={derivation} selectedStage={selectedStage} onSelectStage={(id) => setSelectedStage((s) => (s === id ? null : id))} />
        </div>

        {selectedStage && (
          <StageDrilldown
            selCoin={selCoin}
            derivation={derivation}
            selectedStage={selectedStage}
            onClose={() => setSelectedStage(null)}
          />
        )}

        {loadError && (
          <div style={{ position: 'absolute', left: 50, top: 50, zIndex: 30, fontSize: 10, color: 'var(--color-hermes-amber)', background: 'rgba(232,179,77,.13)', border: '1px solid rgba(232,179,77,.4)', borderRadius: 6, padding: '6px 10px' }}>
            ⚠ {t('degraded')} ({loadError})
          </div>
        )}
      </div>
    </div>
  )
}
