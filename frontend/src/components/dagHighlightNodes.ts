import type { Edge, Node } from '@xyflow/react'
import type { DagNodeData } from './dag/dagNodeTypes'
import { collectAncestors, collectDescendants } from './dagGraphRelations'

export function collectHighlightRelations(
  activeNode: string,
  edgeBySource: Record<string, Edge[]>,
  edgeByTarget: Record<string, Edge[]>
) {
  const ancestors = new Set<string>()
  const descendants = new Set<string>()
  collectAncestors(activeNode, edgeByTarget, ancestors)
  collectDescendants(activeNode, edgeBySource, descendants)
  // 与选中节点同链路（自身/祖先/后代）的节点保持全亮，其余节点置灰。
  // activeNode 自身也进 highlighted，保证从 activeNode 出发无法回溯自身
  // 的退化图（如仅剩孤立节点）仍能正确全亮。
  const highlightedNodeIds = new Set<string>([
    activeNode,
    ...ancestors,
    ...descendants,
  ])
  return { ancestors, descendants, highlightedNodeIds }
}

export function applyNodeHighlight(
  rfNodes: Node<DagNodeData>[],
  activeNode: string | null,
  highlightedNodeIds: ReadonlySet<string>,
  prevNodeData: ReadonlyMap<string, DagNodeData>
): Node<DagNodeData>[] {
  return rfNodes.map((node) => {
    const active = node.id === activeNode
    const shouldDim = activeNode !== null && !highlightedNodeIds.has(node.id)
    const prevData = prevNodeData.get(node.id)
    if (
      (prevData?.dimmed === true) === shouldDim &&
      (prevData?.active === true) === active
    ) {
      return node
    }
    return {
      ...node,
      data: { ...node.data, active, dimmed: shouldDim },
    }
  })
}

/**
 * hover 链式状态机（Codex review on #285 的 P2-3）：prevActiveNode 与
 * hoveredNode 必须原子更新——两个独立 useState 各自调度会引入一轮多余的
 * 全量重算（render 计数测试能捕获），reducer 单次 dispatch 单轮渲染。
 * 重复 enter 同一节点 / 无 hover 时 leave 都是 no-op（返回原 state 引用，
 * React 直接 bail out）。
 */
export interface HoverState {
  hoveredNode: string | null
  prevActiveNode: string | null
}

export type HoverAction = { type: 'enter'; id: string } | { type: 'leave' }

export function hoverReducer(
  state: HoverState,
  action: HoverAction
): HoverState {
  switch (action.type) {
    case 'enter':
      if (action.id === state.hoveredNode) return state
      return {
        hoveredNode: action.id,
        prevActiveNode: state.hoveredNode,
      }
    case 'leave':
      if (state.hoveredNode === null) return state
      return { hoveredNode: null, prevActiveNode: state.hoveredNode }
  }
}
