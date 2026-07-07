// 管理控制台純邏輯層測試（PR-4）：token 分頁級暫存（含 storage 例外降級）、
// live token 產生（CSPRNG hex、符合後端 PUT 驗證規則）、cap 前端驗證邊界、
// 審計遮罩值格式化，以及「⛔ 禁 localStorage」的 repo 級靜態掃描（token
// 儲存策略：React state 為主、最多 sessionStorage——localStorage 是 XSS
// 持久竊取面，任何 src/ 原始碼出現都直接打紅）。

/// <reference types="node" />
import { readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  clearSessionToken,
  formatAuditValue,
  generateLiveToken,
  loadSessionToken,
  saveSessionToken,
  sourceLabel,
  validateCapInput,
  ADMIN_CAP_MAX_USD,
  ADMIN_CAP_MIN_USD,
} from './adminConsole'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// ── session token 暫存 ─────────────────────────────────────────────────

/** 最小 Storage stub（node 測試環境無 DOM sessionStorage）。 */
function fakeStorage(): Storage {
  const map = new Map<string, string>()
  return {
    get length() {
      return map.size
    },
    clear: () => map.clear(),
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    key: (i: number) => [...map.keys()][i] ?? null,
    removeItem: (k: string) => {
      map.delete(k)
    },
    setItem: (k: string, v: string) => {
      map.set(k, v)
    },
  }
}

/** 任何操作都拋錯的 Storage（Safari 隱私模式舊行為/配額爆掉）。 */
function throwingStorage(): Storage {
  const boom = () => {
    throw new DOMException('QuotaExceededError')
  }
  return {
    length: 0,
    clear: boom,
    getItem: boom,
    key: boom,
    removeItem: boom,
    setItem: boom,
  }
}

describe('adminConsole — session token 暫存', () => {
  it('save → load → clear roundtrip', () => {
    const storage = fakeStorage()
    expect(loadSessionToken(storage)).toBeNull()
    saveSessionToken('tok-abc', storage)
    expect(loadSessionToken(storage)).toBe('tok-abc')
    clearSessionToken(storage)
    expect(loadSessionToken(storage)).toBeNull()
  })

  it('空字串視同未設定（不會回傳 ""）', () => {
    const storage = fakeStorage()
    saveSessionToken('', storage)
    expect(loadSessionToken(storage)).toBeNull()
  })

  it('storage 為 null（無 DOM 環境）→ 靜默降級，不 throw', () => {
    expect(loadSessionToken(null)).toBeNull()
    expect(() => saveSessionToken('x', null)).not.toThrow()
    expect(() => clearSessionToken(null)).not.toThrow()
  })

  it('storage 每個操作都拋例外 → 靜默降級成純記憶體模式，不 throw', () => {
    const storage = throwingStorage()
    expect(loadSessionToken(storage)).toBeNull()
    expect(() => saveSessionToken('x', storage)).not.toThrow()
    expect(() => clearSessionToken(storage)).not.toThrow()
  })
})

// ── live token 產生 ────────────────────────────────────────────────────

describe('adminConsole — generateLiveToken', () => {
  it('64 字元小寫 hex（32 bytes），符合後端 24–512 可見 ASCII 規則', () => {
    const token = generateLiveToken()
    expect(token).toMatch(/^[0-9a-f]{64}$/)
  })

  it('連續產生不重複（CSPRNG）', () => {
    expect(generateLiveToken()).not.toBe(generateLiveToken())
  })

  it('確實走 crypto.getRandomValues（不是 Math.random）', () => {
    let called = false
    const fake = {
      getRandomValues: (arr: Uint8Array) => {
        called = true
        arr.fill(0xab)
        return arr
      },
    } as unknown as Crypto
    const token = generateLiveToken(fake)
    expect(called).toBe(true)
    expect(token).toBe('ab'.repeat(32))
  })
})

// ── cap 驗證 ───────────────────────────────────────────────────────────

describe('adminConsole — validateCapInput（與後端 0.1–50 對齊）', () => {
  it('下界 0.1 / 上界 50 放行', () => {
    expect(validateCapInput(String(ADMIN_CAP_MIN_USD))).toEqual({ ok: true, value: 0.1 })
    expect(validateCapInput(String(ADMIN_CAP_MAX_USD))).toEqual({ ok: true, value: 50 })
    expect(validateCapInput(' 3.5 ')).toEqual({ ok: true, value: 3.5 })
  })

  it.each([
    ['0.05', '低於下界'],
    ['0', '0 不接受（緊急全關走開關）'],
    ['-1', '負值'],
    ['50.1', '高於上界'],
    ['abc', '非數字'],
    ['', '空字串'],
    ['   ', '空白'],
    ['Infinity', '非有限數'],
    ['NaN', 'NaN'],
  ])('拒絕 %s（%s）', (raw) => {
    const result = validateCapInput(raw)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.message.length).toBeGreaterThan(0)
  })
})

// ── 審計顯示格式化 ─────────────────────────────────────────────────────

describe('adminConsole — formatAuditValue', () => {
  it('live_token 遮罩值 → 中文字樣，絕不原樣輸出遮罩內容', () => {
    expect(formatAuditValue('live_token', '<rotated last4=ab12>')).toBe('已輪替')
    expect(formatAuditValue('live_token', '<rotated>')).toBe('已輪替')
    expect(formatAuditValue('live_token', '<set last4=cd34>')).toBe('已設定')
    expect(formatAuditValue('live_token', '<set>')).toBe('已設定')
    expect(formatAuditValue('live_token', '<cleared>')).toBe('已清除')
    expect(formatAuditValue('live_token', '<unset>')).toBe('未設定')
  })

  it('live_token 未知格式/非字串 → 「（遮罩）」，不外洩原始內容', () => {
    expect(formatAuditValue('live_token', 'plain-leak?')).toBe('（遮罩）')
    expect(formatAuditValue('live_token', 123)).toBe('（遮罩）')
    expect(formatAuditValue('live_token', null)).toBe('（遮罩）')
  })

  it('一般欄位：null＝清除、bool＝開/關、數字原樣', () => {
    expect(formatAuditValue('daily_cap_usd', null)).toBe('（清除，回落 env/default）')
    expect(formatAuditValue('daily_cap_usd', 3.5)).toBe('3.5')
    expect(formatAuditValue('bedrock_enabled', true)).toBe('開')
    expect(formatAuditValue('bedrock_enabled', false)).toBe('關')
  })
})

describe('adminConsole — sourceLabel', () => {
  it('已知層級有中文標籤，未知字面值原樣顯示（enum 可演進）', () => {
    expect(sourceLabel('config')).toContain('config')
    expect(sourceLabel('env')).toContain('env')
    expect(sourceLabel('default')).toContain('default')
    expect(sourceLabel('some_future_source')).toBe('some_future_source')
  })
})

// ── ⛔ 禁 localStorage 靜態掃描（token 儲存策略防回歸）───────────────────
//
// CEO Round（harper 條件B / qa M1）修復：舊版只掃固定 4 個檔案 + 單一
// `localStorage\s*[.[]` regex，`window['localStorage']`、
// `globalThis.localStorage`、解構賦值（`const { localStorage } = window`）、
// 未來新增的 admin 檔案都繞得過——形同虛設。改成比照
// `noLegacyConfidenceWording.test.ts` 的 `collectSourceFiles`：**整個 src/**
// 掃描（排除 `__fixtures__/`/`node_modules/`、`*.test.ts(x)`），只有
// `lib/theme.ts` 白名單豁免（主題偏好使用 localStorage 是既有且合法的
// 非機敏值用途，不在 admin token 禁令範圍）。
//
// pattern 涵蓋四種實際使用形式（非窮舉字面比對，而是「取得/存取
// localStorage」的語法形狀）：
//   1. 直接呼叫：`localStorage.getItem(...)` / `localStorage['x']`
//   2. 物件屬性存取：`window.localStorage` / `globalThis.localStorage`
//      （`\.localStorage\b`，不要求後面一定接 `.`/`[`，涵蓋
//      `const ls = window.localStorage` 這種賦值後不再串接的寫法）
//   3. 字串鍵值 bracket 存取：`window['localStorage']` /
//      `globalThis["localStorage"]`（不管前面接哪個物件，只要
//      `[...'localStorage'...]` 這個形狀出現就算）
//   4. 解構賦值：`const { localStorage } = window`（大括號內出現
//      `localStorage` 識別字）
// 這裡刻意不比對「裸字」`\blocalStorage\b`（不要求前後任何存取語法）——
// 本檔與 `adminConsole.ts`/`AdminPage.tsx` 的中文註解本身會提及
// 「localStorage」字樣說明政策，裸字比對會把這些說明性註解也打成違規。
// 上述 4 種 pattern 都要求真實的存取/解構語法，不會誤殺純文字敘述。

const WHITELISTED_RELATIVE_PATHS = new Set(['lib/theme.ts'])

const LOCAL_STORAGE_USAGE_PATTERNS: RegExp[] = [
  /\blocalStorage\s*[.[]/, // localStorage.xxx / localStorage['xxx']
  /\.localStorage\b/, // window.localStorage / globalThis.localStorage（任何物件）
  /\[\s*['"]localStorage['"]\s*\]/, // window['localStorage'] / globalThis["localStorage"]
  /\{[^}]*\blocalStorage\b[^}]*\}\s*=/, // const { localStorage } = window（解構）
]

function usesLocalStorage(line: string): boolean {
  return LOCAL_STORAGE_USAGE_PATTERNS.some((re) => re.test(line))
}

function collectSourceFiles(dir: string): string[] {
  const files: string[] = []
  for (const entry of readdirSync(dir)) {
    if (entry === '__fixtures__' || entry === 'node_modules') continue
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

describe('token 儲存策略 — src/ 全域禁止 localStorage（XSS 持久竊取面）', () => {
  it('src/ 所有 .ts/.tsx 原始碼（測試檔除外、theme.ts 白名單除外）不得使用 localStorage', () => {
    const SRC_ROOT = path.resolve(__dirname, '..')
    const violations: string[] = []
    for (const file of collectSourceFiles(SRC_ROOT)) {
      const rel = path.relative(SRC_ROOT, file)
      if (WHITELISTED_RELATIVE_PATHS.has(rel)) continue
      const lines = readFileSync(file, 'utf-8').split('\n')
      lines.forEach((line, i) => {
        if (usesLocalStorage(line)) {
          violations.push(`${rel}:${i + 1}: ${line.trim()}`)
        }
      })
    }
    expect(
      violations,
      `發現 localStorage 使用（admin token 儲存策略禁止；如為 theme.ts 主題偏好用途請確認已在白名單）：\n${violations.join('\n')}`,
    ).toEqual([])
  })

  it('theme.ts 本身確實使用 localStorage（白名單非死條目——若 theme.ts 改寫不再用，應移除白名單）', () => {
    const SRC_ROOT = path.resolve(__dirname, '..')
    const themeFile = path.join(SRC_ROOT, 'lib/theme.ts')
    const lines = readFileSync(themeFile, 'utf-8').split('\n')
    expect(lines.some(usesLocalStorage)).toBe(true)
  })
})
