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
// 入口（「部分节点丢失」的观感来源）。孤立节点（无边图 = 全部节点）按 key
// 稳定排序铺进网格（列数 clamp(⌈√n⌉,2,5)，n=1 单列；行内右进、行满换行），
// 铺在连通分量的 dagre 实际底部之下（隔离带 ISOLATED_GAP）。#424 codex
// 三轮起 dagre 只布局连通子图（见 computeLayout），孤立节点不再进入
// dagre 坐标系，任何输入都得到稳定、可预期的排布。
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
  // 列数：⌈√n⌉ 向上取整（n=2、3 时 floor 仍得 1，会把小图排回竖直单列，
  // 正是本次治理要消除的退化），clamp 到 [2,5]；n=1 是唯一合法单列形态
  // （#424 codex 复审）。
  const columns = Math.max(
    Math.min(Math.ceil(Math.sqrt(isolated.length)), 5),
    isolated.length > 1 ? 2 : 1
  )
  // 网格首行顶 = 连通分量实际底边 + ISOLATED_GAP（无边图时 connectedBottom
  // 为 0，网格从画布顶部隔一个 ISOLATED_GAP 开始）。
  const gridTop = connectedBottom + ISOLATED_GAP
  // rowBottom 的语义是「上一行底边」；首行没有上一行，预置 gridTop -
  // ISOLATED_NODESEP，让首个换行分支恰好落在 gridTop——隔离带严格等于
  // ISOLATED_GAP，而不是多叠一次 ISOLATED_NODESEP（#424 独立复审：原实现
  // 首行实际偏移 180，与注释宣称的 120 隔离带不符）。
  let rowBottom = gridTop - ISOLATED_NODESEP
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

  // #424 codex 三轮 P2：dagre 只喂连通节点子图。原实现把全部节点交给
  // dagre，孤立节点（尤其排在数组前面时）会占据层内纵向位置、把连通分量
  // 整体向下推移，随后孤立节点虽被移走，连通分量却不重新布局，留下大片
  // 空白并让 fitView 过度缩小。只喂连通子图后，连通分量的坐标与「无孤立
  // 节点时单独布局」完全一致。纯无边图 → 子图为空，dagre 直接空布局，
  // 全部节点走网格；纯连通图 → 子图即全图，行为不变。
  const heightMap = new Map<string, number>()
  const isolated = isolatedNodeKeys(nodes, edges)
  const nodeKeys = new Set(nodes.map((n) => n.key))
  for (const node of nodes) {
    const height = estimateDagNodeHeight(node)
    heightMap.set(node.key, height)
    if (!isolated.has(node.key)) {
      g.setNode(node.key, { width: DAG_NODE_WIDTH, height })
    }
  }
  for (const edge of edges) {
    // 悬空边（端点不在节点表里）跳过：另一端可能仍是被引用的连通节点，
    // 这类节点的孤立判定不变（见 isolatedNodeKeys 测试），但缺失端点
    // 进不了子图，dagre 0.8.5 对 setEdge 到不存在节点会自动补一个
    // 意外占位，污染布局。
    if (nodeKeys.has(edge.from) && nodeKeys.has(edge.to)) {
      g.setEdge(edge.from, edge.to)
    }
  }

  dagre.layout(g)

  // 连通分量在 dagre 坐标系里的实际底部（节点中心 y + 半高）。连通节点必
  // 在子图内，g.node() 兜底 0 仅满足类型（子图为空时循环体不执行）。
  let connectedBottom = 0
  for (const node of nodes) {
    if (isolated.has(node.key)) continue
    connectedBottom = Math.max(
      connectedBottom,
      (g.node(node.key)?.y ?? 0) + heightMap.get(node.key)! / 2
    )
  }
  const isolatedPositions = isolatedGridPositions(
    isolated,
    heightMap,
    connectedBottom
  )

  const rfNodes: Node<DagNodeData>[] = nodes.map((node) => {
    const height = heightMap.get(node.key)!
    // 孤立节点：不在 dagre 子图里（#424 codex 三轮），直接用稳定网格坐标。
    const grid = isolatedPositions.get(node.key)
    const gNode = grid ? { x: 0, y: 0 } : g.node(node.key)!
    return {
      id: node.key,
      type: 'dagNode',
      position: grid
        ? { x: grid.x, y: grid.y }
        : {
            // 连通节点必在子图内（与 connectedBottom 同理）。
            x: gNode.x - DAG_NODE_WIDTH / 2,
            y: gNode.y - height / 2,
          },
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
