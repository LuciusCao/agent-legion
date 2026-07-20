import type { DagNodeData } from './DagNode'
import styles from './DagNode.module.css'

// Short chip label for a worker id: strip any "worker-" prefix, keep 8 chars.
export function shortWorkerId(workerId: string): string {
  return workerId.replace(/^worker[-_]?/i, '').slice(0, 8)
}

export function DagNodeExecutorBadge({ data }: { data: DagNodeData }) {
  const { executorId, workerId } = data
  if (!workerId && !executorId) return null
  const title = workerId
    ? [executorId, workerId].filter(Boolean).join(' / ')
    : (executorId ?? undefined)
  return (
    <span
      data-testid="dag-node-executor-badge"
      className={styles.workerTag}
      title={title}
    >
      {workerId ? shortWorkerId(workerId) : executorId}
    </span>
  )
}
