// `/api/admin/*` 前端呼叫層測試（PR-4）：
//   1. 請求形狀——X-Admin-Token 一律走 header（絕不進 URL/query）、PUT 帶
//      Content-Type: application/json + expected_version、GET 不帶 body。
//   2. 回應驗證——AdminConfigData/AdminAuditData guard 擋契約漂移（管理面
//      顯示錯值比白屏更危險）。
//   3. 409 version_conflict 的 `current_version` 附帶欄位透傳（number/null
//      合法、畸形型別整包 parse_error，不讓假 version 進下一次 CAS）。

import { afterEach, describe, expect, it, vi } from 'vitest'
import { getAdminAudit, getAdminConfig, putAdminConfig } from './endpoints'
import type { AdminConfigData } from './types'

function jsonResponse(status: number, body: unknown): Response {
  return {
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response
}

/** 對齊後端 `web.py::_admin_config_view()` 的完整合法回應。 */
function validAdminConfigData(): AdminConfigData {
  return {
    daily_cap_usd: { config: 1.0, env: '5.0', default: 3.0, effective: 1.0, source: 'config' },
    bedrock_enabled: {
      config: false,
      bedrock_model_id_set: true,
      effective: false,
      source: 'config',
    },
    hermes_autonomy_enabled: {
      config: false,
      env: null,
      effective: false,
      source: 'config',
    },
    live_token: {
      config_configured: true,
      config_last4: 'ab12',
      env_configured: false,
      effective_configured: true,
      source: 'config',
    },
    version: 7,
    updated_at: '2026-07-07T03:00:00+00:00',
    updated_by: 'admin@203.0.113.5',
    exists: true,
    version_corrupt: false,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('admin endpoints — 請求形狀（token 走 header、PUT 帶 CAS body）', () => {
  it('getAdminConfig：GET、X-Admin-Token header、token 不出現在 URL', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { ok: true, data: validAdminConfigData() }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await getAdminConfig('secret-admin-token')
    expect(result.ok).toBe(true)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/admin/config')
    expect(url).not.toContain('secret-admin-token')
    expect(init.method).toBe('GET')
    expect((init.headers as Record<string, string>)['X-Admin-Token']).toBe('secret-admin-token')
    expect(init.body).toBeUndefined()
  })

  it('putAdminConfig：PUT、Content-Type json、body 含 changes + expected_version', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(200, { ok: true, data: { ...validAdminConfigData(), warnings: [] } }),
      )
    vi.stubGlobal('fetch', fetchMock)
    const result = await putAdminConfig('secret-admin-token', { daily_cap_usd: 2.5 }, 7)
    expect(result.ok).toBe(true)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/admin/config')
    expect(init.method).toBe('PUT')
    const headers = init.headers as Record<string, string>
    expect(headers['X-Admin-Token']).toBe('secret-admin-token')
    expect(headers['Content-Type']).toBe('application/json')
    expect(JSON.parse(init.body as string)).toEqual({ daily_cap_usd: 2.5, expected_version: 7 })
  })

  it('putAdminConfig：live_token: null（清除）要保留在 body（不能被序列化丟掉）', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(200, { ok: true, data: { ...validAdminConfigData(), warnings: [] } }),
      )
    vi.stubGlobal('fetch', fetchMock)
    await putAdminConfig('t', { live_token: null }, 3)
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({ live_token: null, expected_version: 3 })
  })

  it('getAdminAudit：GET、X-Admin-Token header', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { ok: true, data: { limit: 50, records: [] } }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await getAdminAudit('tok')
    expect(result.ok).toBe(true)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/admin/audit')
    expect((init.headers as Record<string, string>)['X-Admin-Token']).toBe('tok')
  })
})

describe('admin endpoints — 回應驗證（契約漂移擋下、不進表單）', () => {
  it('完整合法 GET 回應 → 正常放行', async () => {
    const data = validAdminConfigData()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await getAdminConfig('t')
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.data).toEqual(data)
  })

  it('item 尚不存在（exists=false、version/updated_* 全 null）→ 合法放行', async () => {
    const data: AdminConfigData = {
      ...validAdminConfigData(),
      daily_cap_usd: { config: null, env: null, default: 3.0, effective: 3.0, source: 'default' },
      bedrock_enabled: {
        config: null,
        bedrock_model_id_set: false,
        effective: false,
        source: 'env',
      },
      live_token: {
        config_configured: false,
        config_last4: null,
        env_configured: false,
        effective_configured: false,
        source: 'none',
      },
      version: null,
      updated_at: null,
      updated_by: null,
      exists: false,
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await getAdminConfig('t')
    expect(result.ok).toBe(true)
  })

  it('daily_cap_usd.effective 缺席 → parse_error（生效值是誠實顯示核心，不許半成品）', async () => {
    const data = validAdminConfigData() as unknown as Record<string, Record<string, unknown>>
    delete data.daily_cap_usd.effective
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await getAdminConfig('t')
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('live_token 帶非預期形狀（config_configured 非 bool）→ parse_error', async () => {
    const data = validAdminConfigData() as unknown as Record<string, Record<string, unknown>>
    data.live_token.config_configured = 'yes'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await getAdminConfig('t')
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('PUT 回應 warnings 非字串陣列 → parse_error（頁面逐條渲染，不能炸）', async () => {
    const data = { ...validAdminConfigData(), warnings: [123] }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await putAdminConfig('t', { bedrock_enabled: true }, 7)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('audit：合法紀錄（含 token 遮罩 change）放行', async () => {
    const data = {
      limit: 50,
      records: [
        {
          ts: '2026-07-07T03:00:00+00:00',
          actor: 'admin@203.0.113.5',
          changes: [
            { field: 'daily_cap_usd', old: 3.0, new: 1.0 },
            { field: 'live_token', old: '<set>', new: '<rotated last4=ab12>' },
          ],
          version_from: 6,
          version_to: 7,
          user_agent: 'curl/8.0',
        },
      ],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await getAdminAudit('t')
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.data.records).toHaveLength(1)
  })

  it('audit：records 元素缺 field → parse_error', async () => {
    const data = {
      limit: 50,
      records: [
        {
          ts: null,
          actor: null,
          changes: [{ old: 1, new: 2 }],
          version_from: null,
          version_to: null,
          user_agent: null,
        },
      ],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true, data })))
    const result = await getAdminAudit('t')
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })
})

describe('admin endpoints — 409 version_conflict 的 current_version 透傳', () => {
  it('current_version 是 number → 原樣透傳給呼叫端', async () => {
    const envelope = {
      ok: false,
      error: { code: 'version_conflict', message: '設定已被他人變更', current_version: 9 },
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(409, envelope)))
    const result = await putAdminConfig('t', { daily_cap_usd: 2 }, 7)
    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.error.code).toBe('version_conflict')
      expect(result.error.current_version).toBe(9)
    }
  })

  it('current_version 是 null（後端重讀失敗）→ 合法，仍是 version_conflict', async () => {
    const envelope = {
      ok: false,
      error: { code: 'version_conflict', message: '設定已被他人變更', current_version: null },
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(409, envelope)))
    const result = await putAdminConfig('t', { daily_cap_usd: 2 }, 7)
    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.error.code).toBe('version_conflict')
      expect(result.error.current_version).toBeNull()
    }
  })

  it('current_version 型別畸形（字串）→ parse_error，不讓假 version 進下一次 CAS', async () => {
    const envelope = {
      ok: false,
      error: { code: 'version_conflict', message: '衝突', current_version: '9' },
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(409, envelope)))
    const result = await putAdminConfig('t', { daily_cap_usd: 2 }, 7)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error.code).toBe('parse_error')
  })

  it('401 unauthorized 信封原樣透傳（token 閘顯錯用）', async () => {
    const envelope = { ok: false, error: { code: 'unauthorized', message: '未授權' } }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(401, envelope)))
    const result = await getAdminConfig('wrong-token')
    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.error.code).toBe('unauthorized')
      expect(result.error.message).toBe('未授權')
    }
  })
})
