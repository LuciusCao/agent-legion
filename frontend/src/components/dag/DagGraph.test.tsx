import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { ReactFlowProvider, useStoreApi } from '@xyflow/react'
import { DagGraph, DagGraphNode, DagGraphEdge } from './DagGraph'

const nodes: DagGraphNode[] = [
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

const edges: DagGraphEdge[] = [
  { from: 'a', to: 'b' },
  { from: 'b', to: 'c' },
]

describe('DagGraph', () => {
  it('renders ReactFlow container and nodes', () => {
    render(<DagGraph nodes={nodes} edges={edges} />)
    expect(screen.getByText('提取')).toBeInTheDocument()
    expect(screen.getByText('生成')).toBeInTheDocument()
    expect(screen.getByText('审核')).toBeInTheDocument()
  })

  it('renders empty state when no nodes', () => {
    const { container } = render(<DagGraph nodes={[]} edges={[]} />)
    expect(container.querySelector('.react-flow')).toBeInTheDocument()
  })

  it('shows details panel when a node is clicked', () => {
    render(<DagGraph nodes={nodes} edges={edges} />)
    fireEvent.click(screen.getByText('提取'))
    expect(screen.getByText('查看日志')).toBeInTheDocument()
  })

  it('passes onViewLogs to the details panel', () => {
    const onViewLogs = vi.fn()
    render(<DagGraph nodes={nodes} edges={edges} onViewLogs={onViewLogs} />)
    fireEvent.click(screen.getByText('提取'))
    fireEvent.click(screen.getByText('查看日志'))
    expect(onViewLogs).toHaveBeenCalledWith('a')
  })

  it('passes padded fit view options to React Flow', () => {
    render(<DagGraph nodes={nodes} edges={edges} hideNodeDetails />)

    expect(screen.getByTestId('dag-flow-wrapper')).toHaveAttribute(
      'data-fit-view-padding',
      '0.18'
    )
  })

  it('reflects controlled selected node and calls onSelectedNodeChange when a different node is clicked', () => {
    const onSelectedNodeChange = vi.fn()
    render(
      <DagGraph
        nodes={nodes}
        edges={edges}
        selectedNode="b"
        onSelectedNodeChange={onSelectedNodeChange}
      />
    )
    expect(screen.getByRole('heading', { name: '生成' })).toBeInTheDocument()
    fireEvent.click(screen.getByText('提取'))
    expect(onSelectedNodeChange).toHaveBeenCalledWith('a')
  })

  it('renders conditional edge labels', async () => {
    render(
      <DagGraph
        nodes={[
          {
            key: 'classify',
            label: '判断是否适合审题',
            status: 'pending',
            created_at: '',
            inputs: [],
            outputs: ['decision.json'],
          },
          {
            key: 'assemble',
            label: '组装审题信息',
            status: 'pending',
            created_at: '',
            inputs: ['decision.json'],
            outputs: [],
          },
        ]}
        edges={[
          {
            from: 'classify',
            to: 'assemble',
            label: '$.eligible == true',
            conditional: true,
          },
        ]}
      />
    )

    expect(await screen.findByText('$.eligible == true')).toBeInTheDocument()
  })

  // #276：hover 高亮从「全量重建 node/edge 的 style/className」下沉为
  // node.data.dimmed / edge.data.highlighted 后，视觉行为必须与重构前一致。
  // 下面的用例覆盖边描边与节点置灰两条高亮链路；「hover 只重渲染受影响节点」
  // 的渲染计数断言见 DagGraph.render.test.tsx。
  //
  // jsdom 没有布局引擎（offsetWidth/getBoundingClientRect 恒为 0），xyflow
  // 拿不到 handleBounds 就连边都不渲染。stub 出固定几何，再手动调一次
  // updateNodeInternals（真实环境由 ResizeObserver 完成同一件事），才能让
  // EdgeWrapper 走到自定义 DagEdge 的渲染，断言 stroke/opacity 内联样式。
  function stubDomGeometry() {
    const rect = {
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 240,
      bottom: 100,
      width: 240,
      height: 100,
      toJSON: () => ({}),
    } as DOMRect
    Element.prototype.getBoundingClientRect = () => rect
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
      configurable: true,
      get: () => 240,
    })
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
      configurable: true,
      get: () => 100,
    })
  }

  // 必须包在 ReactFlowProvider 里让测试助手与 DagGraph 内部的 ReactFlow
  // 共享同一个 store（否则 Wrapper 会各建各的）。
  const ForceNodeInternals = () => {
    const api = useStoreApi()
    return (
      <button
        data-testid="force-internals"
        onClick={() => {
          const updates = new Map<
            string,
            { id: string; nodeElement: HTMLDivElement; force?: boolean }
          >()
          document
            .querySelectorAll<HTMLElement>('[data-id]')
            .forEach((el) => {
              const id = el.getAttribute('data-id')!
              updates.set(id, {
                id,
                nodeElement: el as HTMLDivElement,
                force: true,
              })
            })
          api.getState().updateNodeInternals(updates)
        }}
      />
    )
  }

  async function renderDagGraphWithEdges(props: {
    nodes: DagGraphNode[]
    edges: DagGraphEdge[]
  }) {
    stubDomGeometry()
    const view = render(
      <ReactFlowProvider>
        <DagGraph {...props} />
        <ForceNodeInternals />
      </ReactFlowProvider>
    )
    await act(async () => {
      fireEvent.click(screen.getByTestId('force-internals'))
      // updateNodeInternals 会级联触发 MiniMap 等订阅组件的 setState；
      // 等一个 rAF 让这些更新都落在 act 里，避免 "not wrapped in act" 噪音。
      await new Promise((resolve) => requestAnimationFrame(resolve))
    })
    return view
  }

  function edgeInlineStyles(container: HTMLElement) {
    return Array.from(
      container.querySelectorAll<SVGPathElement>('.react-flow__edge-path')
    ).map((p) => ({
      stroke: p.getAttribute('stroke') ?? p.style.stroke,
      strokeWidth: p.getAttribute('stroke-width') ?? p.style.strokeWidth,
      opacity: p.getAttribute('opacity') ?? p.style.opacity,
    }))
  }

  it('renders edge highlight styles from custom dagEdge component', async () => {
    const view = await renderDagGraphWithEdges({ nodes, edges })
    // 常态：默认灰描边、宽 2、透明度 0.4（与重构前内联 style 逐字段一致）。
    expect(edgeInlineStyles(view.container)).toEqual([
      { stroke: '#d1d5db', strokeWidth: '2', opacity: '0.4' },
      { stroke: '#d1d5db', strokeWidth: '2', opacity: '0.4' },
    ])

    // hover 节点 b：邻接边（a→b、b→c）变蓝加粗全亮。
    fireEvent.mouseEnter(view.container.querySelector('[data-id="b"]')!)
    expect(edgeInlineStyles(view.container)).toEqual([
      { stroke: '#1d4ed8', strokeWidth: '3', opacity: '1' },
      { stroke: '#1d4ed8', strokeWidth: '3', opacity: '1' },
    ])

    // 移出：恢复常态。
    fireEvent.mouseLeave(view.container.querySelector('[data-id="b"]')!)
    expect(edgeInlineStyles(view.container)).toEqual([
      { stroke: '#d1d5db', strokeWidth: '2', opacity: '0.4' },
      { stroke: '#d1d5db', strokeWidth: '2', opacity: '0.4' },
    ])
  })

  it('dims off-chain nodes when hovering a node', () => {
    const isolated: DagGraphNode[] = [
      ...nodes,
      {
        key: 'd',
        label: '孤立',
        status: 'pending',
        created_at: '2026-06-17T00:00:00Z',
        inputs: [],
        outputs: [],
      },
    ]
    const view = render(<DagGraph nodes={isolated} edges={edges} />)
    const nodeD = view.container.querySelector('[data-id="d"]')
    expect(nodeD).not.toBeNull()

    fireEvent.mouseEnter(nodeD!)
    const dCard = nodeD!.querySelector('[data-testid="dag-node"]') as HTMLElement
    // d 是 active 节点：自身不置灰，且渲染 active 轮廓类（旧版
    // selectedFlowNode 的等价视觉）。
    expect(dCard.style.opacity).toBe('')
    expect(dCard.className).toContain('active')
    const aCard = view.container
      .querySelector('[data-id="a"]')!
      .querySelector('[data-testid="dag-node"]') as HTMLElement
    // a 不在 d 的链路上：置灰 opacity 0.45（与重构前的 node.style 一致）。
    expect(aCard.style.opacity).toBe('0.45')
    expect(aCard.className).not.toContain('active')

    fireEvent.mouseLeave(nodeD!)
    expect(aCard.style.opacity).toBe('')
    expect(dCard.className).not.toContain('active')
  })
})
