import type { DagNodeStatus } from '../dagNodeStatus'
import type { DagNodeChangeType } from './DagGraph'
import type { ExecutorKind } from '../../types/jobTypes'

export interface DagNodeData extends Record<string, unknown> {
  label: string
  status: DagNodeStatus
  duration?: number
  executorKind?: ExecutorKind | null
  executorId?: string | null
  agentId?: string | null
  workerId?: string | null
  nodeKey?: string
  capability?: string
  executorUnbound?: boolean
  topologyBadges?: Array<'entry' | 'branch' | 'terminal'>
  terminalOutcome?: string
  inputs: string[]
  outputs: string[]
  changeType?: DagNodeChangeType
  ghost?: boolean
}
