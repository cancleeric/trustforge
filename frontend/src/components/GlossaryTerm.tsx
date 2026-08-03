import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { GLOSSARY_BY_ID, type GlossaryTermId } from '../lib/glossaryCatalog'
import { useHermesLocaleOptional } from '../hermes/hermesI18n'

export type GlossaryKey = GlossaryTermId

type Props = {
  term: GlossaryKey
  label?: string
  compact?: boolean
}

export default function GlossaryTerm({ term, label, compact }: Props) {
  const [open, setOpen] = useState(false)
  // #847：滑鼠停留浮出的短版白話文。跟 `open`（點開的完整解釋卡）分成兩個狀態，
  // 不是同一個開關的兩種樣子——內容不同（tooltip vs description）、觸發與關閉
  // 條件也不同，混在一起會出現「滑過去先看到短的、點下去內容跳掉」這種怪事。
  const [hovered, setHovered] = useState(false)
  const [popoverStyle, setPopoverStyle] = useState<CSSProperties>({})
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const locale = useHermesLocaleOptional()
  const id = useId()
  const root = useRef<HTMLSpanElement>(null)
  // N51：portal 之後 popover 不再是 root 的子孫，關閉判定要多認它一個，
  // 否則點 popover 內文（想選取文字）就等於點外面，自己把自己關掉。
  const popover = useRef<HTMLSpanElement>(null)
  const entry = GLOSSARY_BY_ID[term]
  // 點開的優先：已經點開完整解釋時，滑鼠再怎麼動都不該把它換成短版。
  const hoverOnly = hovered && !open && Boolean(entry?.tooltip)
  const visible = open || hoverOnly

  /**
   * #847：只有「新手模式 + 這台裝置真的有滑鼠 + 這個詞有白話版」三者同時成立才浮。
   *
   * 新手模式旗標掛在 documentElement 上而不是儀表板容器上，因為下面的 popover 是
   * portal 到 <body>（見 N51），祖先 class 傳不進來。
   * `(hover: hover)` 擋掉觸控裝置：手機上 pointerenter 會在點擊時一併觸發，
   * 變成「點一下先跳短的、再跳完整的」閃兩次。觸控維持原本的點擊行為。
   */
  const hoverEnabled = () =>
    Boolean(entry?.tooltip)
    && document.documentElement.dataset.tfBeginner === '1'
    && (typeof window.matchMedia !== 'function' || window.matchMedia('(hover: hover)').matches)

  const clearHoverTimer = () => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current)
    hoverTimer.current = null
  }
  const onPointerEnter = () => {
    if (!hoverEnabled()) return
    clearHoverTimer()
    // 300ms 才浮：滑鼠只是路過整段文字時不該一路彈出一排小卡。
    hoverTimer.current = setTimeout(() => setHovered(true), 300)
  }
  const onPointerLeave = () => {
    clearHoverTimer()
    // WCAG 1.4.13（Content on Hover）要求浮出的內容可以把滑鼠移過去（hoverable）。
    // 這裡留 160ms 讓指標從詞走到小卡上；小卡本身也掛了 enter/leave，走到上面就
    // 會取消這次關閉，使用者才能選取裡面的字。
    hoverTimer.current = setTimeout(() => setHovered(false), 160)
  }

  useEffect(() => clearHoverTimer, [])

  // N51b：原本一律往下開（`top: rect.bottom + 7`），沒有量過下方還剩多少空間。
  // 右軌最底下的「跨來源分歧 ?」鈕在 1920x1080 落在 top:1002，popover 就被放到
  // 1032～1113，下緣直接掉出 1080 的畫面外。這裡改成先量 popover 實際高度
  // （portal 已掛載，useLayoutEffect 這時量得到），下方塞不下就翻到鈕上方；
  // 上下都塞不下才退回貼齊視窗、由 max-height 內部捲動。
  const updatePosition = useCallback(() => {
    const rect = root.current?.getBoundingClientRect()
    if (!rect) return
    const width = Math.min(280, Math.max(160, window.innerWidth - 24))
    const vh = window.innerHeight
    // 先把寬度寫進元素再量高度：popover 是換行文字，高度取決於寬度，
    // 若等 state 回寫才量，第一次會量到 width:auto 下的錯誤高度。
    const node = popover.current
    if (node) node.style.width = `${width}px`
    const height = node?.offsetHeight ?? 0
    const below = rect.bottom + 7
    const above = rect.top - 7 - height
    const top = below + height <= vh - 12 ? below
      : above >= 12 ? above
        : Math.max(12, Math.min(below, vh - height - 12))
    setPopoverStyle({
      left: Math.min(Math.max(12, rect.left), Math.max(12, window.innerWidth - width - 12)),
      top,
      width,
    })
  }, [])

  useLayoutEffect(() => {
    if (!visible) return
    updatePosition()
  }, [visible, updatePosition])

  useEffect(() => {
    if (!visible) return
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [visible, updatePosition])

  useEffect(() => {
    if (!visible) return
    const onKeyDown = (event: KeyboardEvent) => {
      // WCAG 1.4.13 的另一半：滑鼠浮出來的東西也必須能用 Esc 關掉，
      // 不然它會一直擋住底下的字。
      if (event.key === 'Escape') { setOpen(false); clearHoverTimer(); setHovered(false) }
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (!root.current?.contains(target) && !popover.current?.contains(target)) setOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('pointerdown', onPointerDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('pointerdown', onPointerDown)
    }
  }, [visible])

  if (!entry) return label ?? null

  return (
    <span
      ref={root}
      className={`tf-glossary${compact ? ' is-compact' : ''}`}
      onClick={(event) => event.stopPropagation()}
      onPointerEnter={onPointerEnter}
      onPointerLeave={onPointerLeave}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        aria-describedby={hoverOnly ? id : undefined}
        onClick={() => { clearHoverTimer(); setHovered(false); setOpen((value) => !value) }}
      >
        {label ?? entry.label}
        <i aria-hidden="true">?</i>
      </button>
      {/* N51：popover 必須 portal 到 <body>。`position: fixed` 只有在沒有
          「會建立 containing block 的祖先」時才以視窗為基準，而 backdrop-filter
          正是其中一種——右軌 `.hermes-glass` 有 `backdrop-filter: blur(10px)`，
          於是這裡算好的 `left: 1628px` 變成從右軌左緣(1620) 起算，實際落在
          3248，整塊飛出 1920 寬的畫面外。老闆原話「這幾個問號點了東西閃一下
          就不見了」——不是動畫，是它真的被丟到畫面外。portal 之後定位基準永遠
          是視窗，所有掛在毛玻璃容器裡的名詞解釋一次修好。 */}
      {visible && createPortal(
        <span
          ref={popover}
          id={id}
          className={`tf-glossary-popover${hoverOnly ? ' is-hint' : ''}`}
          role={hoverOnly ? 'tooltip' : 'note'}
          style={popoverStyle}
          onClick={(event) => event.stopPropagation()}
          onPointerEnter={clearHoverTimer}
          onPointerLeave={onPointerLeave}
        >
          <b>{entry.label}</b>
          {/* #847：滑過去給白話短句，點下去才給正式定義。
              `description` 是比賽方看的報告用的那一份，這裡不覆寫、不改寫，
              新手模式關掉時整段跟現在一字不差。 */}
          <span>{hoverOnly ? (entry.tooltip?.[locale] ?? entry.description) : entry.description}</span>
          {!hoverOnly && entry.riskNote && (
            <span className="tf-glossary-risk">
              <i aria-hidden="true">⚠️</i>
              {entry.riskNote}
            </span>
          )}
        </span>,
        document.body,
      )}
    </span>
  )
}
