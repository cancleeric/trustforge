import { useEffect, useRef, useState } from 'react'

type DiandianState = 'active' | 'thinking' | 'idle'

interface Props {
  /** Override the auto-detected state */
  state?: DiandianState
  /** Whether analysis is currently running */
  isAnalyzing?: boolean
  /** Called when user clicks the avatar */
  onClick?: () => void
}

const IDLE_TIMEOUT_MS = 60_000 // 1 minute

const HOVER_MESSAGES: Array<{ msg: string; state: DiandianState; weight: number }> = [
  { msg: '你最認真的小助手點點正在思考', state: 'thinking', weight: 80 },
  { msg: '點點又要加班了嗎.......', state: 'idle', weight: 20 },
]

function pickHoverMessage() {
  const total = HOVER_MESSAGES.reduce((s, m) => s + m.weight, 0)
  let r = Math.random() * total
  for (const m of HOVER_MESSAGES) {
    r -= m.weight
    if (r <= 0) return m
  }
  return HOVER_MESSAGES[0]
}

export default function DiandianAvatar({ state: overrideState, isAnalyzing, onClick }: Props) {
  const [idleActive, setIdleActive] = useState(false)
  const [hoverBubble, setHoverBubble] = useState<{ msg: string; state: DiandianState } | null>(null)
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const hoverRef = useRef(false)

  // Idle detection: 1 minute no interaction
  useEffect(() => {
    const reset = () => {
      setIdleActive(false)
      if (idleTimer.current) clearTimeout(idleTimer.current)
      idleTimer.current = setTimeout(() => setIdleActive(true), IDLE_TIMEOUT_MS)
    }
    reset()
    window.addEventListener('click', reset)
    window.addEventListener('scroll', reset, true)
    window.addEventListener('keypress', reset)
    return () => {
      window.removeEventListener('click', reset)
      window.removeEventListener('scroll', reset, true)
      window.removeEventListener('keypress', reset)
      if (idleTimer.current) clearTimeout(idleTimer.current)
    }
  }, [])

  // Determine current state
  const currentState: DiandianState = hoverBubble
    ? hoverBubble.state
    : overrideState
      ? overrideState
      : isAnalyzing
        ? 'thinking'
        : idleActive
          ? 'idle'
          : 'active'

  const imageSrc = `/diandian/${currentState}.png`
  const animClass = `diandian-${currentState}`

  const handlePointerEnter = () => {
    if (hoverRef.current) return
    hoverRef.current = true
    const pick = pickHoverMessage()
    setHoverBubble(pick)
    setTimeout(() => {
      setHoverBubble(null)
      hoverRef.current = false
    }, 3000)
  }

  const handlePointerLeave = () => {
    // bubble will auto-dismiss after 3s
  }

  return (
    <div
      className="diandian-container"
      onPointerEnter={handlePointerEnter}
      onPointerLeave={handlePointerLeave}
      onClick={onClick}
      role="button"
      aria-label="點點助手"
      tabIndex={0}
    >
      <img
        src={imageSrc}
        alt="點點"
        className={`diandian-avatar ${animClass}`}
        draggable={false}
      />
      {hoverBubble && (
        <div className="diandian-bubble">
          {hoverBubble.msg}
        </div>
      )}
    </div>
  )
}
