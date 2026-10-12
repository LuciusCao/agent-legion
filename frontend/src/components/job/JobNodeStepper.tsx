import type { JobNodeSummary } from '../../types/jobTypes'
import { isStepperCompact, stepperTrackGap } from '../../lib/stepperLayout'
import { StepperCompactView } from '../dag/StepperCompactView'
import styles from './JobNodeStepper.module.css'

// 与 JobNodeStepper.module.css 的 .stepper width 保持一致。
const TRACK_WIDTH_PX = 176

export interface JobNodeStepperProps {
  nodeSummaries: JobNodeSummary[]
  activeNodeKey?: string | null
  totalNodes?: number
  /** 超过该节点数切换紧凑形态（#1257）；默认 STEPPER_COMPACT_THRESHOLD。 */
  compactThreshold?: number
}

const STATUS_CLASSES: Record<JobNodeSummary['status'], string> = {
  completed: styles.completed,
  running: styles.running,
  failed: styles.failed,
  stale: styles.stale,
  pending: styles.pending,
  // #1277：与 StepperCompactView（#1275）同色——ready 沿用 running 蓝
  // （同 DagNode），awaiting_approval 用审批紫。
  awaiting_approval: styles.awaitingApproval,
  ready: styles.ready,
}

export function JobNodeStepper({
  nodeSummaries,
  activeNodeKey,
  totalNodes = 0,
  compactThreshold,
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

  // 紧凑形态（#1257）：超阈值后逐段失去辨识度，改数字进度 + 细进度条。
  // 当前节点名不内联——唯一消费方 JobListItem 已在相邻 activeLabel 渲染
  // 当前节点 label，176px 行槽内联会重复（名字经 title / aria 暴露）。
  if (isStepperCompact(segments.length, compactThreshold)) {
    return (
      <div className={styles.stepper} data-compact="true">
        <StepperCompactView
          segments={segments}
          activeNodeKey={activeNodeKey}
          showLabel={false}
        />
      </div>
    )
  }

  return (
    <div className={styles.stepper}>
      <div
        className={styles.track}
        style={{ gap: stepperTrackGap(segments.length, TRACK_WIDTH_PX) }}
        role="list"
        aria-label="节点进度"
      >
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
                data-status={summary.status}
              />
            </div>
          )
        })}
      </div>
    </div>
  )
}
