import { Edge, MarkerType, Node } from '@xyflow/react'
import type { DagNodeData } from './dag/dagNodeTypes'
import { buildRelationMaps, collectAncestors, collectDescendants } from './dagGraphRelations'

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
 */
export function applyHighlight(
  rfNodes: Node<DagNodeData>[],
  rfEdges: Edge[],
  activeNode: string | null
): { highlightedNodes: Node<DagNodeData>[]; highlightedEdges: Edge[] } {
  if (!activeNode) {
    // 全图常态：只有 data.active/dimmed 从 true 翻回 false 的条目需要
    // 换新对象（边同时还原默认 marker 颜色）。
    return {
      highlightedEdges: rfEdges.map((edge) =>
        edge.data?.highlighted === true
          ? {
              ...edge,
              data: { ...edge.data, highlighted: false },
              markerEnd: { type: MarkerType.ArrowClosed, color: '#9ca3af' },
            }
          : edge
      ),
      highlightedNodes: rfNodes.map((node) =>
        node.data.active === true || node.data.dimmed === true
          ? { ...node, data: { ...node.data, active: false, dimmed: false } }
          : node
      ),
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
    if ((edge.data?.highlighted === true) === isHighlighted) return edge
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
    if (
      (node.data.dimmed === true) === shouldDim &&
      (node.data.active === true) === active
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
