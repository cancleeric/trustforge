import type { ReactNode } from 'react'
import GlossaryTerm from './GlossaryTerm'
import { findGlossaryAnnotations } from '../lib/annotatedText'

type Props = {
  text: string
  className?: string
  compact?: boolean
}

export default function AnnotatedText({ text, className, compact }: Props) {
  const annotations = findGlossaryAnnotations(text)

  if (!annotations.length) return <span className={className}>{text}</span>

  const nodes: ReactNode[] = []
  let cursor = 0

  annotations.forEach((annotation, index) => {
    if (annotation.start > cursor) nodes.push(text.slice(cursor, annotation.start))
    const label = text.slice(annotation.start, annotation.end)
    nodes.push(<GlossaryTerm key={`${annotation.start}-${annotation.end}-${index}`} term={annotation.term} label={label} compact={compact} />)
    cursor = annotation.end
  })

  if (cursor < text.length) nodes.push(text.slice(cursor))

  return <span className={className ? `tf-annotated-text ${className}` : 'tf-annotated-text'}>{nodes}</span>
}
