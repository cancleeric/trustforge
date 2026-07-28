import { useEffect, useState } from 'react'
import { getAgentCoreStatus, type AgentCoreStatusData } from '../lib/endpoints'

type ViewState = AgentCoreStatusData['state'] | 'checking' | 'unavailable'

export default function AgentCoreStatusBadge() {
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
      ? 'AgentCore configured'
      : state === 'misconfigured'
        ? 'AgentCore needs setup'
        : state === 'inactive'
          ? 'Builtin runtime'
          : state === 'checking'
            ? 'Runtime checking'
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
