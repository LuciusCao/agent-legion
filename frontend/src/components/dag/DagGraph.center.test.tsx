import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/* #667 B2：DagSelectionViewport 的镜头定位用 mock 的 useReactFlow 验证
 * （xyflow 真实实例的 setCenter 走 d3 transition，jsdom 里无法观测）。
 * 模块其余导出保持真实，DagGraph 照常渲染。 */
const mocks = vi.hoisted(() => {
  // measured 可变：模拟 DOM 测量完成前（空对象）与完成后（实际宽高）。
  const state = {
    measured: { width: 280, height: 100 } as {
      width?: number
      height?: number
    },
  }
  return {
    state,
    setCenter: vi.fn(),
    getZoom: vi.fn(() => 1),
    // 只认识图里真实存在的节点；未知 id 与 xyflow 一样返回 undefined。
    getInternalNode: vi.fn((id: string) => {
      if (id !== 'a' && id !== 'b') return undefined
      return {
        id,
        internals: { positionAbsolute: { x: 100, y: 40 } },
        measured: state.measured,
      }
    }),
    // DagSelectionViewport 的 useStore 选择器只读 nodeLookup。
    storeState: {
      nodeLookup: {
        get: (id: string) =>
          id === 'a' || id === 'b' ? { measured: state.measured } : undefined,
      },
    },
  }
})

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
    useStore: ((selector: (state: unknown) => unknown) =>
      selector(mocks.storeState)) as unknown as typeof actual.useStore,
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

/* 容器尺寸可变 stub：DagSelectionViewport 跳过零尺寸容器（jsdom
 * clientWidth 恒为 0），默认给非零尺寸让定位走到 mocked setCenter；
 * 尺寸恢复重试用例把它调成 0 再调回。 */
const containerSize = { width: 800, height: 600 }

class ResizeObserverMock {
  static instances: ResizeObserverMock[] = []
  private callback: ResizeObserverCallback
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
    ResizeObserverMock.instances.push(this)
  }
  observe() {}
  unobserve() {}
  disconnect() {}
  trigger() {
    this.callback([], this as unknown as ResizeObserver)
  }
}

const originalResizeObserver = globalThis.ResizeObserver

describe('DagGraph 选中节点镜头定位（#667 B2）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.state.measured = { width: 280, height: 100 }
    containerSize.width = 800
    containerSize.height = 600
    ResizeObserverMock.instances = []
    globalThis.ResizeObserver =
      ResizeObserverMock as unknown as typeof ResizeObserver
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get: () => containerSize.width,
    })
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
      configurable: true,
      get: () => containerSize.height,
    })
  })

  afterEach(() => {
    globalThis.ResizeObserver = originalResizeObserver
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

  it('measured 未就绪时不标完成，测量后重试定位到节点中心', () => {
    // 新 ReactFlow 实例以已有 selectedNode 挂载（如选中后打开全屏 DAG）：
    // DOM 测量完成前 measured 为空，宽高降 0 会把镜头定到节点左上角且
    // 无法校正——必须保留未定位态等测量后重试。
    mocks.state.measured = {}
    const { rerender } = render(
      <DagGraph nodes={nodes} edges={edges} selectedNode="a" />
    )
    expect(mocks.setCenter).not.toHaveBeenCalled()

    mocks.state.measured = { width: 280, height: 100 }
    rerender(<DagGraph nodes={nodes} edges={edges} selectedNode="a" />)
    expect(mocks.setCenter).toHaveBeenCalledTimes(1)
    expect(mocks.setCenter).toHaveBeenLastCalledWith(240, 90, {
      zoom: 1,
      duration: 350,
    })
  })

  it('容器从零尺寸恢复后重试未完成的定位（display:none 面板切回）', () => {
    // 移动端 DAG 面板被 CSS display:none 隐藏时尺寸为零：定位被跳过，且
    // 切回面板只改 class，selectedNode/nodesVersion 不变——必须靠容器尺寸
    // 监听重试。
    containerSize.width = 0
    containerSize.height = 0
    render(<DagGraph nodes={nodes} edges={edges} selectedNode="a" />)
    expect(mocks.setCenter).not.toHaveBeenCalled()

    act(() => {
      containerSize.width = 800
      containerSize.height = 600
      ResizeObserverMock.instances.forEach((instance) => instance.trigger())
    })
    expect(mocks.setCenter).toHaveBeenCalledTimes(1)
    expect(mocks.setCenter).toHaveBeenLastCalledWith(240, 90, {
      zoom: 1,
      duration: 350,
    })

    // 已完成定位后，后续尺寸变化不重复飞行。
    act(() => {
      ResizeObserverMock.instances.forEach((instance) => instance.trigger())
    })
    expect(mocks.setCenter).toHaveBeenCalledTimes(1)
  })

  it('外部选中后点击同节点再取消，重新外部选中同节点仍定位', () => {
    // 回归：外部选中并定位后，画布点击同一节点写入 clickOriginRef 但不被
    // 消费（focusedRef 去重提前返回）；取消选中时必须一并清掉，否则下次
    // 同节点的外部选择被误判为画布点击而跳过定位。
    const onSelectedNodeChange = vi.fn()
    const { rerender } = render(
      <DagGraph
        nodes={nodes}
        edges={edges}
        selectedNode="a"
        onSelectedNodeChange={onSelectedNodeChange}
      />
    )
    expect(mocks.setCenter).toHaveBeenCalledTimes(1)

    // 受控选中下详情面板也渲染同名标题，直接点节点容器。
    fireEvent.click(
      screen.getByTestId('dag-flow-wrapper').querySelector('[data-id="a"]')!
    )
    expect(onSelectedNodeChange).toHaveBeenCalledWith('a')
    expect(mocks.setCenter).toHaveBeenCalledTimes(1)

    rerender(
      <DagGraph
        nodes={nodes}
        edges={edges}
        selectedNode={null}
        onSelectedNodeChange={onSelectedNodeChange}
      />
    )
    rerender(
      <DagGraph
        nodes={nodes}
        edges={edges}
        selectedNode="a"
        onSelectedNodeChange={onSelectedNodeChange}
      />
    )
    expect(mocks.setCenter).toHaveBeenCalledTimes(2)
  })

  it('selectionNonce 变化（key 不变）时重新定位——已选中节点被再次请求定位', () => {
    // 移动端在 Agent 面板点草稿 diff 里已选中的同一节点：setter 写相同值
    // 不触发选中更新，只有 nonce 变化能驱动镜头再次定位。
    const { rerender } = render(
      <DagGraph
        nodes={nodes}
        edges={edges}
        selectedNode="a"
        selectionNonce={0}
      />
    )
    expect(mocks.setCenter).toHaveBeenCalledTimes(1)

    rerender(
      <DagGraph
        nodes={nodes}
        edges={edges}
        selectedNode="a"
        selectionNonce={1}
      />
    )
    expect(mocks.setCenter).toHaveBeenCalledTimes(2)
    expect(mocks.setCenter).toHaveBeenLastCalledWith(240, 90, {
      zoom: 1,
      duration: 350,
    })

    // nonce 也不变时不重复飞行。
    rerender(
      <DagGraph
        nodes={nodes}
        edges={edges}
        selectedNode="a"
        selectionNonce={1}
      />
    )
    expect(mocks.setCenter).toHaveBeenCalledTimes(2)
  })
})
