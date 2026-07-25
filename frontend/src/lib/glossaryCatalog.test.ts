import { describe, expect, it } from 'vitest'
import {
  GLOSSARY_BY_ID,
  GLOSSARY_CATALOG,
  HELP_CENTER_GLOSSARY,
  type GlossaryCatalogTerm,
  validateGlossaryCatalog,
} from './glossaryCatalog'

// ── 核心詞彙存在性 ──────────────────────────────────────────
describe('glossary catalog — 核心詞彙', () => {
  const REQUIRED_TERM_IDS = [
    'fdv',
    'market_cap',
    'tvl',
    'tokenomics',
    'gas_fee',
    'unlock_sell_pressure',
  ] as const

  it('收錄 FDV、MC、TVL、Tokenomics、Gas Fee、解鎖賣壓', () => {
    for (const termId of REQUIRED_TERM_IDS) {
      expect(GLOSSARY_BY_ID[termId]).toBeDefined()
    }
  })

  it('每個核心詞彙都有 label 與 description', () => {
    for (const termId of REQUIRED_TERM_IDS) {
      const term = GLOSSARY_BY_ID[termId]
      expect(term.label, `${termId} 缺少 label`).toBeTruthy()
      expect(term.description, `${termId} 缺少 description`).toBeTruthy()
    }
  })

  it('每個核心詞彙 audience 包含 report、popover、help_center 三者', () => {
    for (const termId of REQUIRED_TERM_IDS) {
      const term = GLOSSARY_BY_ID[termId]
      expect(term.audiences).toContain('report')
      expect(term.audiences).toContain('popover')
      expect(term.audiences).toContain('help_center')
    }
  })

  it('FDV 有 riskNote', () => {
    expect(GLOSSARY_BY_ID.fdv.riskNote).toBeTruthy()
  })

  it('MC（market_cap）無 riskNote', () => {
    expect(GLOSSARY_BY_ID.market_cap.riskNote).toBeUndefined()
  })

  it('gas_fee riskNote 提到 ETH / Arbitrum', () => {
    const note = GLOSSARY_BY_ID.gas_fee.riskNote
    expect(note).toContain('ETH')
    expect(note).toContain('Arbitrum')
  })

  it('unlock_sell_pressure riskNote 為中文', () => {
    const note = GLOSSARY_BY_ID.unlock_sell_pressure.riskNote
    expect(note).toBeTruthy()
    expect(note).toContain('解鎖')
  })
})

// ── 結構完整性 ──────────────────────────────────────────────
describe('glossary catalog — 結構完整性', () => {
  it('GLOSSARY_CATALOG 為非空陣列', () => {
    expect(GLOSSARY_CATALOG.length).toBeGreaterThan(0)
  })

  it('每個 term 都有 term_id、label、description', () => {
    for (const term of GLOSSARY_CATALOG) {
      expect(term.term_id).toBeTruthy()
      expect(typeof term.term_id).toBe('string')
      expect(term.label).toBeTruthy()
      expect(typeof term.label).toBe('string')
      expect(term.description).toBeTruthy()
      expect(typeof term.description).toBe('string')
    }
  })

  it('aliases 為字串陣列（可為空陣列）', () => {
    for (const term of GLOSSARY_CATALOG) {
      expect(Array.isArray(term.aliases)).toBe(true)
      for (const alias of term.aliases) {
        expect(typeof alias).toBe('string')
        expect(alias.trim().length).toBeGreaterThan(0)
      }
    }
  })

  it('audiences 為非空陣列，僅含合法值', () => {
    const valid: string[] = ['report', 'popover', 'help_center']
    for (const term of GLOSSARY_CATALOG) {
      expect(term.audiences.length, `${term.term_id} audiences 為空`).toBeGreaterThan(0)
      for (const a of term.audiences) {
        expect(valid, `${term.term_id} audience "${a}" 不合法`).toContain(a)
      }
    }
  })

  it('GLOSSARY_BY_ID 的所有 key 對應 GLOSSARY_CATALOG 的 term_id', () => {
    const catalogIds = new Set(GLOSSARY_CATALOG.map((t) => t.term_id))
    for (const id of Object.keys(GLOSSARY_BY_ID)) {
      expect(catalogIds.has(id as GlossaryCatalogTerm['term_id'])).toBe(true)
    }
    // 反過來也對：每個 catalog term 都在 BY_ID 中
    for (const term of GLOSSARY_CATALOG) {
      expect(GLOSSARY_BY_ID[term.term_id]).toBe(term)
    }
  })
})

// ── 別名與重複檢測 ──────────────────────────────────────────
describe('glossary catalog — 別名與重複 term_id 檢測', () => {
  it('validateGlossaryCatalog 拒絕重複的 term_id', () => {
    const duplicate = [
      { term_id: 'fdv', label: 'FDV', description: 'one', aliases: [], audiences: ['popover'] as const },
      { term_id: 'fdv', label: 'FDV2', description: 'two', aliases: [], audiences: ['popover'] as const },
    ] as unknown as GlossaryCatalogTerm[]
    expect(() => validateGlossaryCatalog(duplicate)).toThrow('duplicate glossary term_id')
  })

  it('validateGlossaryCatalog 拒絕跨詞彙的相同 alias（case-insensitive）', () => {
    const dupAlias = [
      { term_id: 'fdv', label: 'FDV', description: 'dup alias test', aliases: ['Same'], audiences: ['popover'] as const },
      { term_id: 'tvl', label: 'TVL', description: 'dup alias test', aliases: [' same '], audiences: ['popover'] as const },
    ] as unknown as GlossaryCatalogTerm[]
    expect(() => validateGlossaryCatalog(dupAlias)).toThrow('duplicate glossary alias')
  })

  it('validateGlossaryCatalog 拒絕 label 與其他 term 的 alias 重複', () => {
    const labelAliasConflict = [
      { term_id: 'fdv', label: 'X', description: 'a', aliases: [], audiences: ['popover'] as const },
      { term_id: 'tvl', label: 'Y', description: 'b', aliases: ['x'], audiences: ['popover'] as const },
    ] as unknown as GlossaryCatalogTerm[]
    expect(() => validateGlossaryCatalog(labelAliasConflict)).toThrow('duplicate glossary alias')
  })

  it('同一詞彙內可以自有 alias 指向自己 label（不觸發重複）', () => {
    const selfAliasOk = [
      { term_id: 'fdv', label: 'FDV', description: 'desc', aliases: ['fdv'], audiences: ['popover'] as const },
    ] as unknown as GlossaryCatalogTerm[]
    // label="FDV" canonical="fdv", alias="fdv" canonical="fdv" — same term_id → OK
    expect(() => validateGlossaryCatalog(selfAliasOk)).not.toThrow()
  })

  it('生產 GLOSSARY_CATALOG 已通過 validate — 無重複 term_id 或 alias', () => {
    // Already validated during module load; re-validate to confirm.
    const rechecked = validateGlossaryCatalog(GLOSSARY_CATALOG)
    expect(rechecked.length).toBe(GLOSSARY_CATALOG.length)
    expect(rechecked).toEqual(GLOSSARY_CATALOG)
  })

  it('生產 GLOSSARY_CATALOG 每個 term 的 aliases 不含自己的 label（避免多餘別名）', () => {
    for (const term of GLOSSARY_CATALOG) {
      const labelCanon = term.label.trim().toLocaleLowerCase().replace(/\s+/g, ' ')
      for (const alias of term.aliases) {
        const aliasCanon = alias.trim().toLocaleLowerCase().replace(/\s+/g, ' ')
        // 允許 label 與 alias 同字不同大小寫（如 "MC" → "market cap"）
        // 但不該有完全相同的 canonical
        expect(
          aliasCanon,
          `${term.term_id}: alias "${alias}" canonical 等同 label "${term.label}"`,
        ).not.toBe(labelCanon)
      }
    }
  })
})

// ── Help Center 篩選 ─────────────────────────────────────────
describe('glossary catalog — Help Center 匯出', () => {
  it('HELP_CENTER_GLOSSARY 只包含 audience 含 help_center 的 term', () => {
    const manual = GLOSSARY_CATALOG.filter((t) => t.audiences.includes('help_center'))
    expect(HELP_CENTER_GLOSSARY).toEqual(manual)
  })

  it('HELP_CENTER_GLOSSARY 為非空', () => {
    expect(HELP_CENTER_GLOSSARY.length).toBeGreaterThan(0)
  })

  it('每個 HELP_CENTER_GLOSSARY term 都有 where 欄位（供 Help Center 表格使用）', () => {
    for (const term of HELP_CENTER_GLOSSARY) {
      expect(
        term.where,
        `${term.term_id} 缺少 where 欄位（Help Center 表格需要）`,
      ).toBeTruthy()
    }
  })
})

// ── Regression: specific term content ────────────────────────
describe('glossary catalog — regression: 既有內容不變', () => {
  it('trustScore description 包含「不是價格漲跌機率」', () => {
    expect(GLOSSARY_BY_ID.trustScore.description).toContain('不是價格漲跌機率')
  })

  it('recency description 包含「獨立 0-100 子分數」', () => {
    expect(GLOSSARY_BY_ID.recency.description).toContain('獨立 0-100 子分數')
  })

  it('manipulation label 為「抗操縱能力」', () => {
    expect(GLOSSARY_BY_ID.manipulation.label).toBe('抗操縱能力')
  })

  it('divergence label 為「跨來源分歧」', () => {
    expect(GLOSSARY_BY_ID.divergence.label).toBe('跨來源分歧')
  })
})
