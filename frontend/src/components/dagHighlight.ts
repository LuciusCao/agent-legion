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
    // 全图常态：只有 data.active/dimmed/highlighted 从 true 翻回 false 的
    // 条目需要换新对象（边同时还原默认 marker 颜色）。对照 prev 判断翻转
    // 而非基线——从「hover 中」到「移出」也只重建高亮过的条目。
    return {
      highlightedEdges: rfEdges.map((edge) =>
        prevEdgeData.get(edge.id)?.highlighted === true
          ? {
              ...edge,
              data: { ...edge.data, highlighted: false },
              markerEnd: { type: MarkerType.ArrowClosed, color: '#9ca3af' },
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

  // 三元判定避免 boolean 与 undefined 混用：data 字段只以 true/false 参与
  // 比较（undefined 视作 false），保证「首次 hover」不会因 undefined→false
  // 的归一化而产生无谓的新对象——引用复用判断对未翻转条目保持穷尽。
  const highlightedEdges = rfEdges.map((edge) => {
    const isHighlighted =
      edge.source === activeNode ||
      edge.target === activeNode ||
      (ancestors.has(edge.source) && edge.target === activeNode) ||
      (edge.source === activeNode && descendants.has(edge.target))
    if ((prevEdgeData.get(edge.id)?.highlighted === true) === isHighlighted) {
      return edge
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
