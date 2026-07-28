import type { Evidence, ExecutionEvent, ExecutionManifest, Report } from '../lib/types'
import { executionLogDownload } from '../lib/executionLogDownload'
import { useHermesI18n } from '../hermes/hermesI18n'
import { downloadFile, reportMarkdown } from '../lib/reportArtifacts'

/** N71（CEO：「手動分析的報告要在哪裡下載？執行過程的 LOG 要在哪裡下載、在哪裡看」）：
 *  三顆下載鈕本來只存在於 `HermesExecutionPanel` 裡，而那塊被包在報告最底下
 *  一個**預設收合**的 `<details>`「技術細節」中——等於使用者跑完手動分析後，
 *  報告、證據、執行紀錄全部藏在一層 disclosure 後面，沒人找得到。
 *  這裡把下載動作抽成共用元件，報告抬頭直接放一排（永遠看得見），
 *  技術細節裡那排照舊，兩邊同一份實作不會走鐘。 */

export default function ReportDownloads({
  execution, events, report, evidence, onOpenExecution,
}: {
  execution: ExecutionManifest
  events: ExecutionEvent[]
  report: Report
  evidence: Evidence[]
  /** 報告抬頭那排才傳：帶使用者去看（並展開）執行過程面板。 */
  onOpenExecution?: () => void
}) {
  const { t } = useHermesI18n()
  const cls = 'rounded border border-tf-link px-3 py-1.5 text-sm font-semibold text-tf-link'
  return (
    <>
      <button type="button" className={cls} onClick={() => downloadFile(`${execution.run_id}-report.md`, reportMarkdown(report, t), 'text/markdown;charset=utf-8')}>{t('hepDownloadReport')}</button>
      <button type="button" className={cls} onClick={() => downloadFile(`${execution.run_id}-evidence.json`, JSON.stringify(evidence, null, 2), 'application/json')}>{t('hepDownloadEvidence')}</button>
      <button type="button" className={cls} onClick={() => {
        const artifact = executionLogDownload(execution, events)
        downloadFile(artifact.name, artifact.body, artifact.type)
      }}>{t('hepDownloadLog')}</button>
      {onOpenExecution && (
        <button type="button" className="rounded border border-tf-border px-3 py-1.5 text-sm font-semibold text-tf-text2" onClick={onOpenExecution}>{t('hepViewExecution')}</button>
      )}
    </>
  )
}
