import { useEffect, useState } from 'react'
import { getAgentCoreStatus, type AgentCoreStatusData } from '../lib/endpoints'

type ViewState = AgentCoreStatusData['state'] | 'checking' | 'unavailable'

export default function AgentCoreStatusBadge({
  locale,
}: {
  locale: 'zh-TW' | 'en'
}) {
  const [state, setState] = useState<ViewState>('checking')

  useEffect(() => {
    const controller = new AbortController()
    getAgentCoreStatus(controller.signal)
      .then((result) => {
        setState(result.ok && result.data ? result.data.state : 'unavailable')
      })
      .catch(() => setState('unavailable'))
    return () => controller.abort()
  }, [])

  const label =
    state === 'configured'
      ? locale === 'zh-TW'
        ? 'AgentCore 已選用'
        : 'AgentCore selected'
      : state === 'misconfigured'
        ? locale === 'zh-TW'
          ? 'AgentCore 尚未設定'
          : 'AgentCore needs setup'
        : state === 'inactive'
          ? locale === 'zh-TW'
            ? '內建執行層'
            : 'Builtin runtime'
          : state === 'checking'
            ? locale === 'zh-TW'
              ? '檢查執行層'
              : 'Runtime checking'
            : locale === 'zh-TW'
              ? '執行層狀態無法取得'
              : 'Runtime unavailable'

  return (
    <div
      className={`agentcore-status agentcore-status--${state}`}
      role="status"
      aria-live="polite"
      data-testid="agentcore-status"
    >
      <span aria-hidden="true" />
      {label}
    </div>
  )
}
