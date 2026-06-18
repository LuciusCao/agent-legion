import type { JobNodeSummary } from '../jobTypes'
import styles from './JobNodeStepper.module.css'

export interface JobNodeStepperProps {
  nodeSummaries: JobNodeSummary[]
  activeNodeKey?: string | null
  totalNodes?: number
}

const STATUS_CLASSES: Record<JobNodeSummary['status'], string> = {
  completed: styles.completed,
  running: styles.running,
  failed: styles.failed,
  stale: styles.stale,
  pending: styles.pending,
}

export function JobNodeStepper({
  nodeSummaries,
  activeNodeKey,
  totalNodes = 0,
}: JobNodeStepperProps) {
  if (nodeSummaries.length === 0 && totalNodes <= 0) {
    return <div className={styles.stepper}>—</div>
  }

  const segments =
    nodeSummaries.length > 0
      ? nodeSummaries
      : Array.from({ length: totalNodes }, (_, index) => ({
          node_key: `pending-${index}`,
          label: `节点 ${index + 1}`,
          status: 'pending' as const,
          error_message: '',
        }))

  return (
    <div className={styles.stepper}>
      <div className={styles.track} role="list" aria-label="节点进度">
        {segments.map((summary) => {
          const stateClass = STATUS_CLASSES[summary.status] || styles.pending
          const isActive = summary.node_key === activeNodeKey
          return (
            <div
              key={summary.node_key}
              className={`${styles.segment} ${stateClass}`}
              title={summary.label || undefined}
              data-status={summary.status}
              data-active={isActive || undefined}
              role="listitem"
              aria-label={
                summary.label
                  ? `${summary.label}: ${summary.status}`
                  : undefined
              }
            >
              {/* .pulse-blue is a global utility class defined in styles.css */}
              <div
                className={`${styles.bar} ${
                  summary.status === 'running' ? 'pulse-blue' : ''
                }`.trim()}
              />
            </div>
          )
        })}
      </div>
    </div>
  )
}
