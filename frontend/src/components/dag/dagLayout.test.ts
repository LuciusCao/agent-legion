import { describe, expect, it } from 'vitest'
import { computeLayout, isolatedNodeKeys } from './dagLayout'
import { estimateDagNodeHeight } from '../dagNodeHeight'
import type { DagGraphEdge, DagGraphNode } from './DagGraph'

function node(
  key: string,
  overrides: Partial<DagGraphNode> = {}
): DagGraphNode {
  return {
    key,
    label: key,
    status: 'pending',
    created_at: '',
    ...overrides,
  }
}

const normalize = () => null

describe('isolatedNodeKeys (#417)', () => {
  it('marks every node isolated when there are no edges', () => {
    const nodes = [node('a'), node('b'), node('c')]
    expect(isolatedNodeKeys(nodes, [])).toEqual(new Set(['a', 'b', 'c']))
  })

  it('marks only nodes not attached to any edge', () => {
    const nodes = [node('a'), node('b'), node('c'), node('d')]
    const edges: DagGraphEdge[] = [
      { from: 'a', to: 'b' },
      { from: 'b', to: 'c' },
    ]
    expect(isolatedNodeKeys(nodes, edges)).toEqual(new Set(['d']))
  })

  it('treats nodes only referenced by dangling edges as connected', () => {
    // 边端点不在节点表里时边本身无效，但节点挂上边即视为已连接（渲染层
    // 的责任是排布，不是校验悬空边）。
    const nodes = [node('a'), node('ghostTarget')]
    const edges: DagGraphEdge[] = [{ from: 'a', to: 'ghostTarget' }]
    expect(isolatedNodeKeys(nodes, edges)).toEqual(new Set())
  })
})

describe('computeLayout (#417 edgeless degenerate layout)', () => {
  it('lays an edgeless graph out as a stable key-ordered grid, not array order', () => {
    // 症状复现（issue #417）：字母序的 published 记录 + 无边 → dagre 按数组
    // 序竖排，expand_analysis 之类冒充拓扑入口。网格布局必须按 key 排序：
    // zeta 在数组首位也不得占网格第一格。5 个孤立节点 → 2 列网格，key 序
    // 依次入格：alpha(0,0) beta(1,0) expand_analysis(0,1) intake(1,1)
    // zeta(0,2)。
    const nodes = [
      node('zeta'),
      node('expand_analysis'),
      node('intake'),
      node('alpha'),
      node('beta'),
    ]
    const { rfNodes } = computeLayout(nodes, [], normalize)
    const byKey = new Map(rfNodes.map((n) => [n.id, n.position]))
    const alpha = byKey.get('alpha')!
    const beta = byKey.get('beta')!
    const expand = byKey.get('expand_analysis')!
    const zeta = byKey.get('zeta')!
    // 第一格是 key 序第一（alpha），不是数组序第一（zeta）。
    expect(alpha.x).toBe(0)
    expect(alpha.y).toBeLessThan(zeta.y)
    // 同行右进、行满换行。
    expect(beta.y).toBe(alpha.y)
    expect(beta.x).toBeGreaterThan(alpha.x)
    expect(expand.y).toBeGreaterThan(alpha.y)
    expect(zeta.y).toBeGreaterThan(expand.y)
  })

  it('is deterministic across calls with shuffled node arrays', () => {
    const ordered = [node('a'), node('b'), node('c'), node('d')]
    const shuffled = [node('d'), node('b'), node('a'), node('c')]
    const first = computeLayout(ordered, [], normalize).rfNodes
    const second = computeLayout(shuffled, [], normalize).rfNodes
    const pos = (nodes: typeof first, key: string) =>
      nodes.find((n) => n.id === key)!.position
    for (const key of ['a', 'b', 'c', 'd']) {
      expect(pos(first, key)).toEqual(pos(second, key))
    }
  })

  it('keeps dagre layout for connected nodes and moves isolated ones below', () => {
    const nodes = [node('a'), node('b'), node('c'), node('iso')]
    const edges: DagGraphEdge[] = [
      { from: 'a', to: 'b' },
      { from: 'b', to: 'c' },
    ]
    const { rfNodes } = computeLayout(nodes, edges, normalize)
    const byKey = new Map(rfNodes.map((n) => [n.id, n.position]))
    // 连通链保持 LR：a.x < b.x < c.x。
    expect(byKey.get('a')!.x).toBeLessThan(byKey.get('b')!.x)
    expect(byKey.get('b')!.x).toBeLessThan(byKey.get('c')!.x)
    // 孤立节点铺在连通分量下方（y 更大），不再挤在链旁。
    expect(byKey.get('iso')!.y).toBeGreaterThan(byKey.get('a')!.y)
    expect(byKey.get('iso')!.y).toBeGreaterThan(byKey.get('c')!.y)
  })

  it('places the first isolated row exactly ISOLATED_GAP below the connected bottom (#424 独立复审)', () => {
    // 首行间距钉死：换行分支对首行也生效，曾把首行实际推到
    // ISOLATED_GAP + ISOLATED_NODESEP（180），与「隔离带 ISOLATED_GAP」
    // 的注释/常量语义不符。连通分量实际底边 = 各连通节点顶边 + 高度的
    // 最大值（position.y 是顶边，顶边 + 高度 = dagre 中心 y + 半高）。
    const nodes = [node('a'), node('b'), node('iso')]
    const edges: DagGraphEdge[] = [{ from: 'a', to: 'b' }]
    const { rfNodes } = computeLayout(nodes, edges, normalize)
    const byKey = new Map(rfNodes.map((n) => [n.id, n.position]))
    const connectedBottom = Math.max(
      ...nodes
        .filter((n) => n.key !== 'iso')
        .map((n) => byKey.get(n.key)!.y + estimateDagNodeHeight(n))
    )
    // 120 = ISOLATED_GAP（dagLayout 模块内常量，未导出）。
    expect(byKey.get('iso')!.y).toBeCloseTo(connectedBottom + 120, 6)
  })

  it('keeps isolated nodes clear of a tall connected node by its real bottom (#424 独立复审)', () => {
    // connectedBottom 必须按「节点中心 y + 半高」取 max。连通节点全是
    // 66px 矮节点时，漏加半高或误用 min 只差几十像素，宽松的 y 比较
    // 抓不住；这里放一个 200+ 的连通高节点（capability + 多 inputs）把
    // 防重叠间距钉死：孤立首行必须落在高节点实际底边 + ISOLATED_GAP 之下。
    const tall = node('tall', {
      capability: 'files.read',
      inputs: ['in1.json', 'in2.json', 'in3.json', 'in4.json'],
    })
    const nodes = [tall, node('sink'), node('iso')]
    const edges: DagGraphEdge[] = [{ from: 'tall', to: 'sink' }]
    const { rfNodes } = computeLayout(nodes, edges, normalize)
    const byKey = new Map(rfNodes.map((n) => [n.id, n.position]))
    const tallHeight = estimateDagNodeHeight(tall)
    expect(tallHeight).toBeGreaterThanOrEqual(200)
    // position.y 是顶边；顶边 + 高度 = 底边（即 dagre 中心 y + 半高）。
    const tallBottom = byKey.get('tall')!.y + tallHeight
    expect(byKey.get('iso')!.y).toBeGreaterThanOrEqual(tallBottom + 120)
  })

  it('places every node (none dropped by the layout)', () => {
    const nodes = [node('a'), node('b'), node('c')]
    const { rfNodes } = computeLayout(nodes, [], normalize)
    expect(rfNodes.map((n) => n.id).sort()).toEqual(['a', 'b', 'c'])
    for (const rfNode of rfNodes) {
      expect(Number.isFinite(rfNode.position.x)).toBe(true)
      expect(Number.isFinite(rfNode.position.y)).toBe(true)
    }
  })
})
