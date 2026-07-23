import { useEffect, useId, useRef, useState, type CSSProperties } from 'react'
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
  const entry = GLOSSARY_BY_ID[term]

  useEffect(() => {
    if (!open) return
    const updatePosition = () => {
      const rect = root.current?.getBoundingClientRect()
      if (!rect) return
      const width = Math.min(280, Math.max(160, window.innerWidth - 24))
      setPopoverStyle({
        left: Math.min(Math.max(12, rect.left), Math.max(12, window.innerWidth - width - 12)),
        top: rect.bottom + 7,
        width,
      })
    }
    updatePosition()
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    const onPointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
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
      {open && (
        <span id={id} className="tf-glossary-popover" role="note" style={popoverStyle}>
          <b>{entry.label}</b>
          <span>{entry.description}</span>
        </span>
      )}
    </span>
  )
}
