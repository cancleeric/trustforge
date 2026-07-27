import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { GLOSSARY_BY_ID, type GlossaryTermId } from '../lib/glossaryCatalog'

export type GlossaryKey = GlossaryTermId

type Props = {
  term: GlossaryKey
  label?: string
  compact?: boolean
}

export default function GlossaryTerm({ term, label, compact }: Props) {
  const [open, setOpen] = useState(false)
  const [popoverStyle, setPopoverStyle] = useState<CSSProperties>({})
  const id = useId()
  const root = useRef<HTMLSpanElement>(null)
  // N51：portal 之後 popover 不再是 root 的子孫，關閉判定要多認它一個，
  // 否則點 popover 內文（想選取文字）就等於點外面，自己把自己關掉。
  const popover = useRef<HTMLSpanElement>(null)
  const entry = GLOSSARY_BY_ID[term]

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
    if (!open) return
    updatePosition()
  }, [open, updatePosition])

  useEffect(() => {
    if (!open) return
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [open, updatePosition])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
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
  }, [open])

  if (!entry) return label ?? null

  return (
    <span
      ref={root}
      className={`tf-glossary${compact ? ' is-compact' : ''}`}
      onClick={(event) => event.stopPropagation()}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
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
      {open && createPortal(
        <span
          ref={popover}
          id={id}
          className="tf-glossary-popover"
          role="note"
          style={popoverStyle}
          onClick={(event) => event.stopPropagation()}
        >
          <b>{entry.label}</b>
          <span>{entry.description}</span>
          {entry.riskNote && (
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
