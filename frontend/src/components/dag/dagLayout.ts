import type { Node } from '@xyflow/react'
import * as dagre from 'dagre'
import type { DagNodeData } from './DagNode'
import { estimateDagNodeHeight } from '../dagNodeHeight'
import type { DagGraphEdge, DagGraphNode } from './DagGraph'

// #415：与 DagNode.module.css 的 .node 宽度同步（240→280）——dagre 拿它
// 布点，CSS 卡片宽于该值时相邻列会重叠。常量原在 DagGraph.tsx（NODE_WIDTH），
// #417 拆布局时随之迁来；改动任一侧宽度时两处（本常量与 .node）必须同步。
export const DAG_NODE_WIDTH = 280

/** 键序（locale 无关）：跨环境稳定，与字母序一致。 */
function stableKeyOrder(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0
}

/** 孤立节点集合：不挂在任何边上的节点（#417）。无边图里它就是全部节点。 */
export function isolatedNodeKeys(
  nodes: DagGraphNode[],
  edges: DagGraphEdge[]
): Set<string> {
  const connected = new Set<string>()
  for (const edge of edges) {
    connected.add(edge.from)
    connected.add(edge.to)
  }
  return new Set(
    nodes.filter((node) => !connected.has(node.key)).map((n) => n.key)
  )
}

// #417 无边图的退化布局治理：dagre 对没有任何边的图按传入数组顺序竖排，
// 而字母序的 published 节点数组会把 expand_analysis 之类排在首位冒充拓扑
// 入口（「部分节点丢失」的观感来源）。改为：孤立节点（无边图 = 全部节点）
// 按 key 稳定排序铺进网格（列数 clamp(⌊√n⌋,1,5)，行内右进、行满换行），
// 铺在连通分量的 dagre 实际底部之下（隔离带 ISOLATED_GAP）。有边图里
// dagre 本就把孤立分量排在连通分量下方——网格只替换孤立节点的占位坐标，
// 连通分量的 dagre 布局不受影响；任何输入都得到稳定、可预期的排布。
const ISOLATED_GAP = 120
const ISOLATED_NODESEP = 60

function isolatedGridPositions(
  isolatedKeys: Set<string>,
  heightMap: Map<string, number>,
  connectedBottom: number
): Map<string, { x: number; y: number }> {
  const isolated = [...isolatedKeys].sort(stableKeyOrder)
  const positions = new Map<string, { x: number; y: number }>()
  if (isolated.length === 0) return positions
  const columns = Math.min(
    5,
    Math.max(1, Math.floor(Math.sqrt(isolated.length)))
  )
  // 无边图时 connectedBottom 为 0，网格从画布顶部开始。
  const gridTop = connectedBottom + ISOLATED_GAP
  let rowBottom = gridTop
  let rowStart = gridTop
  isolated.forEach((key, index) => {
    const column = index % columns
    if (column === 0) {
      rowStart = rowBottom + ISOLATED_NODESEP
      rowBottom = rowStart
    }
    const height = heightMap.get(key) ?? 0
    rowBottom = Math.max(rowBottom, rowStart + height)
    positions.set(key, {
      x: column * (DAG_NODE_WIDTH + ISOLATED_NODESEP),
      y: rowStart,
    })
  })
  return positions
}

export function computeLayout(
  nodes: DagGraphNode[],
  edges: DagGraphEdge[],
  normalizeExecutorKind: (
    kind?: DagGraphNode['executorKind']
  ) => DagNodeData['executorKind']
): { rfNodes: Node<DagNodeData>[] } {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 100 })
  g.setDefaultEdgeLabel(() => ({}))

  const heightMap = new Map<string, number>()
  for (const node of nodes) {
    const height = estimateDagNodeHeight(node)
    heightMap.set(node.key, height)
    g.setNode(node.key, { width: DAG_NODE_WIDTH, height })
  }
  for (const edge of edges) {
    g.setEdge(edge.from, edge.to)
  }

  dagre.layout(g)

  const isolated = isolatedNodeKeys(nodes, edges)
  // 连通分量在 dagre 坐标系里的实际底部（节点中心 y + 半高）。
  let connectedBottom = 0
  for (const node of nodes) {
    if (isolated.has(node.key)) continue
    const gNode = g.node(node.key)
    connectedBottom = Math.max(
      connectedBottom,
      gNode.y + heightMap.get(node.key)! / 2
    )
  }
  const isolatedPositions = isolatedGridPositions(
    isolated,
    heightMap,
    connectedBottom
  )

  const rfNodes: Node<DagNodeData>[] = nodes.map((node) => {
    const height = heightMap.get(node.key)!
    // 孤立节点：丢弃 dagre 的数组序占位坐标，换稳定网格坐标（#417）。
    const grid = isolatedPositions.get(node.key)
    const gNode = g.node(node.key)
    return {
      id: node.key,
      type: 'dagNode',
      position: grid
        ? { x: grid.x, y: grid.y }
        : { x: gNode.x - DAG_NODE_WIDTH / 2, y: gNode.y - height / 2 },
      data: {
        label: node.label,
        status: node.status,
        duration: node.duration,
        executorKind: normalizeExecutorKind(node.executorKind),
        executorId: node.executorId ?? null,
        agentId: node.agentId ?? null,
        workerId: node.workerId ?? null,
        nodeKey: node.capability ? node.key : undefined,
        capability: node.capability,
        executorUnbound: node.executorUnbound ?? false,
        executionWarning: node.executionWarning,
        topologyBadges: node.topologyBadges,
        terminalOutcome: node.terminalOutcome,
        inputs: node.inputs || [],
        outputs: node.outputs || [],
        changeType: node.changeType,
        ghost: node.ghost ?? false,
        // #276：高亮/置灰态放 data 而非 node.style/className，让 hover 时
        // 未受影响节点能保持 data 引用稳定（见 DagGraph highlightMemo 注释）。
        active: false,
        dimmed: false,
      },
    }
  })

  return { rfNodes }
}
