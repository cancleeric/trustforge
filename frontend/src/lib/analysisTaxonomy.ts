/**
 * 分析題型 / 分析角度的單一事實來源。
 *
 * 背景見 docs/plans/PLAN-competition-question-format-ui-2026-07-26.md：
 * 主入口原本把「風險評估／市場情緒／基本面／新聞驗證／催化因子」當成題型顯示，
 * 但那五個是我們的產品化「分析角度」，不是 HOYA BIT 官方命題的題型。官方題型
 * 只有三種——多源整合 / 假設驗證 / 比較分析——評審看的是這三種。PLAN 採方案 B：
 * 官方題型升為主選單，五個角度降級成選填的第二層。
 *
 * 另一個要一起解掉的問題：原本 dashboard 與左軌是拿「翻譯後的 label 字串」當
 * 狀態，再用 `qtypes.indexOf(qtype)` 去平行陣列裡撈回 id，同一組 magic array
 * ['risk','sentiment',…] 散在 5 個地方。語系一切換字串就變，任何一處漏改就錯位。
 * 這裡改成以 id 為狀態、label 只在 render 時用 t() 取，平行陣列全部退場。
 */

import type { MessageKey } from '../hermes/hermesI18n'

export type QuestionTypeId = 'multi_source' | 'hypothesis' | 'comparison'
export type AnalysisFocusId = 'risk' | 'sentiment' | 'fundamentals' | 'news' | 'catalyst'

/** 官方三題型。`backendType` 是送給後端 `QuestionType` 的值。 */
export const QUESTION_TYPES: Array<{ id: QuestionTypeId; labelKey: MessageKey }> = [
  { id: 'multi_source', labelKey: 'qtypeMultiSource' },
  { id: 'hypothesis', labelKey: 'qtypeHypothesis' },
  { id: 'comparison', labelKey: 'qtypeComparison' },
]

/** 產品化分析角度（選填）。對應後端既有的 `mode`，不得移除：
 *  排程、舊連結與歷史 job 仍在用（PLAN §10.1）。 */
export const ANALYSIS_FOCUSES: Array<{ id: AnalysisFocusId; labelKey: MessageKey }> = [
  { id: 'risk', labelKey: 'risk' },
  { id: 'sentiment', labelKey: 'sentiment' },
  { id: 'fundamentals', labelKey: 'fundamentals' },
  { id: 'news', labelKey: 'news' },
  { id: 'catalyst', labelKey: 'catalyst' },
]

export const FOCUS_IDS: AnalysisFocusId[] = ANALYSIS_FOCUSES.map((item) => item.id)

/**
 * 角度 → 官方題型的既有映射（沿用原本 QueryConsole/beginnerExperience 的規則）。
 * 只在使用者「沒有明確選題型」時當預設用；一旦使用者自己選了題型就以他為準。
 */
export function defaultQuestionTypeForFocus(focus: AnalysisFocusId): QuestionTypeId {
  return focus === 'fundamentals' || focus === 'catalyst' ? 'hypothesis' : 'multi_source'
}

export function isQuestionTypeId(value: string | null | undefined): value is QuestionTypeId {
  return value === 'multi_source' || value === 'hypothesis' || value === 'comparison'
}

export function isAnalysisFocusId(value: string | null | undefined): value is AnalysisFocusId {
  return FOCUS_IDS.includes(value as AnalysisFocusId)
}
