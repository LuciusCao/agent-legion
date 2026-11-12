// JobNodeStepper / DagStepper 共用的 track 间距计算（#1124）：flex gap
// 不可收缩，节点数 ~45+ 时 gap 总量超过固定宽 track，把每段 bar 压成 0 宽
// 并向右溢出。按段数收缩 gap，保证每段至少 STEPPER_MIN_SEGMENT_PX 可见；
// 段数多到 2px 都放不下时 gap 归零，段宽交给 flex 均分继续收缩。

export const STEPPER_MAX_GAP_PX = 4
export const STEPPER_MIN_SEGMENT_PX = 2

export function stepperTrackGap(
  segmentCount: number,
  trackWidthPx: number
): number {
  if (segmentCount <= 1) return STEPPER_MAX_GAP_PX
  const budget = trackWidthPx - STEPPER_MIN_SEGMENT_PX * segmentCount
  if (budget <= 0) return 0
  return Math.min(STEPPER_MAX_GAP_PX, Math.floor(budget / (segmentCount - 1)))
}

// 紧凑形态阈值（#1257）：节点数超过该值时逐段渲染失去辨识度（100+ 节点
// 每段不足 2px），stepper 切换为紧凑形态（渲染见
// components/dag/StepperCompactView）。与 #1124 的出事阈值对齐；
// 两个 stepper 组件接受 compactThreshold prop 覆盖。
export const STEPPER_COMPACT_THRESHOLD = 50

export function isStepperCompact(
  segmentCount: number,
  threshold: number = STEPPER_COMPACT_THRESHOLD
): boolean {
  return segmentCount > Math.max(0, threshold)
}
