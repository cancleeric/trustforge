import { describe, expect, it } from 'vitest'
import type { AnalysisConversationMessage } from '../lib/endpoints'
import { latestUniqueConversationMessages } from '../lib/conversationMessages'

function message(message_id: string, content: string, created_at: number): AnalysisConversationMessage {
  return { message_id, content, created_at, role: 'user', question_id: null, job_id: null, snapshot_id: null }
}

describe('latestUniqueConversationMessages', () => {
  it('deduplicates repeated sqlite conversation rows by message_id before rendering', () => {
    const messages = [
      message('m1', '找出近期最可能影響價格的催化因素', 1),
      message('m1', '找出近期最可能影響價格的催化因素', 1),
      message('m1', '找出近期最可能影響價格的催化因素', 1),
      message('m2', '評估整體信任狀態', 2),
    ]

    expect(latestUniqueConversationMessages(messages).map((item) => item.message_id)).toEqual(['m1', 'm2'])
  })
})
