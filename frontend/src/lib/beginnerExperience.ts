export type BeginnerIntent = 'trust' | 'manipulation' | 'news' | 'compare' | 'decline'
export type AnalysisModeId = 'risk' | 'sentiment' | 'fundamentals' | 'news' | 'catalyst'

export const BEGINNER_INTENTS: Array<{
  id: BeginnerIntent
  label: string
  description: string
  mode: AnalysisModeId
  question: string
}> = [
  { id: 'trust', label: '這個幣現在可信嗎？', description: '整理整體可信程度與主要風險', mode: 'risk', question: '評估目前整體信任狀態，列出三個最重要的支持或風險原因。' },
  { id: 'manipulation', label: '有沒有操縱風險？', description: '檢查異常訊號與來源分歧', mode: 'risk', question: '檢查是否有正在形成的市場操縱風險，並指出支持與反對證據。' },
  { id: 'news', label: '這則消息可信嗎？', description: '比對新聞與其他可靠來源', mode: 'news', question: '驗證最近的重要消息是否可信，區分已確認事實、推論與尚未證實說法。' },
  { id: 'compare', label: '什麼正在影響價格？', description: '找出近期可能的價格催化因素', mode: 'catalyst', question: '找出近期最可能影響價格的催化因素，並說明證據強度與不確定性。' },
  { id: 'decline', label: '為什麼信任分數下降？', description: '找出下降來源與變化原因', mode: 'fundamentals', question: '說明信任分數下降的主要原因，列出變化最大的訊號與需要持續觀察的項目。' },
]

export function recommendAnalysisMode(question: string): AnalysisModeId {
  const value = question.toLowerCase()
  if (/新聞|消息|傳聞|真假|查證|媒體|news|rumou?r|verify/.test(value)) return 'news'
  if (/社群|情緒|看法|輿情|sentiment|social|mood/.test(value)) return 'sentiment'
  if (/基本面|營運|代幣經濟|財務|團隊|fundamental|tokenomic|revenue/.test(value)) return 'fundamentals'
  if (/價格|上漲|下跌|催化|事件影響|price|catalyst|pump|drop/.test(value)) return 'catalyst'
  return 'risk'
}

export function beginnerTypeForMode(mode: AnalysisModeId): 'multi_source' | 'hypothesis' {
  return mode === 'fundamentals' || mode === 'catalyst' ? 'hypothesis' : 'multi_source'
}

export function beginnerQuestion(coin: string, intent: BeginnerIntent): string {
  const selected = BEGINNER_INTENTS.find((item) => item.id === intent) ?? BEGINNER_INTENTS[0]
  return `${coin}：${selected.question}`
}

const ONBOARDING_COOKIE = 'trustforge_hermes_onboarding_v1'

export function shouldShowHermesOnboarding(): boolean {
  return !document.cookie.split('; ').some((item) => item === `${ONBOARDING_COOKIE}=done`)
}

export function rememberHermesOnboarding(): void {
  document.cookie = `${ONBOARDING_COOKIE}=done; Max-Age=31536000; Path=/; SameSite=Lax`
}
