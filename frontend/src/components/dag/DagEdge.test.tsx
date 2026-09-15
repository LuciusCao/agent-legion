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
  // 描边视觉必须与重构前逐字段一致（置灰 #d1d5db 2/0.4，高亮蓝 3/1）。
  // #668：常态不再走这里——undefined highlighted 透传 buildRfEdges 的
  // 原始 style（#6b7280 / 2.5 / 全亮），false 只表示「高亮模式内置灰」。
  it('renders dimmed stroke when off-chain in highlight mode', () => {
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

  it('treats missing data as the un-highlighted baseline (no style override)', () => {
    // 三态语义（Codex review on #285）：undefined 是「从未进入高亮模式」，
    // 不覆盖 buildRfEdges 的原始 style——普通边不透明。旧双态语义会把
    // 常态边强制盖成 0.4 透明度。#668 起 buildRfEdges 的初始 data 即为
    // 此形态（highlighted 缺省），常态描边 #6b7280 / 2.5 由此透传。
    const path = edgePathOf(
      renderEdge(undefined, {
        stroke: '#6b7280',
        strokeWidth: 2.5,
        opacity: 0.5,
      })
    )
    expect(path.style.stroke).toBe('#6b7280')
    expect(path.style.opacity).toBe('0.5')
  })

  it('keeps conditional dashed style from edge style prop', () => {
    // buildRfEdges 为条件边写入 strokeDasharray（'6 4'）；DagEdge 需要
    // 原样透传，不能因高亮重构丢失。
    const path = edgePathOf(
      renderEdge(
        { highlighted: false },
        {
          stroke: '#6b7280',
          strokeWidth: 2.5,
          strokeDasharray: '6 4',
          opacity: undefined,
        }
      )
    )
    expect(path.style.strokeDasharray).toBe('6 4')
  })
})
