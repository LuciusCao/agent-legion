import { Edge, MarkerType, Node } from '@xyflow/react'
import type { DagNodeData } from './dag/dagNodeTypes'
import {
  buildRelationMaps,
  collectAncestors,
  collectDescendants,
} from './dagGraphRelations'

/**
 * #276 的 hover/选中高亮计算（从 DagGraph.tsx 抽出，纯函数）：
 * 高亮态下沉为 node.data.active / node.data.dimmed / edge.data.highlighted
 * 布尔字段，由 DagNode / DagEdge 组件自行渲染样式；这里只替换「高亮态
 * 实际翻转」的少数条目，其余 node/edge/data 对象引用原样复用。
 *
 * xyflow 的增量管道（ReactFlow → setNodes → adoptUserNodes(checkEquality)
 * → zustand nodeLookup/edgeLookup → NodeWrapper/EdgeWrapper）对引用不变的
 * 对象零通知：internal node 原对象复用 → NodeWrapper 的 useStore(shallow)
 * 不触发 → memo(NodeWrapper) 不渲染 → 自定义组件不执行。于是 hover 的
 * 渲染面从 O(全部节点+边)（旧版全量 .map + spread 重建）收敛到
 * O(高亮态翻转的节点+边 + 祖先/后代遍历)。
 *
 * 链式输入（Codex review on #285）：`prev` 是上一次高亮的结果。判断
 * 「条目是否翻转」必须对照上一次的视觉态（prev 里的 data 高亮位），而非
 * 未高亮的基线——从节点 A hover 到 B 时，仍应置灰的节点在基线里
 * dimmed=false 但视觉上已是 true，对照基线会把它们全部重建，hover 在
 * 大图上退回 O(全部非同链路节点)。position 等用户态字段始终取基线
 * （rfNodes/rfEdges 是拖拽后的最新形状），只沿用 prev 的 data 高亮位。
 */
export function applyHighlight(
  rfNodes: Node<DagNodeData>[],
  rfEdges: Edge[],
  activeNode: string | null,
  prev?: {
    highlightedNodes: Node<DagNodeData>[]
    highlightedEdges: Edge[]
  }
): { highlightedNodes: Node<DagNodeData>[]; highlightedEdges: Edge[] } {
  const prevNodeData = new Map(
    (prev?.highlightedNodes ?? rfNodes).map((node) => [node.id, node.data])
  )
  const prevEdgeData = new Map(
    (prev?.highlightedEdges ?? rfEdges).map((edge) => [edge.id, edge.data])
  )

  if (!activeNode) {
    // 全图常态：凡 data.highlighted 已定义（处于/曾处于高亮模式）的边都
    // 归位为 undefined（从未进入高亮模式）并还原默认 marker 颜色，DagEdge
    // 据此透传 buildRfEdges 的原始 style（#668：常态是加深的全亮描边，
    // 不是置灰态；只归位 true 会把 hover 时置灰的 false 边永远留在置灰
    // 视觉）。对照 prev 判断翻转而非基线——从「hover 中」到「移出」也只
    // 重建高亮过的条目。
    return {
      highlightedEdges: rfEdges.map((edge) =>
        prevEdgeData.get(edge.id)?.highlighted !== undefined
          ? {
              ...edge,
              data: { ...edge.data, highlighted: undefined },
              markerEnd: { type: MarkerType.ArrowClosed, color: '#6b7280' },
            }
          : edge
      ),
      highlightedNodes: rfNodes.map((node) => {
        const prevData = prevNodeData.get(node.id)
        if (prevData?.active === true || prevData?.dimmed === true) {
          return {
            ...node,
            data: { ...node.data, active: false, dimmed: false },
          }
        }
        return node
      }),
    }
  }

  const { edgeBySource, edgeByTarget } = buildRelationMaps(rfEdges)
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

  // 三态视觉判定（#668）：data.highlighted 的 undefined=常态（透传
  // buildRfEdges 原始 style：加深加粗的全亮描边）、false=置灰、
  // true=高亮。常态 ≠ 置灰后「视觉态未翻转」的边不能一律回退基线对象
  // （基线是常态视觉）——保持置灰/高亮的边必须复用 prev 已重建的对象，
  // 否则 hover 移动时非链路边会闪回常态。
  const prevEdgeById = new Map(
    (prev?.highlightedEdges ?? []).map((edge) => [edge.id, edge])
  )
  const highlightedEdges = rfEdges.map((edge) => {
    const isHighlighted =
      edge.source === activeNode ||
      edge.target === activeNode ||
      (ancestors.has(edge.source) && edge.target === activeNode) ||
      (edge.source === activeNode && descendants.has(edge.target))
    const prevHighlighted = prevEdgeData.get(edge.id)?.highlighted
    const prevVisual =
      prevHighlighted === true
        ? 'highlighted'
        : prevHighlighted === false
          ? 'dimmed'
          : 'normal'
    const desiredVisual = isHighlighted ? 'highlighted' : 'dimmed'
    if (prevVisual === desiredVisual) {
      return prevEdgeById.get(edge.id) ?? edge
    }
    // markerEnd 颜色与描边同步翻转（视觉行为与重构前逐字段一致）；只在
    // 翻转时随 edge 一起新建，未翻转的边连 markerEnd 引用都不变。
    return {
      ...edge,
      data: { ...edge.data, highlighted: isHighlighted },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: isHighlighted ? '#1d4ed8' : '#d1d5db',
      },
    }
  })

  const highlightedNodes = rfNodes.map((node) => {
    const active = node.id === activeNode
    const shouldDim = !highlightedNodeIds.has(node.id)
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

  return { highlightedNodes, highlightedEdges }
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
