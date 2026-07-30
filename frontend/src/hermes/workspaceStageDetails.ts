import type { BridgeHologramData } from '../components/BridgeHologramContext'
import type { HermesWorkspaceModule } from './HermesModuleDeck'
import type { HermesLocale } from './hermesI18n'
import { moduleStageLabels } from './stagePresentation'

export type WorkspaceStageId = `${HermesWorkspaceModule}:${number}`

export type WorkspaceStageFact = {
  label: string
  value: string
}

export type WorkspaceStageDetail = {
  id: WorkspaceStageId
  module: HermesWorkspaceModule
  index: number
  label: string
  purpose: string
  status: 'available' | 'unavailable'
  metric?: string
  unit?: string
  facts: WorkspaceStageFact[]
  missingReason?: string
}

type WorkspacePurposeMap = Record<HermesWorkspaceModule, [string, string, string, string, string]>

const PURPOSES: WorkspacePurposeMap = {
  analyze: [
    '收集這次 run 實際採用的來源與快照。',
    '把來源內容整理成可追溯的主張。',
    '依證據計算信任構成與推理結果。',
    '把結論綁回可驗證的證據。',
    '封存並交付這次 run 的正式報告。',
  ],
  compare: [
    '呈現比較基準的第一個市場。',
    '呈現比較基準的第二個市場。',
    '將兩側資料換算到相同尺度。',
    '計算兩側可驗證指標的差異。',
    '彙整這次比較的結論。',
  ],
  history: [
    '確認可供回放的歷史快照。',
    '依所選期間切出 point-in-time 資料。',
    '逐日重建當時可見的資訊。',
    '將實際結果回標至歷史 run。',
    '呈現校準後的趨勢。',
  ],
  status: [
    '檢查目前可連線的資料來源。',
    '檢查服務使用的快取狀態。',
    '確認各來源資料是否仍在鮮度門檻內。',
    '彙整目前可驗證的異常訊號。',
    '呈現整體服務健康狀態。',
  ],
  costs: [
    '彙整已記錄的模型呼叫。',
    '依模型或供應商整理用量。',
    '計量帳本內已記錄的 token。',
    '確認成本紀錄已寫入 append-only ledger。',
    '彙整跨 run 的已記錄 LLM 成本。',
  ],
  whale: [
    '辨識已記錄的大額鏈上活動。',
    '區分流入或流出交易所的活動。',
    '彙整可驗證的淨流入與淨流出。',
    '列出已記錄的大額交易明細。',
    '呈現所選期間的鯨魚活動趨勢。',
  ],
}

const PURPOSES_EN: WorkspacePurposeMap = {
  analyze: ['Collect sources and snapshot used by this run.', 'Extract traceable claims.', 'Compute trust components and reasoning.', 'Bind conclusions to evidence.', 'Seal and deliver the formal report.'],
  compare: ['Show the first comparison market.', 'Show the second comparison market.', 'Normalize both sides.', 'Compute verifiable differences.', 'Summarize the comparison verdict.'],
  history: ['Locate replayable snapshots.', 'Select point-in-time records.', 'Replay each day.', 'Attach observed outcomes.', 'Show calibrated trends.'],
  status: ['Check source uplinks.', 'Inspect cache state.', 'Validate source freshness.', 'Summarize verified alerts.', 'Show overall service health.'],
  costs: ['Collect recorded model calls.', 'Group usage by model or provider.', 'Meter recorded tokens.', 'Verify append-only ledger entries.', 'Sum recorded LLM cost across runs.'],
  whale: ['Detect recorded large transfers.', 'Classify exchange flows.', 'Aggregate verified net flow.', 'List recorded large transactions.', 'Show whale activity trends.'],
}

export function workspaceStageId(module: HermesWorkspaceModule, index: number): WorkspaceStageId {
  return `${module}:${index}` as WorkspaceStageId
}

export function buildWorkspaceStageDetails(
  module: HermesWorkspaceModule,
  locale: HermesLocale,
  telemetry: BridgeHologramData | null | undefined,
): WorkspaceStageDetail[] {
  const labels = moduleStageLabels(locale)[module]
  const purposes = (locale === 'zh-TW' ? PURPOSES : PURPOSES_EN)[module]
  return labels.map((label, index) => {
    const stage = telemetry?.pipelineStages?.[index] ?? telemetry?.workspaceStageMetrics?.[index]
    const derived = stage ?? derivedStage(module, index, telemetry, locale)
    const facts: WorkspaceStageFact[] = [...(derived?.facts ?? [])]
    if (telemetry?.runId) facts.push({ label: 'run', value: telemetry.runId })
    if (telemetry?.snapshotAt) facts.push({ label: 'snapshot', value: telemetry.snapshotAt })
    if (telemetry?.primaryLabel) facts.push({ label: locale === 'zh-TW' ? '主要對象' : 'Primary', value: telemetry.primaryLabel })
    if (telemetry?.secondaryLabel) facts.push({ label: locale === 'zh-TW' ? '比較對象' : 'Secondary', value: telemetry.secondaryLabel })
    if (telemetry?.status) facts.push({ label: locale === 'zh-TW' ? '工作區狀態' : 'Workspace status', value: telemetry.status })
    if (derived?.status) facts.push({ label: locale === 'zh-TW' ? '階段狀態' : 'Stage status', value: derived.status })

    return {
      id: workspaceStageId(module, index),
      module,
      index,
      label,
      purpose: purposes[index],
      status: derived ? 'available' : 'unavailable',
      metric: derived?.metric,
      unit: derived?.unit,
      facts,
      missingReason: derived ? undefined : locale === 'zh-TW'
        ? '此工作區尚未提供這一階段的可驗證遙測契約；畫面不會用推估值代替。'
        : 'This workspace has not supplied a verifiable telemetry contract for this stage; no estimate is substituted.',
    }
  })
}

type DerivedStage = {
  metric: string
  unit: string
  status?: string
  facts?: WorkspaceStageFact[]
}

function derivedStage(
  module: HermesWorkspaceModule,
  index: number,
  telemetry: BridgeHologramData | null | undefined,
  locale: HermesLocale,
): DerivedStage | null {
  if (!telemetry) return null
  const zh = locale === 'zh-TW'
  const total = telemetry.total ?? 0
  const points = telemetry.points ?? []
  const percent = (value: number | undefined) => value == null ? '—' : `${Math.round(value * 100)}%`
  if (module === 'compare') {
    const values: DerivedStage[] = [
      { metric: percent(telemetry.primaryValue), unit: telemetry.primaryLabel ?? (zh ? '市場 A' : 'market A') },
      { metric: percent(telemetry.secondaryValue), unit: telemetry.secondaryLabel ?? (zh ? '市場 B' : 'market B') },
      { metric: String(total), unit: zh ? '兩側證據筆數' : 'evidence across both sides' },
      { metric: percent(telemetry.secondaryValue == null || telemetry.primaryValue == null ? undefined : Math.abs(telemetry.primaryValue - telemetry.secondaryValue)), unit: zh ? '信任差距' : 'trust delta' },
      { metric: telemetry.status ?? (zh ? '比較資料已載入' : 'comparison loaded'), unit: '' },
    ]
    return values[index]
  }
  if (module === 'history') {
    const first = points[0]
    const last = points.at(-1)
    const values: DerivedStage[] = [
      { metric: String(total), unit: zh ? '歷史快照' : 'archived snapshots' },
      { metric: String(points.length), unit: zh ? '時間切片' : 'point-in-time slices' },
      { metric: String(points.length), unit: zh ? '可回放日' : 'replayable days' },
      { metric: '—', unit: zh ? 'API 未提供實際結果回標總數' : 'outcome-label count not supplied by API' },
      { metric: last == null ? '—' : `${Math.round(last * 100)}%`, unit: zh ? '最新信任值' : 'latest trust', facts: first == null || last == null ? [] : [{ label: zh ? '區間變化' : 'Range delta', value: `${Math.round((last - first) * 100)} pts` }] },
    ]
    return values[index]
  }
  if (module === 'status') {
    const values: DerivedStage[] = [
      { metric: String(total), unit: zh ? '受監測來源格' : 'monitored source cells' },
      { metric: telemetry.status ?? '—', unit: zh ? '快取／連線狀態' : 'cache/uplink status' },
      { metric: percent(telemetry.primaryValue), unit: zh ? '新鮮資料比例' : 'fresh ratio' },
      { metric: telemetry.status === 'DEGRADED' ? '1+' : '0', unit: zh ? '衍生異常訊號（非告警帳本）' : 'derived anomalies (not alert ledger)' },
      { metric: telemetry.status ?? '—', unit: zh ? '整體健康' : 'overall health' },
    ]
    return values[index]
  }
  if (module === 'costs') {
    const values: Array<DerivedStage | null> = [
      { metric: String(total), unit: zh ? '帳本 run；API 未提供跨 run 全量 call 數' : 'ledger runs; API omits all-run call count' },
      { metric: telemetry.status ?? '—', unit: zh ? '模型分組' : 'model groups' },
      null,
      { metric: String(total), unit: zh ? 'append-only ledger run' : 'append-only ledger runs' },
      { metric: `$${(telemetry.primaryValue ?? 0).toFixed(4)}`, unit: zh ? '跨 run 已記錄 LLM 成本' : 'recorded LLM cost across runs' },
    ]
    return values[index]
  }
  if (module === 'whale') {
    const values: DerivedStage[] = [
      { metric: String(total), unit: zh ? '已回傳大額活動' : 'returned large transfers' },
      { metric: telemetry.status ?? '—', unit: zh ? '交易所流向狀態' : 'exchange flow status' },
      { metric: telemetry.primaryValue == null ? '—' : `$${telemetry.primaryValue.toFixed(0)}`, unit: zh ? '淨流量' : 'net flow' },
      { metric: String(total), unit: zh ? '回傳明細筆數' : 'returned details' },
      { metric: String(points.length), unit: zh ? '趨勢時間桶' : 'trend buckets' },
    ]
    return values[index]
  }
  return null
}
