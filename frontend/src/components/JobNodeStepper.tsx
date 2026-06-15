import type { JobNodeSummary } from '../jobTypes'
import styles from './JobNodeStepper.module.css'

export interface JobNodeStepperProps {
  nodeSummaries: JobNodeSummary[]
  activeNodeKey?: string | null
  totalNodes?: number
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
          label: '',
          status: 'pending' as const,
          error_message: '',
        }))

  return (
    <div className={styles.stepper}>
      <div className={styles.track} role="list" aria-label="节点进度">
        {segments.map((summary) => {
          const stateClass =
            styles[summary.status as keyof typeof styles] || styles.pending
          const isActive = summary.node_key === activeNodeKey
          return (
            <div
              key={summary.node_key}
              className={`${styles.segment} ${stateClass}`}
              title={summary.label || undefined}
              data-status={summary.status}
              data-active={isActive || undefined}
              role="listitem"
            >
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
