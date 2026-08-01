import { useEffect, useRef } from 'react'
import { modeLabel, useHermesI18n } from './hermesI18n'
import type { AnalysisQuestionContext } from '../lib/endpoints'
import { BEGINNER_INTENTS, type AnalysisModeId } from '../lib/beginnerExperience'
import type { AnalysisFocusId } from '../lib/analysisTaxonomy'
import type { HermesWorkspaceModule } from './HermesModuleDeck'
import { pickCompetitionQuestion, type RandomSource } from '../lib/competitionQuestionPicker'
import type { CoinSymbol } from '../lib/constants'

/** N70（CEO：「把選單功能放到最左邊」「能按的都移到左邊欄」）：
 *  頂欄過去同時承載顯示與操作，一般使用者掃不出哪些能按。導覽與四個開關
 *  全部搬進這裡的 `hermes-rail-controls`；頂欄只留顯示（見 HermesTopBar）。
 *  市場遙測則反向搬上頂欄，做成點擊展開的摘要膠囊。
 *
 *  ⚠️ 手機注意：`hermes.css` 的 `@media (max-width:560px)` 原本對
 *  `[data-region='left-rail']` 下 `display:none !important`——照舊的話所有搬進來
 *  的控制項在 ≤560px 會整組消失。N70 改成 `display:contents` 讓
 *  `.hermes-rail-controls` 穿透出來、定位成頂欄下方可橫向捲動的固定條
 *  （見 hermes.css N70 節，高度已折進 `--hermes-top`）。
 *  改這裡之前先確認手機路徑還在。 */
interface HermesLeftRailProps {
  hermesMessage: string
  hasOrder: boolean
  /** N70：分析角度。**已不是使用者的選擇**——由 HermesDashboard 從題目文字
   *  推導（`recommendAnalysisMode`），`?mode=` 深連結優先。這裡只拿來顯示
   *  「本次用哪個角度」。它仍決定送給後端的 question_type（對應表
   *  `defaultQuestionTypeForFocus` 與後端 `analysis_flow.MODES` 同源，
   *  由 analysisTaxonomy.test.ts 綁住）。存 id 不存翻譯後的 label。 */
  focus: AnalysisFocusId
  query: string
  coin: CoinSymbol
  submitLabel: string
  disabled?: boolean
  /** N70：從頂欄搬下來的操作項。 */
  activeModule?: HermesWorkspaceModule | null
  onModuleSelect?: (id: HermesWorkspaceModule) => void
  onHome?: () => void
  onBeginnerModeChange?: (v: boolean) => void
  reducedMotion?: boolean
  onReducedMotionToggle?: () => void
  onHelp?: () => void
  onToggleShip?: () => void
  questionContext?: AnalysisQuestionContext | null
  onRecallQuestion?: (question: string) => void
  onQuery: (v: string) => void
  onPickCompetitionQuestion?: (v: string) => void
  onSubmit: () => void
  beginnerMode?: boolean
  onChooseIntent?: (mode: AnalysisModeId, question: string) => void
  random?: RandomSource
}

// N70：兩顆下拉（官方題型 N69／分析角度 N70）都移除後，共用的 SELECT_* 樣式
// 常數也一併刪掉——留著會讓人以為左軌還有下拉。

export default function HermesLeftRail({
  hermesMessage, hasOrder, focus, query, coin, submitLabel,
  onQuery, onPickCompetitionQuestion, onSubmit, disabled = false,
  questionContext = null, onRecallQuestion,
  beginnerMode = false, onChooseIntent,
  activeModule = null, onModuleSelect, onHome, onBeginnerModeChange,
  reducedMotion = false, onReducedMotionToggle, onHelp, onToggleShip,
  random = Math.random,
}: HermesLeftRailProps) {
  const { t, locale } = useHermesI18n()
  // N70：從 HermesTopBar 原封搬過來（含 description，nav 的 tooltip/無障礙說明
  // 都靠它），只換了容器。
  const navItems = [
    { id: 'analyze' as const, label: t('analyze'), description: locale === 'zh-TW' ? '找出風險、原因與可追溯證據' : 'Find risks, reasons, and traceable evidence' },
    { id: 'compare' as const, label: t('compare'), description: locale === 'zh-TW' ? '並排比較兩個資產的可信狀態' : 'Compare two assets side by side' },
    { id: 'history' as const, label: t('history'), description: locale === 'zh-TW' ? '查看信任與資料完整度如何變化' : 'Review trust and completeness over time' },
    { id: 'status' as const, label: t('sources'), description: locale === 'zh-TW' ? '確認資料來源是否正常更新' : 'Check whether sources are updating' },
    { id: 'costs' as const, label: t('costs'), description: locale === 'zh-TW' ? '查看分析使用量與模型費用' : 'Review analysis usage and model cost' },
  ]
  // N42: 訊息串永遠停在 scrollTop 0，最新一則被切在容器下緣——實測 14 組
  // （2 locale × 7 視窗）全部 `scrollTop: 0`，而 scrollHeight 最高 1403、
  // clientHeight 只有 140。也就是說使用者從來看不到 HERMES 剛剛回了什麼，
  // 得自己往下捲。沒有任何 agent 介面是這樣的，這正是「隨便一個 ai agent
  // 都比這個強」的核心。新訊息進來就釘到底，跟一般聊天介面一致。
  // N76（CEO：「這很重要怎麼跑下去了」「左邊選單正常要縮吧 怎麼會卡列」）：
  // <1280px 兩個 pane 垂直堆疊。原本選單 pane 是 `flex: 1 1 auto`，會長去吃掉
  // 剩餘空間（實測 474~629px），對話 pane 沒有 flex 宣告只剩 200px，而它的
  // 內容有 326px——輸入框就被擠到對話區自己的捲軸下面，看起來像「跑下去」。
  // 修法全在 CSS：選單 `0 1 auto`（只縮不長）、對話區 `1 1 auto` + min-height
  // 保底。N73 那顆手動收合鈕已移除：它長得像下拉選單又固定佔一列，
  // 使用者看不出是什麼，而且選單本來就該自己縮，不該要人去按。

  const transcriptRef = useRef<HTMLDivElement>(null)
  const transcriptDeps = `${questionContext?.conversation?.length ?? 0}|${hermesMessage}`
  useEffect(() => {
    const el = transcriptRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [transcriptDeps])
  return (
    <div
      className="hermes-glass hermes-rail-split"
      data-region="left-rail"
      style={{
        position: 'absolute', left: 0, top: 'var(--hermes-top)', width: 'var(--hermes-rail)',
        /* N44: N41 把底部管線條的左緣縮到軌道右緣之後，軌道底下（原本被管線
           條蓋住的那條 `--hermes-bottom` 高度）就空出一塊純黑矩形——洞是我
           自己開的。修法不是把管線條還原成橫跨全寬（那又回到「管線壓在互動
           區底下」），而是讓 AI agent 互動區真的佔滿整條左側：高度從
           `calc(100% - top - bottom)` 改成 `calc(100% - top)`，貼到畫面底緣。
           管線條左緣吃 `--hermes-rail`，兩者水平不重疊，z-index 也無關。
           附帶好處：軌道多出 94~120px，composer 不再被擠在最下面。 */
        height: 'calc(100% - var(--hermes-top))', zIndex: 5,
        borderRight: '1px solid var(--color-hermes-bd)', padding: '14px 16px',
        /* display/flex-direction/gap 交給 `.hermes-rail-split`（hermes.css）：
           inline style 會贏過 class，斷點切不動，所以這三個屬性必須留在 CSS。 */
        overflowY: 'auto',
      }}
    >
      {/* N49（四格版面）：老闆原話「左中右 改為 左改為兩區 … 在最左邊多一堆
          選單功能 你要放什麼都放到最左邊 選單往右 保留 hermes 的對話 跟 使用者的
          輸入框 在右邊才是主要的 3D 全息投影區」。左軌本來把遙測、我想做什麼、
          相似歷史、分析模式、模式建議跟對話串＋輸入框全部疊在同一欄，所以
          「塞在一起、跟 hermes 對話卡死真的很煩人」。這裡把左軌切成兩個 pane：
          menu pane 收所有選單/設定/歷史，chat pane 只留 HERMES 對話與輸入框。
          兩者水平並排還是垂直堆疊由 CSS 一個 flex-direction 決定（見 hermes.css
          `.hermes-rail-split`）：≥1280px 並排成四格，窄螢幕自動退回今天已驗證
          的單欄堆疊。左側總寬仍然只由 `--hermes-rail` 表示，所以中間全息區、
          底部管線條、右軌那些 `left: var(--hermes-rail)` 的規則一行都不用改。 */}
      <div className="hermes-rail-menu">
      {/* N70 控制區：原本散在頂欄右半邊的所有可按項目。市場遙測（原本佔這個
          位置）反向搬到頂欄做成點擊展開的膠囊——它是狀態顯示，不是操作。 */}
      <nav className="hermes-rail-controls" aria-label={t('navigation')}>
        {/* N71（CEO：「左邊選單 不像選單 再想想辦法」）：搬進來的東西是對的，
            但這一區沒有任何選單的樣子——沒有分組標題、項目是無框透明文字、
            hover 沒反應、選中只有一層極淡底色，下面五顆開關又是三種不同外觀的
            9px 小膠囊，掃過去像散落的標籤。這裡補上兩個分組標題（功能／設定），
            CSS 端把兩組都排成等節奏的整列列項、選中列加左側指示條。
            ⚠️ ≤560px 這一區會被抽成頂欄下的橫向控制條，標題在那裡是純浪費寬度，
            已在 hermes.css 的 N70/N71 手機段落 `display:none`。 */}
        <p className="hermes-rail-group">{t('railGroupNav')}</p>
        <button type="button" className="hermes-nav-item" onClick={onHome} aria-pressed={activeModule === null}>
          {t('homeAria')}
        </button>
        {/* N70（CEO：「使用者要按要點的功能統一到最左邊的選單欄中」）：
            這裡原本在新手模式只留「分析」。導覽被藏起來的結果是新手模式沒有
            比較的入口——角度已改由題目推導，而推導器不會產生 comparison。
            導覽是「有哪些功能」，不是進階選項，五項一律顯示；新手模式該減的
            是版面密度，不是功能的存在。 */}
        {navItems.map((item) => (
          <button
            type="button"
            key={item.id}
            className="hermes-nav-item"
            onClick={() => onModuleSelect?.(item.id)}
            aria-pressed={activeModule === item.id}
            aria-description={item.description}
            data-description={item.description}
          >
            {item.label}
          </button>
        ))}
        <div className="hermes-rail-controls-sep" role="separator" />
        <p className="hermes-rail-group">{t('railGroupSettings')}</p>
        <a href="/goals" className="hermes-nav-item" style={{ textDecoration: 'none' }}>
          {locale === 'zh-TW' ? '🎯 專案目標' : '🎯 Goals'}
        </a>
        <button type="button" className="hermes-mode-toggle" onClick={() => onBeginnerModeChange?.(!beginnerMode)} aria-pressed={beginnerMode}>
          {beginnerMode ? t('beginnerModeOn') : t('beginnerModeOff')}
        </button>
        {/* N62：這顆按鈕原本掛 aria-label（「啟用低動態模式」），而 aria-label 是
            「取代」不是「補充」可見文字——螢幕閱讀器唸到的名稱跟眼睛看到的
            「動態」對不上，語音操作使用者照著唸也點不到（WCAG 2.5.3 Label in
            Name）。狀態交給 aria-pressed，動作提示留在 title。 */}
        <button type="button" className="hermes-mode-toggle" onClick={onReducedMotionToggle} aria-pressed={reducedMotion} title={reducedMotion ? t('reducedMotionOnTitle') : t('reducedMotionOffTitle')}>
          {reducedMotion ? t('dynamicOff') : t('dynamicOn')}
        </button>
        <button type="button" className="hermes-help-toggle" onClick={onHelp} aria-label={t('openBeginnerHelp')}>{t('helpToggle')}</button>
        {/* N70（CEO：「一樣改到左邊選單，上方只留狀態」）：艦體升級原本只在完整
            模式出現。跟導覽同一個道理——能按的一律留在左軌，新手模式減的是
            版面密度不是功能的存在，所以拿掉 `!beginnerMode` 這道門。 */}
        <button type="button" className="hermes-ship-toggle" onClick={onToggleShip}>{t('shipToggle')}</button>
        {/* N72（CEO：「中文 英文 放右上，不要拿到左邊很怪」）：語言切換不是本產品
            的功能，是整個介面的全域偏好——跟導覽、模式開關擺在一起，會讓人以為
            它跟「分析什麼」同一層。這顆已搬到頂欄最右（`HermesTopBar`）。
            ⚠️ 頂欄「只留狀態」的規則對它有例外：語言是慣例位置（右上），
            這是 CEO 直接指定，不要又搬回左軌。 */}
      </nav>
        {/* N46: 這張「我想做什麼？」卡片預設攤開，五張 intent 卡（每張都是
            標題＋說明兩行）在 zh-TW 就吃掉 300px 以上，把 HERMES 對話串和
            輸入框一路推到畫面底部——老闆原話「這設計根本有問題 排盤很有問題」
            「最多多一個按鈕 做個彈出」。改成預設闔起的 <details>：平常只佔
            一行（summary 就是那顆按鈕），要用才展開。五張卡片仍在 DOM 裡，
            展開即用，不動 i18n。
            N78：基礎規則原本硬寫 `1fr 1fr`，≥1280px 左軌拆出獨立選單欄後
            每顆鈕只剩 55px、標題被壓成 3 行，已改成
            `repeat(auto-fit, minmax(118px, 1fr))` 由容器決定欄數；
            窄視窗 @media 的 `1fr !important` 覆寫照舊生效。 */}
        {beginnerMode && (
          <details className="hermes-intent-picker">
            <summary className="hermes-intent-title" style={{ cursor: 'pointer', minHeight: 24, display: 'flex', alignItems: 'center' }}>{t('whatToDo')}</summary>
            <p>{t('chooseGoal')}</p>
            <div>
              {BEGINNER_INTENTS.map((intent) => (
                <button key={intent.id} type="button" onClick={() => onChooseIntent?.(intent.mode, intent.question)} title={t(intent.descriptionKey)}>
                  <b>{t(intent.labelKey)}</b><span>{t(intent.descriptionKey)}</span>
                </button>
              ))}
            </div>
          </details>
        )}
        {!!questionContext && (
          <details style={{ flexShrink: 0, marginBottom: 10, borderTop: '1px solid var(--color-hermes-bd)', paddingTop: 7 }}>
            {/* N47: 這行原本掛 `<GlossaryTerm term="rag">`，那顆「?」開出來的是
                280x81 的 `position: fixed` 彈窗——在只有 230px 寬的左軌裡，它整塊
                蓋在下面的歷史清單與「分析模式」上，老闆原話「那個問號按了畫面破掉」。
                名詞解釋在寬面板裡沒問題，在 agent 互動軌道裡是純干擾，這裡改回
                純文字標題。GlossaryTerm 元件本身與其他頁面的用法都不動。 */}
            <summary style={{ fontSize: 9, letterSpacing: 1, color: 'var(--color-hermes-cyan)', cursor: 'pointer', minHeight: 24, display: 'flex', alignItems: 'center' }}>
              {t('similarQuestions')}
            </summary>
            <div role="note" style={{ fontSize: 9.5, lineHeight: 1.35, color: 'var(--color-hermes-amber)', margin: '5px 0' }}>
              {t('historyDisclaimer')}
            </div>
            {/* N48: 展開這塊時實測輸入框被往下推 111px、底緣 719 掉出 700 高的
                視窗——三筆歷史每筆都是「百分比 · 幣別/模式 · 整句題目」會折成兩三行，
                加起來 219px，在左軌這種垂直預算裡是奢侈品。清單自己吃捲軸、
                高度封頂 108px（約兩筆半，看得出還有下文），展開的代價因此有上限，
                不會再把 composer 擠出畫面。三筆資料一筆都沒少。 */}
            <div style={{ maxHeight: 108, overflowY: 'auto' }}>
            {questionContext.matches.length ? questionContext.matches.slice(0, 3).map((match) => (
              <button key={match.question_id} type="button" onClick={() => onRecallQuestion?.(match.question)} title={match.answer ?? '尚無完成快照'}
                style={{ width: '100%', textAlign: 'left', background: 'transparent', border: 0, borderBottom: '1px solid var(--color-hermes-bd)', color: 'var(--color-hermes-tx2)', font: 'inherit', fontSize: 11, lineHeight: 1.4, padding: '7px 2px', minHeight: 24, cursor: 'pointer' }}>
                <b style={{ color: 'var(--color-hermes-amber)' }}>{Math.round(match.similarity * 100)}%</b> · {match.coin}/{modeLabel(match.mode, t)} · {match.question}
              </button>
            )) : (
              <div style={{ fontSize: 11, lineHeight: 1.4, color: 'var(--color-hermes-tx3)', padding: '5px 2px' }}>{t('noSimilarQuestions')}</div>
            )}
            </div>
          </details>
        )}
        {/* N69（CEO 回報「這東西是比賽方的範例 不應該給使用者選」）：
            這裡原本有一顆「官方題型」下拉（多源整合／假設驗證／比較分析），
            現已移除。理由不是排版，是它在陳述一件不成立的事——
            `docs/competition/COMPETITION-OFFICIAL.md` 那一節的標題就是
            **「範例題型」**，同一份文件並明寫「比賽當日從題目池抽 1 題 + 指定
            幣種，現場公布，無法預知」。三種是主辦方舉的例子，不是可出題的範圍；
            做成三選一等於把示例當成限制，現場抽到範圍外的題目時使用者會卡住。
            真正的輸入是下面那個自由題目輸入框（後端 `register_question` 收
            1..1000 字任意字串，本來就吃得下）。
            question_type 沒有消失、只是不再要使用者猜：改由分析角度推導
            （`defaultQuestionTypeForFocus`，與後端 `analysis_flow.MODES` 同一套
            對應——fundamentals/catalyst → hypothesis，其餘 → multi_source）。
            「比較分析」本來就有 /compare 專頁且已在主導覽，不需要在這裡再開一個入口。
            ⚠️ 要加回來之前請先確認官方文件已改成「限這三種」——目前不是。 */}
        {/* N70（CEO：「分析角度 也不給使用者選」）：這裡原本是 `#hermes-focus`
            五選一下拉。移除的理由跟 N69 的題型下拉是同一條——它要求使用者先讀懂
            五個金融名詞才能開始，而在手動分析路徑上它實質只決定後端的
            `question_type` 落在 multi_source 還是 hypothesis
            （`analysis_flow.py` MODES：fundamentals/catalyst → HYPOTHESIS，
            其餘 → MULTI_SOURCE；模板只有排程 `enqueue_matrix` 會用到，
            `register_question` 收的是使用者自由文字）。要一般使用者去猜這個
            二選一，是把系統的內部維度當成使用者的選擇題。
            改由題目文字推導（`recommendAnalysisMode`，純函式、零延遲、已有測試），
            這裡只做唯讀說明——保留透明度，但不再是選擇題。
            ⚠️ `?mode=` 深連結仍然優先於推導（見 HermesDashboard），所以既有連結
            與排程完全不受影響；不要為了「還原選單」而把 URL 那條路也拔掉。 */}
        <div className="hermes-focus-derived">
          {t('derivedFocusPrefix')}{modeLabel(focus, t)}{t('derivedFocusSuffix')}
        </div>

        {beginnerMode && <div className="hermes-analysis-expectation">{t('analysisExpectationPrefix')}{modeLabel(focus, t)}{t('analysisExpectationSuffix')}</div>}
      </div>

      {/* HERMES CONSOLE：只剩對話串與輸入框，這才是 agent 互動區。 */}
      <div
        className="hermes-clip hermes-rail-chat"
        /* N37: `minHeight: 0` 明確允許這塊被壓到比內容還矮，而左軌高度是
           `calc(100% - top - bottom)`，視窗一矮就沒空間分。實測 1024x420 時
           這個 .hermes-clip clientHeight 只剩 28px（scrollHeight 222），
           使用者看不到 HERMES 主控台的任何內容。改成 200px 樓地板：左軌自己
           已經是 overflow-y:auto，空間不夠時讓左軌滾，而不是把面板壓扁。
           N76（CEO：「這很重要怎麼跑下去了」）：樓地板從 200 提到 300。
           200 是照「稽核腳本可讀性門檻」訂的，只保證看得到「內容」，
           沒保證看得到「輸入框」——實測內容 326px 塞進 200px 的框，
           composer 就被擠到這個框自己的捲軸下面，畫面上等同輸入不見了。
           300 是量出來的：標頭 + 一則訊息 + composer 剛好露出。
           空間真的不夠時左軌自己是 overflow-y:auto，讓左軌滾，不壓扁這裡。 */
        /* N76 第二刀：`overflowY` 從 'auto' 改成 'hidden'。
           原本這個 pane 自己會捲，於是標頭＋訊息串＋composer 一超過 pane 高度
           就整塊一起捲走——composer 被捲到 pane 的捲軸下面，畫面上等同輸入不見
           了（實測 1279x900 / 1024x600 / 900x560：pane 剛好卡在 300px 樓地板、
           內容 339~347px）。訂樓地板追不上這個問題，因為內容長度本來就會變，
           上面那段「300 是量出來的」只是把同一個 bug 推到更窄的視窗才發作。
           正解是一般 agent 介面的配置：pane 不捲，訊息串（flex:1）自己捲，
           composer `flexShrink:0` 永遠釘在底部。
           ⚠️ 光改這裡不夠，實測過：pane 改 hidden 之後 6 個尺寸照樣 RED
           （對話區 clientHeight 298、內容 347），因為訊息串自己有
           `minHeight: 140` 的樓地板，撐住不縮，composer 就被推到 hidden 的
           裁切線外——比原本更糟（原本至少還捲得到）。兩處要一起改，見下方
           訊息串的 `minHeight: 0`。 */
        style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 300, overflowY: 'hidden', background: 'rgba(13,20,30,.6)', border: '1px solid var(--color-hermes-bd)', borderRadius: 8, padding: 14, boxShadow: 'inset 0 0 24px rgba(77,216,224,.04)' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
          <div style={{ position: 'relative', width: 24, height: 24, flexShrink: 0, animation: 'hermes-hermes-breathe 3.2s ease-in-out infinite' }}>
            <div style={{ position: 'absolute', inset: 0, clipPath: 'polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%)', border: '1.5px solid var(--color-hermes-amber)', animation: 'hermes-orbit-spin 9s linear infinite' }} />
            <div style={{ position: 'absolute', inset: 5, clipPath: 'polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%)', background: 'var(--color-hermes-amber)', opacity: 0.35, animation: 'hermes-orbit-spin-rev 5s linear infinite' }} />
          </div>
          <span style={{ fontSize: 11, letterSpacing: '1.2px', color: 'var(--color-hermes-tx)' }}>HERMES</span>
          <span style={{ fontSize: 9, color: 'var(--color-hermes-cyan)', background: 'rgba(77,216,224,.13)', border: '1px solid rgba(77,216,224,.4)', borderRadius: 3, padding: '1px 6px' }}>{t('online')}</span>
        </div>
        {/* N40: 這塊原本不是「對話」，是三種東西疊在一起——一張 amber 的
            agentOutput 卡（最新回覆）、一張標題寫「對話記憶」而內容是
            `HERMES › …` 前綴純文字的 10.5px 小卡（只留最後 3 則），再加一段
            RAG 相似問題清單。三者用同一個捲動容器，所以歷史訊息、最新回覆、
            系統建議在視覺上分不出誰是誰，也看不出誰先誰後。老闆的原話是
            「隨便一個 ai agent 都比這個強」，這是對的：沒有任何對話介面長這樣。

            改成一般 agent 介面的三段式：
              transcript  單一時間序訊息串，靠左右對齊分角色（使用者靠右、
                          HERMES 靠左），不再用 `HERMES ›` 前綴。字級
                          10.5→12.5，並且不再 `.slice(-3)`——訊息串本來就會捲，
                          砍掉歷史沒有理由。
              suggestions RAG 相似問題移出訊息串，收進預設闔起的 <details>。
                          那是系統建議、不是對話內容，混在一起就是雜訊。
              composer    模式選單＋輸入框＋送出鍵包成一塊，flexShrink:0
                          釘在底部（見下方 N40 composer）。
            角色標籤只在 HERMES 一側顯示品牌字 `HERMES`；使用者一側靠右對齊
            即可辨識，不加字就不必新增 i18n key（少一處可能漏翻的地方）。 */}
        <div
          ref={transcriptRef}
          aria-label={t('agentOutput')}
          /* N76：`minHeight` 從 140 改成 0。訊息串是 pane 裡唯一該被壓縮的
             東西——它自己就是 `overflowY: auto`，壓短了只是少露幾行、捲一下
             就有；composer 被擠掉卻是功能不見。140 的樓地板讓它壓不下去，
             於是 pane（已改 hidden）把 composer 裁掉，實測 960x800 /
             1024x900 / 1100x950 / 1279x900 / 1024x600 / 900x560 六個尺寸
             全 RED。0 之後 flex:1 才真的能收縮，composer 永遠留在畫面上。 */
          style={{ flex: 1, minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 10, paddingRight: 2 }}
        >
          {(() => {
            const history = (questionContext?.conversation ?? [])
              .filter((msg, i, arr) => i === 0 || msg.role !== arr[i - 1].role || msg.content !== arr[i - 1].content)
            const bubbles = history.map((msg) => ({ key: msg.message_id, role: msg.role, text: msg.content, latest: false }))
            // 最新回覆有可能已經是 conversation 的最後一則（後端回寫），重複
            // 顯示同一句話會讓人以為 HERMES 說了兩次，所以只在不同時才補上。
            const lastHermes = [...history].reverse().find((m) => m.role === 'hermes')
            if (hermesMessage && lastHermes?.content !== hermesMessage) {
              bubbles.push({ key: 'latest', role: 'hermes', text: hermesMessage, latest: true })
            } else if (bubbles.length) {
              bubbles[bubbles.length - 1].latest = bubbles[bubbles.length - 1].role === 'hermes'
            }
            // 還沒有任何往返時，把待送出的指令當成使用者的第一顆泡泡，讓畫面
            // 一開始就是對話的樣子，而不是一行 `> risk: …` 的 log。
            if (!bubbles.length && hasOrder) bubbles.push({ key: 'pending', role: 'user', text: query || modeLabel(focus, t), latest: false })
            return bubbles.map((b) => {
              const mine = b.role !== 'hermes'
              return (
                <div key={b.key} style={{ alignSelf: mine ? 'flex-end' : 'flex-start', maxWidth: '92%', flexShrink: 0 }}>
                  {!mine && <div style={{ fontSize: 9, letterSpacing: 1, color: b.latest ? 'var(--color-hermes-amber)' : 'var(--color-hermes-tx3)', marginBottom: 3 }}>HERMES</div>}
                  <div
                    style={{
                      background: mine ? 'rgba(40,64,90,.35)' : 'var(--color-hermes-inset)',
                      border: `1px solid ${b.latest ? 'var(--color-hermes-amber)' : mine ? 'var(--color-hermes-bd2)' : 'var(--color-hermes-bd)'}`,
                      borderRadius: mine ? '8px 8px 2px 8px' : '2px 8px 8px 8px',
                      padding: '9px 11px',
                      fontSize: 12.5,
                      lineHeight: 1.6,
                      color: mine ? 'var(--color-hermes-tx2)' : 'var(--color-hermes-tx)',
                      overflowWrap: 'anywhere',
                    }}
                  >
                    {b.text}
                  </div>
                </div>
              )
            })
          })()}
        </div>
        <div className="hermes-task-label-row">
          <label htmlFor="hermes-task-input">{t('order')}</label>
          <button
            type="button"
            aria-describedby="hermes-question-picker-hint"
            onClick={() => (onPickCompetitionQuestion ?? onQuery)(pickCompetitionQuestion(coin, random).query)}
          >
            {t('competitionQuestionPicker')}
          </button>
        </div>
        <span id="hermes-question-picker-hint" className="hermes-sr-only">
          {t('competitionQuestionPickerHint')}
        </span>
        {/* N40 composer：輸入區整塊 flexShrink:0，訊息串（flex:1）吃剩下的高度，
            這是一般 agent 介面的配置——內容多的時候捲訊息，不是壓輸入框。 */}
        {/* N45 wrapper：輸入框與送出鍵疊在一起，`position: relative` 是圓鍵
            定位的參考框；整塊 flexShrink:0，訊息串吃剩下的高度。 */}
        <div style={{ position: 'relative', flexShrink: 0, marginBottom: 10 }}>
        <textarea
          id="hermes-task-input"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          onKeyDown={(e) => {
            // Enter 送出、Shift+Enter 換行——agent 介面的標準行為。
            // `isComposing` 必須擋：中文/日文輸入法選字時的 Enter 是「確認候選
            // 字」，不是送出，不擋就會在打字中途把半成品送出去。
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              if (!disabled) onSubmit()
            }
          }}
          rows={3}
          /* N39: 這顆是整個主控台唯一的輸入口，卻是最容易被壓爛的一塊——左軌是
             flex column，textarea 沒有 `flex-shrink: 0`，空間一緊就被壓到比
             `rows` 還矮。實測 zh-TW 561x700 clientHeight 只剩 16px（scrollHeight
             51），一行字被切掉上下半截；1280x800 也只有 40/51。
             三件事一起改：
               flexShrink: 0  不再讓 flex 壓縮輸入框（左軌本身 overflow-y:auto，
                              空間不夠就讓軌道滾，跟 N37 同一個處理原則）
               minHeight: 74  rows=3 的實高（3×19.2 + padding 16 + border 2），
                              明確給樓地板，不依賴 `rows` 這個會被 flex 蓋掉的值
               resize: 'vertical'  原本是 'none'，使用者連手動拉大都不行；
                              要寫長一點的任務描述時這是唯一的出路
             字級 11.5→12.5：使用者在這裡打字，不是讀 telemetry。 */
          style={{ width: '100%', resize: 'vertical', flexShrink: 0, minHeight: 74, background: 'var(--color-hermes-inset)', border: '1px solid var(--color-hermes-bd2)', borderRadius: 5, color: 'var(--color-hermes-tx)', fontFamily: 'var(--font-hermes-mono)', fontSize: 12.5, lineHeight: 1.6, padding: '8px 46px 8px 10px' }}
        />
        {/* N45: 原本是一條 width:100% 的 amber 橫幅，光那顆鍵就吃掉約 40px，
            在只有 190px 寬的軌道裡比輸入框本身還搶眼。一般 agent 介面的送出
            是「Enter 直接送 + 輸入框內一顆小圖示鍵」，不是一整條橫幅。
            改成 32x32 的圓鍵疊在輸入框右下角（textarea 補 paddingRight
            讓文字不會被壓到鍵底下），鍵面只留 ⤴，原本的文字標籤搬到
            aria-label/title——標籤字串照舊走 i18n，不新增也不刪 key，
            螢幕閱讀器與 hover 提示都還在。 */}
        <button
          onClick={onSubmit}
          disabled={disabled}
          aria-label={submitLabel}
          title={submitLabel}
          style={{ position: 'absolute', right: 7, bottom: 7, width: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--color-hermes-amber)', border: 'none', borderRadius: 6, color: '#1a1206', fontWeight: 700, fontSize: 14, lineHeight: 1, padding: 0, cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? .55 : 1, transition: 'filter .15s, transform .08s' }}
          onMouseEnter={(e) => (e.currentTarget.style.filter = 'brightness(1.12)')}
          onMouseLeave={(e) => (e.currentTarget.style.filter = 'none')}
          onMouseDown={(e) => (e.currentTarget.style.transform = 'scale(.96)')}
          onMouseUp={(e) => (e.currentTarget.style.transform = 'none')}
        >
          ⤴
        </button>
        </div>
      </div>
    </div>
  )
}
