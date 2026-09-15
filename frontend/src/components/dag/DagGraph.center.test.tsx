import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

/* #667 B2：DagSelectionViewport 的镜头定位用 mock 的 useReactFlow 验证
 * （xyflow 真实实例的 setCenter 走 d3 transition，jsdom 里无法观测）。
 * 模块其余导出保持真实，DagGraph 照常渲染。 */
const mocks = vi.hoisted(() => ({
  setCenter: vi.fn(),
  getZoom: vi.fn(() => 1),
  // 只认识图里真实存在的节点；未知 id 与 xyflow 一样返回 undefined。
  getInternalNode: vi.fn((id: string) => {
    if (id !== 'a' && id !== 'b') return undefined
    return {
      id,
      internals: { positionAbsolute: { x: 100, y: 40 } },
      measured: { width: 280, height: 100 },
    }
  }),
}))

vi.mock('@xyflow/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@xyflow/react')>()
  return {
    ...actual,
    useReactFlow: () =>
      ({
        setCenter: mocks.setCenter,
        getZoom: mocks.getZoom,
        getInternalNode: mocks.getInternalNode,
      }) as unknown as ReturnType<typeof actual.useReactFlow>,
  }
})

import { DagGraph } from './DagGraph'
import type { DagGraphEdge, DagGraphNode } from './DagGraph'

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
]
const edges: DagGraphEdge[] = [{ from: 'a', to: 'b' }]

describe('DagGraph 选中节点镜头定位（#667 B2）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // DagSelectionViewport 跳过零尺寸容器（jsdom clientWidth 恒为 0）；stub
    // 出非零尺寸让定位逻辑在测试里走到 mocked setCenter。
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get: () => 800,
    })
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
      configurable: true,
      get: () => 600,
    })
  })

  it('画布外选中（受控 selectedNode 到达/变化）时镜头平滑移到节点中心', () => {
    const { rerender } = render(
      <DagGraph nodes={nodes} edges={edges} selectedNode={null} />
    )
    expect(mocks.setCenter).not.toHaveBeenCalled()

    rerender(<DagGraph nodes={nodes} edges={edges} selectedNode="b" />)
    // 节点中心 = positionAbsolute + measured/2，zoom 保持当前值。
    expect(mocks.getInternalNode).toHaveBeenCalledWith('b')
    expect(mocks.setCenter).toHaveBeenCalledTimes(1)
    expect(mocks.setCenter).toHaveBeenLastCalledWith(240, 90, {
      zoom: 1,
      duration: 350,
    })

    // 同一选中重复渲染（hover/高亮等）不重复飞行。
    rerender(<DagGraph nodes={nodes} edges={edges} selectedNode="b" />)
    expect(mocks.setCenter).toHaveBeenCalledTimes(1)

    rerender(<DagGraph nodes={nodes} edges={edges} selectedNode="a" />)
    expect(mocks.setCenter).toHaveBeenCalledTimes(2)

    // 取消选中不飞镜头；再次选中同一节点重新定位。
    rerender(<DagGraph nodes={nodes} edges={edges} selectedNode={null} />)
    expect(mocks.setCenter).toHaveBeenCalledTimes(2)
    rerender(<DagGraph nodes={nodes} edges={edges} selectedNode="a" />)
    expect(mocks.setCenter).toHaveBeenCalledTimes(3)
  })

  it('画布内点击的选中不触发镜头定位', () => {
    render(<DagGraph nodes={nodes} edges={edges} />)
    fireEvent.click(screen.getByText('提取'))
    // 选中生效（详情面板出现），但镜头不动——节点本来就在光标下。
    expect(screen.getByText('查看日志')).toBeInTheDocument()
    expect(mocks.setCenter).not.toHaveBeenCalled()
  })

  it('选中节点不在图中时不定位、不抛错', () => {
    render(<DagGraph nodes={nodes} edges={edges} selectedNode="ghost" />)
    expect(mocks.getInternalNode).toHaveBeenCalledWith('ghost')
    expect(mocks.setCenter).not.toHaveBeenCalled()
  })
})
