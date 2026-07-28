import type { Report } from './types'
import type { MessageKey } from '../hermes/hermesI18n'

/** N71：報告/證據/執行紀錄的下載實作。抽成獨立模組，讓 `ReportDownloads.tsx`
 *  維持「只匯出元件」（fast-refresh 友善），報告抬頭與技術細節兩處共用同一份。 */
export function downloadFile(filename: string, body: string, type: string) {
  const href = URL.createObjectURL(new Blob([body], { type }))
  const a = document.createElement('a')
  a.href = href
  a.download = filename
  a.click()
  URL.revokeObjectURL(href)
}

export function reportMarkdown(report: Report, t: (key: MessageKey, params?: Record<string, string | number>) => string): string {
  const lines = [
    `# ${t('hepMdReportTitleTemplate', { coin: report.coin })}`,
    '',
    `> Hermes run: ${report.generated_at}`,
    `> ${t('hepMdQuestionPrefix')}${report.question}`,
    '',
    t('hepMdConclusionHeading'),
    report.market_judgment,
    '',
    t('hepMdKeyBasisHeading'),
    `${t('hepMdFactsHeading')}`,
    ...report.facts.map((item) => `- ${item}`),
    `${t('hepMdInferencesHeading')}`,
    ...report.inferences.map((item) => `- ${item}`),
    `${t('hepMdEvidenceMappingHeading')}`,
    ...report.key_basis.map((item) => `- ${item.claim} [${item.evidence_idx.map((index) => `E${index}`).join(', ')}]：${item.explanation}`),
    '',
    t('hepMdCompletenessHeading'),
    `${t('hepMdCalibratedConfidencePrefix')}${report.calibrated_confidence.toFixed(2)}`,
    `${t('hepMdDecisionStatePrefix')}${report.decision_state}`,
    ...report.limits.map((item) => `${t('hepMdKnownLimitPrefix')}${item}`),
    ...report.could_flip.map((item) => `${t('hepMdCouldFlipPrefix')}${item}`),
  ]
  return lines.join('\n')
}

