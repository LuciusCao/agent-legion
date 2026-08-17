import type { DagNodeData } from './DagNode'
import styles from './DagNode.module.css'

// Short chip label for a worker id: strip any "worker-" prefix, keep 8 chars.
export function shortWorkerId(workerId: string): string {
  return workerId.replace(/^worker[-_]?/i, '').slice(0, 8)
}

export function DagNodeExecutionBadge({ data }: { data: DagNodeData }) {
  const { agentId, executorId, workerId } = data
  if (!agentId && !workerId && !executorId) return null
  const title = [agentId, executorId, workerId].filter(Boolean).join(' / ')
  const label = workerId ? shortWorkerId(workerId) : (agentId ?? executorId)
  return (
    <span
      data-testid="dag-node-execution-badge"
      className={styles.workerTag}
      title={title}
    >
      {label}
    </span>
  )
}
