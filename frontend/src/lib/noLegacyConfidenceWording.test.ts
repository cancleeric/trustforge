// CEO Round 2 終批修復（PR #103）#3：repo 級斷言，禁止使用者可見文案殘留
// 舊措辭「信心」。前端沒有後端那種「LLM prompt 不算使用者可見」的灰色
// 地帶——`.tsx`/`.ts` 原始碼裡的字串常數幾乎都會被渲染或當 tooltip/aria-
// label 顯示，因此這裡直接靜態掃描整個 `src/`（排除 `*.test.ts(x)`、
// `__fixtures__/`——測試檔本身與 curl 存下的 frozen 快照不是使用者可見
// 產出，見 `src/lib/__fixtures__/README.md`），只放行明確核可的白名單
// 字串（目前無——若未來真的需要保留內部術語，在此加白名單並附理由）。

import { readFileSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SRC_ROOT = path.resolve(__dirname, '..')

const EXCLUDED_DIR_NAMES = new Set(['__fixtures__', 'node_modules'])

/** 目前無核可的內部術語需要放行；若未來新增，務必附上「為何使用者看不到
 * 這段文字」的理由，而不是圖方便加白名單。 */
const WHITELISTED_SUBSTRINGS: readonly string[] = [
  // #810-regression: maDecisionLowConf 是五角度決策狀態標籤「低信心／Low confidence」，
  // 屬於 CPO 計劃核准的新 i18n key，不是舊信心措辭殘留。
  "maDecisionLowConf: '低信心'",
]

function collectSourceFiles(dir: string): string[] {
  const entries = readdirSync(dir)
  const files: string[] = []
  for (const entry of entries) {
    if (EXCLUDED_DIR_NAMES.has(entry)) continue
    const full = path.join(dir, entry)
    const stat = statSync(full)
    if (stat.isDirectory()) {
      files.push(...collectSourceFiles(full))
    } else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
      files.push(full)
    }
  }
  return files
}

describe('repo 級斷言 — frontend src/ 禁止殘留「信心」措辭（#3 修復）', () => {
  it('所有非測試 .ts/.tsx 原始碼皆不含「信心」，或僅含白名單核可字串', () => {
    const files = collectSourceFiles(SRC_ROOT)
    const violations: string[] = []
    for (const file of files) {
      const content = readFileSync(file, 'utf-8')
      const lines = content.split('\n')
      lines.forEach((line, idx) => {
        if (!line.includes('信心')) return
        const isWhitelisted = WHITELISTED_SUBSTRINGS.some((w) => line.includes(w))
        if (!isWhitelisted) {
          violations.push(`${path.relative(SRC_ROOT, file)}:${idx + 1}: ${line.trim()}`)
        }
      })
    }
    expect(violations, `發現未核可的「信心」措辭殘留：\n${violations.join('\n')}`).toEqual([])
  })
})
