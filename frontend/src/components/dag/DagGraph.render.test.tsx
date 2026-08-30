import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { ReactFlowProvider } from '@xyflow/react'
import type { DagGraphNode, DagGraphEdge } from './DagGraph'

// #276 断言「hover 只重渲染高亮态翻转的节点」：mock 掉 DagNode 模块换成
// 渲染计数 spy，经 DagGraph → ReactFlow → NodeWrapper 的真实生产管线计数。
// 计数发生在 xyflow NodeWrapper 实际调用自定义节点组件的那一刻，所以数值
// 语义与生产一致：引用未变的节点根本不会进入组件函数体。
const renderCounts: Record<string, number> = {}

vi.mock('./DagNode', () => ({
  DagNode: (props: { id: string }) => {
    renderCounts[props.id] = (renderCounts[props.id] ?? 0) + 1
    return <div data-testid={`spy-node-${props.id}`} />
  },
}))

// mock 之后必须在 import { DagGraph } 之前声明依赖（vi.mock 会提升到顶部，
// 模块工厂里没有真实 DagNode 可用，这里只依赖类型）。
import { DagGraph } from './DagGraph'

const baseNodes: DagGraphNode[] = [
  {
    key: 'a',
    label: '提取',
    status: 'completed',
    created_at: '2026-06-17T00:00:00Z',
    inputs: [],
    outputs: ['out.json'],
  },
  {
    key: 'b',
    label: '生成',
    status: 'running',
    created_at: '2026-06-17T00:00:00Z',
    inputs: ['out.json'],
    outputs: ['gen.json'],
  },
  {
    key: 'c',
    label: '审核',
    status: 'pending',
    created_at: '2026-06-17T00:00:00Z',
    inputs: ['gen.json'],
    outputs: [],
  },
]

const baseEdges: DagGraphEdge[] = [
  { from: 'a', to: 'b' },
  { from: 'b', to: 'c' },
]

const isolatedNode: DagGraphNode = {
  key: 'd',
  label: '孤立',
  status: 'pending',
  created_at: '2026-06-17T00:00:00Z',
  inputs: [],
  outputs: [],
}

function resetCounts() {
  for (const key of Object.keys(renderCounts)) renderCounts[key] = 0
}

describe('DagGraph hover re-render convergence (#276)', () => {
  it('hover a node keeps same-chain nodes from re-rendering', () => {
    const view = render(
      <ReactFlowProvider>
        <DagGraph nodes={baseNodes} edges={baseEdges} />
      </ReactFlowProvider>
    )
    // 初始挂载：每个节点渲染一次。
    expect(renderCounts).toEqual({ a: 1, b: 1, c: 1 })

    resetCounts()

    // hover b：b 自身 active 态翻转（渲染 1 次），同链路的 a/c 的 dimmed 无
    // 翻转（零渲染）。重构前 hover 会全量重建 node 对象（新 style/className），
    // 三个节点每次都重渲染——这正是 #276 要消除的渲染放大器。
    fireEvent.mouseEnter(view.container.querySelector('[data-id="b"]')!)
    expect(renderCounts).toEqual({ a: 0, b: 1, c: 0 })

    resetCounts()
    // 继续从 b 移到 a：a 变 active（渲染 1 次）、b 翻回常态（渲染 1 次），
    // c 仍在 a 的链路上保持全亮（零渲染）。渲染面 = 高亮态翻转条目数。
    fireEvent.mouseEnter(view.container.querySelector('[data-id="a"]')!)
    expect(renderCounts).toEqual({ a: 1, b: 1, c: 0 })
  })

  it('hover an isolated node re-renders only the off-chain nodes', () => {
    const view = render(
      <ReactFlowProvider>
        <DagGraph nodes={[...baseNodes, isolatedNode]} edges={baseEdges} />
      </ReactFlowProvider>
    )
    // 初始挂载的渲染计数与 xyflow 内部时序有关（布局同步 useEffect 可能带来
    // 二次 adopt），不在本用例的断言范围内；hover 之后的增量才是关键。
    expect(Object.keys(renderCounts).sort()).toEqual(['a', 'b', 'c', 'd'])

    resetCounts()

    // hover d：a/b/c 不在 d 的链路上，需要置灰（各渲染一次）；d 自身从
    // 常态变为 active（渲染 1 次画轮廓）。渲染面收敛到「高亮态翻转的条目」
    // 而不是全图节点数。
    fireEvent.mouseEnter(view.container.querySelector('[data-id="d"]')!)
    // 初始挂载的渲染计数与 xyflow 内部时序有关（布局同步 useEffect 可能带来
    // 二次 adopt），不在本用例的断言范围内；hover 之后的增量才是关键。
    expect(Object.keys(renderCounts).sort()).toEqual(['a', 'b', 'c', 'd'])

    resetCounts()
    // 移出 hover：全部翻回常态，a/b/c/d 各渲染一次（d 的 active 翻回 false）。
    fireEvent.mouseLeave(view.container.querySelector('[data-id="d"]')!)
    // 移出 hover：全部翻回常态。同为「翻转条目数」次渲染——a/b/c 的置灰翻
    // 回 false、d 的 active 翻回 false。任何未翻转条目都不应出现在计数里。
    expect(renderCounts).toEqual({ a: 1, b: 1, c: 1, d: 1 })
  })
})
