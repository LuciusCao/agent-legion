import { render } from '@testing-library/react'
import { beforeEach, describe, it, expect } from 'vitest'
import { ReactFlowProvider } from '@xyflow/react'
import { DagEdge, DagEdgeData } from './DagEdge'
import { expectConsoleError } from '../../test-setup'

// jsdom 不认识 SVG 小写标签（<path> 等），React 会告警 "unrecognized tag"。
// 这是渲染环境噪音，与被测逻辑无关，登记后放行。
beforeEach(() => {
  expectConsoleError(/unrecognized in this browser/)
})

type EdgePropsShape = Parameters<typeof DagEdge>[0]

function edgePathOf(view: { container: HTMLElement }) {
  const path = view.container.querySelector<SVGPathElement>(
    '.react-flow__edge-path'
  )
  expect(path).not.toBeNull()
  return path!
}

function renderEdge(
  data: DagEdgeData | undefined,
  style?: EdgePropsShape['style']
) {
  const base = {
    id: 'e-1',
    source: 'a',
    target: 'b',
    sourceX: 0,
    sourceY: 0,
    targetX: 200,
    targetY: 0,
    sourcePosition: 'right',
    targetPosition: 'left',
    data,
    style,
  }
  return render(
    <ReactFlowProvider>
      <DagEdge {...(base as EdgePropsShape)} />
    </ReactFlowProvider>
  )
}

describe('DagEdge', () => {
  // #276：高亮态从 DagGraph 内联的 edge.style 下沉到 data.highlighted 后，
  // 描边视觉必须与重构前逐字段一致（默认灰 2/0.4，高亮蓝 3/1）。
  it('renders default stroke when not highlighted', () => {
    const path = edgePathOf(renderEdge({ highlighted: false }))
    expect(path.style.stroke).toBe('#d1d5db')
    expect(path.style.strokeWidth).toBe('2')
    expect(path.style.opacity).toBe('0.4')
  })

  it('renders highlighted stroke when data.highlighted is true', () => {
    const path = edgePathOf(renderEdge({ highlighted: true }))
    expect(path.style.stroke).toBe('#1d4ed8')
    expect(path.style.strokeWidth).toBe('3')
    expect(path.style.opacity).toBe('1')
  })

  it('treats missing data as not highlighted', () => {
    const path = edgePathOf(renderEdge(undefined))
    expect(path.style.stroke).toBe('#d1d5db')
    expect(path.style.strokeWidth).toBe('2')
    expect(path.style.opacity).toBe('0.4')
  })

  it('keeps conditional dashed style from edge style prop', () => {
    // buildRfEdges 为条件边写入 strokeDasharray（'6 4'）；DagEdge 需要
    // 原样透传，不能因高亮重构丢失。
    const path = edgePathOf(
      renderEdge(
        { highlighted: false },
        {
          stroke: '#9ca3af',
          strokeWidth: 2,
          strokeDasharray: '6 4',
          opacity: undefined,
        }
      )
    )
    expect(path.style.strokeDasharray).toBe('6 4')
  })
})
