import type { NodeProps } from '@xyflow/react'
import type { DagNodeType } from './DagNode'

/**
 * #276 的 memo 比较函数：xyflow 的 NodeWrapper 只在节点对象引用变化时才把
 * 新 props 传进来，本身已挡掉未受影响节点的重渲染。这里把 memo 语义显式
 * 固定下来：data 引用相同时直接跳过；引用不同（DagGraph 只在 active/
 * dimmed 翻转时新建 data）则逐字段比较业务内容，高亮态变化必然触发渲染，
 * 其余字段按引用比较（inputs/outputs/topologyBadges 等数组由 computeLayout
 * 随 props 重建，引用比较即内容比较）。防止后续往 data 加字段时无意破坏
 * hover 的局部渲染（大图场景 hover 全量重渲染是 #276 要消除的渲染放大器）。
 */
export function dagNodePropsEqual(
  prevProps: NodeProps<DagNodeType>,
  nextProps: NodeProps<DagNodeType>
): boolean {
  if (
    prevProps.selected !== nextProps.selected ||
    prevProps.dragging !== nextProps.dragging
  ) {
    return false
  }
  const prevData = prevProps.data
  const nextData = nextProps.data
  if (prevData === nextData) return true
  if (
    prevData.active !== nextData.active ||
    prevData.dimmed !== nextData.dimmed
  ) {
    return false
  }
  return (
    prevData.label === nextData.label &&
    prevData.status === nextData.status &&
    prevData.nodeKey === nextData.nodeKey &&
    prevData.duration === nextData.duration &&
    prevData.executorKind === nextData.executorKind &&
    prevData.executorId === nextData.executorId &&
    prevData.agentId === nextData.agentId &&
    prevData.workerId === nextData.workerId &&
    prevData.capability === nextData.capability &&
    prevData.executorUnbound === nextData.executorUnbound &&
    prevData.topologyBadges === nextData.topologyBadges &&
    prevData.terminalOutcome === nextData.terminalOutcome &&
    prevData.changeType === nextData.changeType &&
    prevData.ghost === nextData.ghost &&
    prevData.inputs === nextData.inputs &&
    prevData.outputs === nextData.outputs
  )
}
