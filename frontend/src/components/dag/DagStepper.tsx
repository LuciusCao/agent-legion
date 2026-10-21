import type { JobNode } from '../../types/jobTypes'
import { stepperTrackGap } from '../../lib/stepperLayout'
import { StepperCompactView } from './StepperCompactView'
import { useStepperCompactMode } from './useStepperCompactMode'
import styles from './DagStepper.module.css'

// 实测宽度不可得（无 ResizeObserver 环境）时间距计算的回落宽度，
// 与 DagStepper.module.css 的 .dagStepper width 保持一致。
const FALLBACK_TRACK_WIDTH_PX = 200

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
  /** 显式覆盖紧凑判定（节点数阈值，#1257）；缺省按轨道实测宽度动态
   * 计算（#1335）。 */
  compactThreshold?: number
}

export function DagStepper({ nodes, compactThreshold }: DagStepperProps) {
  const { containerRef, compact, trackWidthPx } = useStepperCompactMode(
    nodes.length,
    compactThreshold
  )
  if (nodes.length === 0) return null

  // 紧凑形态（#1257）：面板头部没有别处展示当前节点名，故内联。
  if (compact) {
    return (
      <div ref={containerRef} className={styles.dagStepper} data-compact="true">
        <StepperCompactView segments={nodes} showLabel />
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className={styles.dagStepper}
      style={{
        gap: stepperTrackGap(
          nodes.length,
          trackWidthPx ?? FALLBACK_TRACK_WIDTH_PX
        ),
      }}
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
