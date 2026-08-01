import type { ReactNode } from 'react'

interface Props {
  title: string
  count?: number | string
  defaultOpen?: boolean
  children: ReactNode
}

export default function CollapsibleSection({ title, count, defaultOpen = false, children }: Props) {
  return (
    <details className="trustforge-collapse" open={defaultOpen}>
      <summary>
        {title}{count !== undefined && <span style={{ opacity: 0.6, fontSize: '0.9em' }}>（{count}）</span>}
      </summary>
      <div className="trustforge-collapse-content">
        {children}
      </div>
    </details>
  )
}
