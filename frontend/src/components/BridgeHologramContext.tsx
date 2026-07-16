// oxlint-disable react/only-export-components
import { createContext, useContext, type ReactNode } from 'react'

export type BridgeHologramData = {
  question?: string
  analysisMode?: string
  snapshotAt?: string
  runId?: string
  primaryLabel?: string
  secondaryLabel?: string
  primaryValue?: number
  secondaryValue?: number
  total?: number
  points?: number[]
  status?: string
  trustScore?: number
  componentScores?: {
    reputation: number | null
    corroboration: number | null
    recency: number | null
    resistance: number | null
  }
  pipelineStages?: Array<{
    id: string
    label: string
    metric: string
    unit: string
    status: 'completed' | 'failed' | 'pending'
  }>
}

type BridgeHologramValue = {
  data: BridgeHologramData | null
  setData: (data: BridgeHologramData | null) => void
}

const BridgeHologramContext = createContext<BridgeHologramValue | null>(null)

export function BridgeHologramProvider({ value, children }: { value: BridgeHologramValue; children: ReactNode }) {
  return <BridgeHologramContext.Provider value={value}>{children}</BridgeHologramContext.Provider>
}

export function useBridgeHologram() {
  const value = useContext(BridgeHologramContext)
  if (!value) throw new Error('useBridgeHologram must be used inside BridgeHologramProvider')
  return value
}
