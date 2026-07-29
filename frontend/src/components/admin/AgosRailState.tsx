interface AgosRailStateProps {
  kind: 'loading' | 'empty' | 'error' | 'unauthorized'
  message?: string
}

export function AgosRailState({ kind, message }: AgosRailStateProps) {
  if (kind === 'loading') {
    return (
      <div role="status" aria-label="Loading Agent OS data" className="animate-pulse space-y-3">
        {[1, 2, 3].map(i => <div key={i} className="h-24 rounded-lg bg-gray-100" />)}
      </div>
    )
  }

  const unauthorized = kind === 'unauthorized'
  return (
    <div
      role={kind === 'error' || unauthorized ? 'alert' : 'status'}
      className={`rounded-lg border p-4 text-sm ${
        kind === 'error' || unauthorized
          ? 'border-red-200 bg-red-50 text-red-800'
          : 'border-gray-200 bg-gray-50 text-gray-600'
      }`}
    >
      {message || (unauthorized
        ? 'Admin authorization is required. Open the Admin console and provide a valid token.'
        : 'No records found for this run.')}
    </div>
  )
}
