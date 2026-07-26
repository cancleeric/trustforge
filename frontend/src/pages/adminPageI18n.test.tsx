// @vitest-environment jsdom

import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { HermesI18nProvider, useHermesI18n } from '../hermes/hermesI18n'
import AdminPage from './AdminPage'
import type { AdminConfigData } from '../lib/types'

vi.mock('../lib/adminConsole', async () => {
  const actual = await vi.importActual<typeof import('../lib/adminConsole')>('../lib/adminConsole')
  return {
    ...actual,
    loadSessionToken: vi.fn(() => 'test-token'),
    saveSessionToken: vi.fn(),
    clearSessionToken: vi.fn(),
  }
})

function makeBaseConfig(): AdminConfigData {
  return {
    daily_cap_usd: { config: 5, env: null, default: 3, effective: 5, source: 'config' },
    bedrock_enabled: { config: false, bedrock_model_id_set: true, effective: false, source: 'config' },
    hermes_autonomy_enabled: { config: null, env: null, effective: true, source: 'default' },
    live_token: { config_configured: false, config_last4: null, env_configured: false, effective_configured: false, source: 'default' },
    version: 1,
    updated_at: null,
    updated_by: null,
    exists: true,
    version_corrupt: false,
  }
}

vi.mock('../lib/endpoints', () => ({
  getAdminConfig: vi.fn().mockResolvedValue({ ok: true, data: makeBaseConfig() }),
  getAdminAudit: vi.fn().mockResolvedValue({ ok: true, data: { records: [] } }),
  getAdminBackendProviders: vi.fn().mockResolvedValue({
    ok: true,
    data: {
      kind: 'backend_provider_registry',
      providers: {
        memory: 'builtin', policy: 'builtin', eval: 'builtin', llm: 'builtin',
        gateway: 'builtin', observability: 'builtin', upgrade: 'builtin',
      },
      valid_providers: ['builtin', 'agentcore'],
      provider_keys: ['memory', 'policy', 'eval', 'llm', 'gateway', 'observability', 'upgrade'],
      hot_config: true,
      restart_required: false,
    },
  }),
  putAdminConfig: vi.fn(),
  setAdminBackendProvider: vi.fn(),
  setAllAdminBackendProviders: vi.fn(),
}))

function LocaleSwitcher() {
  const { setLocale } = useHermesI18n()
  return (
    <div>
      <button onClick={() => setLocale('en')}>use English</button>
      <button onClick={() => setLocale('zh-TW')}>使用中文</button>
    </div>
  )
}

describe('AdminPage i18n', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.cookie = 'trustforge_hermes_locale=; Max-Age=0'
  })

  it('renders admin console strings in zh-TW then switches to English on the same DOM', async () => {
    render(
      <HermesI18nProvider>
        <LocaleSwitcher />
        <AdminPage />
      </HermesI18nProvider>,
    )

    await waitFor(() => expect(screen.getByRole('heading', { name: '管理控制台' })).toBeInTheDocument())
    expect(screen.getByText('每日 Bedrock 花費上限（USD）')).toBeInTheDocument()
    expect(screen.getByText('Bedrock 真呼叫開關')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '鎖定並清除 token' })).toBeInTheDocument()
    expect(screen.getByText('設定變更審計（近 50 筆）')).toBeInTheDocument()
    expect(screen.getByText('目前尚無設定變更紀錄。')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'use English' }))

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Admin Console' })).toBeInTheDocument())
    expect(screen.getByText('Daily Bedrock Spend Cap (USD)')).toBeInTheDocument()
    expect(screen.getByText('Real Bedrock Call Switch')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Lock and clear token' })).toBeInTheDocument()
    expect(screen.getByText('Configuration Change Audit (last 50)')).toBeInTheDocument()
    expect(screen.getByText('No configuration change records yet.')).toBeInTheDocument()
    expect(screen.queryByText('管理控制台')).not.toBeInTheDocument()
  })
})
