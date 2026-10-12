import type { JobNode } from '../../types/jobTypes'
import { isStepperCompact, stepperTrackGap } from '../../lib/stepperLayout'
import { StepperCompactView } from './StepperCompactView'
import styles from './DagStepper.module.css'

// 与 DagStepper.module.css 的 .dagStepper width 保持一致。
const TRACK_WIDTH_PX = 200

type StepState = 'completed' | 'running' | 'failed' | 'stale' | 'pending'

function getStepState(status: string): StepState {
  if (status === 'completed') return 'completed'
  if (status === 'running') return 'running'
  if (status === 'failed') return 'failed'
  // stale is visually treated as pending; only failed is red
  if (status === 'stale') return 'pending'
  return 'pending'
}

export interface DagStepperProps {
  nodes: JobNode[]
  /** 超过该节点数切换紧凑形态（#1257）；默认 STEPPER_COMPACT_THRESHOLD。 */
  compactThreshold?: number
}

export function DagStepper({ nodes, compactThreshold }: DagStepperProps) {
  if (nodes.length === 0) return null

  // 紧凑形态（#1257）：面板头部没有别处展示当前节点名，故内联。
  if (isStepperCompact(nodes.length, compactThreshold)) {
    return (
      <div className={styles.dagStepper} data-compact="true">
        <StepperCompactView segments={nodes} showLabel />
      </div>
    )
  }

  return (
    <div
      className={styles.dagStepper}
      style={{ gap: stepperTrackGap(nodes.length, TRACK_WIDTH_PX) }}
    >
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
