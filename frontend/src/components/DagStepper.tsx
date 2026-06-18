import type { JobNodeRecord } from '../types'
import styles from './DagStepper.module.css'

type StepState = 'completed' | 'running' | 'failed' | 'stale' | 'pending'

function getStepState(status: string): StepState {
  if (status === 'completed') return 'completed'
  if (status === 'running') return 'running'
  if (status === 'failed') return 'failed'
  // stale is visually treated as pending; only failed is red
  if (status === 'stale') return 'pending'
  return 'pending'
}

export function DagStepper({ nodes }: { nodes: JobNodeRecord[] }) {
  if (nodes.length === 0) return null

  return (
    <div className={styles.dagStepper}>
      {nodes.map((node) => {
        const state = getStepState(node.status)
        return (
          <div key={node.node_key} className={styles.step} title={node.label}>
            <div
              /* .pulse-blue is a global utility class defined in styles.css */
              className={`${styles.stepBar} ${styles[state]} ${state === 'running' ? 'pulse-blue' : ''}`}
            />
          </div>
        )
      })}
    </div>
  )
}
