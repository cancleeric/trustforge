import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { FOCUS_IDS, defaultQuestionTypeForFocus } from './analysisTaxonomy'

/** N69：question_type 從「使用者選的下拉」改成「由分析角度推導」之後，
 * 前端這張對應表就變成契約的一部分——它算出來的題型必須跟後端實際會跑的
 * 那個一致，否則畫面顯示「假設驗證」、後端卻跑 multi_source，沒人會發現。
 * 這條直接讀後端 `analysis_flow.py` 的 MODES 當事實來源，任何一邊單獨改動
 * 都會紅。（做法上跟 EcoLinkPage 那條「chip 綁 fixture」同一個路數：
 * 不是比對字面，是拿對面那份真的資料來對答案。） */
describe('分析角度 → 官方題型的對應', () => {
  it('與後端 analysis_flow.MODES 完全一致', () => {
    const flow = readFileSync(
      path.join(__dirname, '..', '..', '..', 'src', 'trustforge', 'analysis_flow.py'),
      'utf8',
    )
    const block = /MODES: dict\[str, tuple\[QuestionType, str\]\] = \{([\s\S]*?)\n\}/.exec(flow)?.[1]
    expect(block, '找不到後端 MODES 定義，對應表已無從驗證').toBeTruthy()

    const backend = new Map<string, string>()
    for (const m of (block as string).matchAll(/"(\w+)":\s*\(QuestionType\.(\w+),/g)) {
      backend.set(m[1], m[2].toLowerCase())
    }
    // 後端有的角度前端要有，前端有的角度後端也要認得——少一邊就是死選項。
    expect([...backend.keys()].sort()).toEqual([...FOCUS_IDS].sort())
    for (const focus of FOCUS_IDS) {
      expect([focus, defaultQuestionTypeForFocus(focus)]).toEqual([focus, backend.get(focus)])
    }
  })
})
