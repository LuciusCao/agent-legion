import type { JobNodeSummary } from '../jobTypes'
import styles from './JobNodeStepper.module.css'

export interface JobNodeStepperProps {
  nodeSummaries: JobNodeSummary[]
  activeNodeKey?: string | null
}

export function JobNodeStepper({
  nodeSummaries,
  activeNodeKey,
}: JobNodeStepperProps) {
  if (nodeSummaries.length === 0) {
    return <div className={styles.stepper}>—</div>
  }

  const activeSummary = activeNodeKey
    ? nodeSummaries.find((s) => s.node_key === activeNodeKey)
    : undefined

  return (
    <div className={styles.stepper}>
      {activeSummary && (
        <div className={styles.activeLabel}>当前：{activeSummary.label}</div>
      )}
      <div className={styles.track} role="list" aria-label="节点进度">
        {nodeSummaries.map((summary) => {
          const stateClass =
            styles[summary.status as keyof typeof styles] || styles.pending
          return (
            <div
              key={summary.node_key}
              className={`${styles.segment} ${stateClass}`}
              title={summary.label}
              data-status={summary.status}
              role="listitem"
            >
              <div
                className={`${styles.bar} ${
                  summary.status === 'running' ? 'pulse-blue' : ''
                }`.trim()}
              />
              <span className={styles.label}>{summary.label}</span>
              {summary.error_message && (
                <span className={styles.error}>{summary.error_message}</span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
