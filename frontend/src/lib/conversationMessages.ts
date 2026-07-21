import type { AnalysisConversationMessage } from './endpoints'

export function latestUniqueConversationMessages(messages: AnalysisConversationMessage[], limit = 3): AnalysisConversationMessage[] {
  return Array.from(new Map(messages.map((message) => [message.message_id, message])).values()).slice(-limit)
}
