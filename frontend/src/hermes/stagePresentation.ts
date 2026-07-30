import type { HermesWorkspaceModule } from './HermesModuleDeck'
import type { HermesLocale } from './hermesI18n'

export function moduleStageLabels(locale: HermesLocale): Record<HermesWorkspaceModule, [string, string, string, string, string]> {
  return locale === 'zh-TW' ? {
    analyze: ['來源蒐集', '主張抽取', '信任推理', '證據綁定', '報告交付'],
    compare: ['市場 A', '市場 B', '基準正規化', '差異向量', '比較結論'],
    history: ['歷史封存', '時間切片', '每日回放', '結果回標', '校準趨勢'],
    status: ['來源連線', '快取狀態', '資料鮮度', '異常告警', '系統健康'],
    costs: ['呼叫收集', '模型分組', 'Token 計量', '帳本封存', '累計成本'],
    whale: ['鯨魚偵測', '交易所流向', '淨流入出', '大額明細', '趨勢總覽'],
  } : {
    analyze: ['SOURCE INTAKE', 'CLAIM EXTRACTION', 'TRUST REASONING', 'EVIDENCE BINDING', 'REPORT DELIVERY'],
    compare: ['MARKET A', 'MARKET B', 'NORMALIZE', 'DELTA VECTOR', 'VERDICT'],
    history: ['ARCHIVE', 'TIME SLICE', 'DAILY REPLAY', 'OUTCOME LABEL', 'CALIBRATION'],
    status: ['UPLINK', 'CACHE', 'FRESHNESS', 'ALERTS', 'HEALTH'],
    costs: ['CALLS', 'MODELS', 'TOKENS', 'LEDGER', 'TOTAL COST'],
    whale: ['WHALE DETECT', 'EXCHANGE FLOW', 'NET FLOW', 'LARGE TX', 'TREND'],
  }
}
