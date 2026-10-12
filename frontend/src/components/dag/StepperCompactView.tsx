import styles from './StepperCompactView.module.css'

// JobNodeStepper / DagStepper 共用的紧凑形态（#1257）：节点数超过
// STEPPER_COMPACT_THRESHOLD 时逐段渲染失去辨识度，切换为「数字进度
// current/total（当前节点状态色）+ 细进度条（填充 = completed/total）」，
// DOM 规模与节点数无关。当前节点高亮的等价物是 count 本身（它即当前节点
// 位置与状态）；running 脉冲从分段 bar 移到填充条。原形态没有点击/跳转
// 交互（仅 title tooltip），紧凑形态无需降级等价物。

export interface StepperCompactSegment {
  node_key: string
  label: string
  status: string
}

export interface StepperCompactInfo {
  /** 当前节点的 1-based 位置（即 12/57 中的 12）。 */
  current: number
  total: number
  /** status === 'completed' 的节点数，用于细进度条填充比例。 */
  completed: number
  currentKey: string
  currentLabel: string
  currentStatus: string
}

// 「当前节点」解析顺序：调用方高亮（activeNodeKey）→ running → failed →
// stale → 首个 pending（下一个待跑）→ 全部完成/兜底取末节点。
export function stepperCompactInfo(
  segments: readonly StepperCompactSegment[],
  activeNodeKey?: string | null
): StepperCompactInfo | null {
  if (segments.length === 0) return null
  const total = segments.length
  const completed = segments.filter((s) => s.status === 'completed').length

  let index = activeNodeKey
    ? segments.findIndex((s) => s.node_key === activeNodeKey)
    : -1
  if (index < 0) index = segments.findIndex((s) => s.status === 'running')
  if (index < 0) index = segments.findIndex((s) => s.status === 'failed')
  if (index < 0) index = segments.findIndex((s) => s.status === 'stale')
  if (index < 0) index = segments.findIndex((s) => s.status === 'pending')
  if (index < 0) index = total - 1

  const current = segments[index]
  return {
    current: index + 1,
    total,
    completed,
    currentKey: current.node_key,
    currentLabel: current.label,
    currentStatus: current.status,
  }
}

export interface StepperCompactViewProps {
  segments: readonly StepperCompactSegment[]
  activeNodeKey?: string | null
  /** 是否内联当前节点名。JobNodeStepper 的消费方 JobListItem 已在相邻
   * activeLabel 渲染当前节点名（176px 行槽内联会重复），传 false 仅经
   * title / aria-valuetext 暴露；DagStepper 所在面板头部无别处展示，传
   * true 内联。 */
  showLabel: boolean
}

export function StepperCompactView({
  segments,
  activeNodeKey,
  showLabel,
}: StepperCompactViewProps) {
  const info = stepperCompactInfo(segments, activeNodeKey)
  if (!info) return null
  const percent = (info.completed / info.total) * 100

  return (
    <div className={styles.compact}>
      <div className={styles.compactRow}>
        <span
          className={styles.compactCount}
          data-status={info.currentStatus}
          data-active={
            activeNodeKey != null && info.currentKey === activeNodeKey
              ? true
              : undefined
          }
          title={info.currentLabel}
        >
          {info.current}/{info.total}
        </span>
        {showLabel && (
          <span className={styles.compactLabel} title={info.currentLabel}>
            {info.currentLabel}
          </span>
        )}
      </div>
      <div
        className={styles.compactTrack}
        role="progressbar"
        aria-label="节点进度"
        aria-valuemin={0}
        aria-valuemax={info.total}
        aria-valuenow={info.completed}
        aria-valuetext={`${info.current}/${info.total} ${info.currentLabel}`}
      >
        {/* .pulse-blue is a global utility class defined in styles.css */}
        <div
          className={`${styles.compactFill} ${
            info.currentStatus === 'running' ? 'pulse-blue' : ''
          }`.trim()}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  )
}
