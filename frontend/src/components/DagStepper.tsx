import type { JobNodeRecord } from '../types'
import styles from './DagStepper.module.css'

type StepState = 'completed' | 'running' | 'failed' | 'pending'

function getStepState(status: string): StepState {
  if (status === 'completed') return 'completed'
  if (status === 'running') return 'running'
  if (status === 'failed') return 'failed'
  return 'pending'
}

export function DagStepper({ nodes }: { nodes: JobNodeRecord[] }) {
  if (nodes.length === 0) return null
  const compact = nodes.length > 8

  return (
    <div className={`${styles.dagStepper} ${compact ? styles.compact : ''}`}>
      {nodes.map((node) => {
        const state = getStepState(node.status)
        return (
          <div key={node.node_key} className={styles.step} title={node.label}>
            <div
              /* .pulse-blue is a global utility class defined in styles.css */
              className={`${styles.stepBar} ${styles[state]} ${state === 'running' ? 'pulse-blue' : ''}`}
            />
            <span className={styles.stepLabel}>{node.label}</span>
          </div>
        )
      })}
    </div>
  )
}
