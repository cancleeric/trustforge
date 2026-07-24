import { GLOSSARY_CATALOG, type GlossaryTermId } from './glossaryCatalog'

export type GlossaryAnnotation = {
  start: number
  end: number
  term: GlossaryTermId
}

const ASCII_WORD = /[A-Za-z0-9_]/

const REPORT_TERMS = GLOSSARY_CATALOG
  .filter((term) => term.audiences.includes('report'))
  .flatMap((term) =>
    [term.label, ...term.aliases].map((phrase) => ({
      phrase,
      lowerPhrase: phrase.toLocaleLowerCase(),
      term: term.term_id,
      needsAsciiBoundary: ASCII_WORD.test(phrase),
    })),
  )
  .filter((item) => item.phrase.trim().length > 0)
  .sort((a, b) => b.phrase.length - a.phrase.length)

export function findGlossaryAnnotations(text: string): GlossaryAnnotation[] {
  const annotations: GlossaryAnnotation[] = []
  const lowerText = text.toLocaleLowerCase()

  for (const candidate of REPORT_TERMS) {
    let start = lowerText.indexOf(candidate.lowerPhrase)
    while (start >= 0) {
      const end = start + candidate.phrase.length
      const before = start > 0 ? text[start - 1] : ''
      const after = end < text.length ? text[end] : ''
      const hasBoundary =
        !candidate.needsAsciiBoundary || (!ASCII_WORD.test(before) && !ASCII_WORD.test(after))
      const overlaps = annotations.some((item) => start < item.end && end > item.start)
      if (hasBoundary && !overlaps) annotations.push({ start, end, term: candidate.term })
      start = lowerText.indexOf(candidate.lowerPhrase, start + 1)
    }
  }

  return annotations.sort((a, b) => a.start - b.start || b.end - a.end)
}
