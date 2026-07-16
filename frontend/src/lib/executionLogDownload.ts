import type { ExecutionEvent, ExecutionManifest } from './types'

export function executionLogJson(execution: ExecutionManifest, events: ExecutionEvent[]): string {
  return JSON.stringify({ execution, events }, null, 2)
}

export function executionLogDownload(execution: ExecutionManifest, events: ExecutionEvent[]) {
  return {
    name: `${execution.run_id}-execution-log.json`,
    body: executionLogJson(execution, events),
    type: 'application/json',
  }
}
