import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { DagStepper } from './DagStepper'
import type { JobNode } from '../../types/jobTypes'

// 可控 ResizeObserver 桩（#1335）：observe 不自动回调，测试用 fire(width)
// 显式报告轨道实测宽度；不 fire 即「宽度未测得」，走节点数阈值回落。
class ResizeObserverStub {
  static instances: ResizeObserverStub[] = []
  private callback: ResizeObserverCallback
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
    ResizeObserverStub.instances.push(this)
  }
  observe = () => {}
  unobserve = () => {}
  disconnect = () => {}
  fire(width: number) {
    this.callback(
      [{ contentRect: { width } } as unknown as ResizeObserverEntry],
      this as unknown as ResizeObserver
    )
  }
}

function fireTrackWidth(width: number) {
  const instance =
    ResizeObserverStub.instances[ResizeObserverStub.instances.length - 1]
  act(() => instance?.fire(width))
}

const originalResizeObserver = globalThis.ResizeObserver

beforeEach(() => {
  ResizeObserverStub.instances = []
  globalThis.ResizeObserver =
    ResizeObserverStub as unknown as typeof ResizeObserver
})

afterEach(() => {
  globalThis.ResizeObserver = originalResizeObserver
})

describe('DagStepper', () => {
  const makeNodes = (count: number) =>
    Array.from({ length: count }, (_, i) => ({
      id: i,
      job_id: 'job-1',
      node_key: `node-${i}`,
      label: `节点 ${i}`,
      status: i === 0 ? 'completed' : i === 1 ? 'running' : 'pending',
      after: i > 0 ? [`node-${i - 1}`] : [],
    })) as JobNode[]

  it('does not render node labels', () => {
    render(<DagStepper nodes={makeNodes(8)} />)
    expect(screen.queryByText('节点 0')).not.toBeInTheDocument()
    expect(screen.queryByText('节点 7')).not.toBeInTheDocument()
  })

  it('applies status classes', () => {
    const { container } = render(<DagStepper nodes={makeNodes(3)} />)
    const bars = container.querySelectorAll('[class*="stepBar"]')
    expect(bars[0].className).toContain('completed')
    expect(bars[1].className).toContain('running')
    expect(bars[2].className).toContain('pending')
  })

  it('maps stale status to pending class', () => {
    const { container } = render(
      <DagStepper
        nodes={
          [
            {
              id: 0,
              job_id: 'job-1',
              node_key: 'stale-node',
              label: 'Stale Node',
              status: 'stale',
              after: [],
              capability: 'test',
              error_message: '',
              inputs: [],
              outputs: [],
              stale_reason: '',
            },
          ] as unknown as JobNode[]
        }
      />
    )
    const bar = container.querySelector('[class*="stepBar"]')
    expect(bar?.className).toContain('pending')
    expect(bar?.className).not.toContain('stale')
  })

  it('keeps the default 4px gap for small node counts', () => {
    const { container } = render(<DagStepper nodes={makeNodes(8)} />)
    expect((container.firstChild as HTMLElement).style.gap).toBe('4px')
  })

  it('shrinks the gap at 50 nodes on the 200px track so every step stays visible (#1124)', () => {
    const { container } = render(<DagStepper nodes={makeNodes(50)} />)
    fireTrackWidth(200)
    // 200px track：50 × 2px 段 + 49 × gap ≤ 200，gap 必须收缩到 2px
    // （保持 4px 时 gap 总量 196px，段被压成 0 宽）。
    expect((container.firstChild as HTMLElement).style.gap).toBe('2px')
  })

  it('computes the gap from the measured width, not a hardcoded constant (#1335)', () => {
    const { container } = render(<DagStepper nodes={makeNodes(50)} />)
    fireTrackWidth(400)
    // 400px 实测宽：每段预算翻倍，gap 回到 4px 上限。
    expect((container.firstChild as HTMLElement).style.gap).toBe('4px')
  })
})

describe('DagStepper 紧凑形态（#1335 宽度驱动）', () => {
  const makeMany = (count: number) =>
    Array.from({ length: count }, (_, i) => ({
      id: i,
      job_id: 'job-1',
      node_key: `node-${i}`,
      label: `节点 ${i}`,
      status:
        i < 10 ? 'completed' : i === 10 ? 'running' : ('pending' as const),
      after: i > 0 ? [`node-${i - 1}`] : [],
    })) as JobNode[]

  it('keeps the segmented form while per-segment width stays at the 2px floor', () => {
    // 200px 轨道：100 段 × 2px 刚好铺满，仍在可辨识下限上。
    const { container } = render(<DagStepper nodes={makeMany(100)} />)
    fireTrackWidth(200)
    expect(container.querySelectorAll('[class*="stepBar"]')).toHaveLength(100)
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('switches to compact one node past the width-derived threshold', () => {
    // 同一 200px 轨道：101 段每段不足 2px → 紧凑形态。
    const { container } = render(<DagStepper nodes={makeMany(101)} />)
    fireTrackWidth(200)

    expect(container.querySelectorAll('[class*="stepBar"]')).toHaveLength(0)
    // 数字进度 + 当前节点名（running 节点为当前节点）
    expect(screen.getByText('11/101')).toBeInTheDocument()
    expect(screen.getByText('节点 10')).toBeInTheDocument()
    const progress = screen.getByRole('progressbar', { name: '节点进度' })
    expect(progress).toHaveAttribute('aria-valuemax', '101')
    expect(progress).toHaveAttribute('aria-valuenow', '10')
  })

  it('keeps mid-size workflows segmented on a wide track (#1335)', () => {
    // 宽轨道（800px）：80 段每段 10px，不再被固定阈值 50 过早切成数字形态。
    const { container } = render(<DagStepper nodes={makeMany(80)} />)
    fireTrackWidth(800)
    expect(container.querySelectorAll('[class*="stepBar"]')).toHaveLength(80)
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('switches form live when the track width changes, both directions', () => {
    // 60 段：400px 轨道逐段（每段 6.7px）→ 缩到 110px（每段 1.8px）切紧凑
    // → 拉宽还原，无抖动卡死。
    const { container } = render(<DagStepper nodes={makeMany(60)} />)
    fireTrackWidth(400)
    expect(container.querySelectorAll('[class*="stepBar"]')).toHaveLength(60)

    fireTrackWidth(110)
    expect(container.querySelectorAll('[class*="stepBar"]')).toHaveLength(0)
    expect(screen.getByText('11/60')).toBeInTheDocument()

    fireTrackWidth(400)
    expect(container.querySelectorAll('[class*="stepBar"]')).toHaveLength(60)
  })

  it('keeps the DOM size constant at 200 nodes (no overflow/crowding)', () => {
    const { container } = render(<DagStepper nodes={makeMany(200)} />)
    fireTrackWidth(200)

    expect(screen.getByText('11/200')).toBeInTheDocument()
    expect(screen.getByText('节点 10')).toBeInTheDocument()
    expect(container.querySelectorAll('[class*="stepBar"]')).toHaveLength(0)
    expect(container.querySelectorAll('div').length).toBeLessThan(10)
  })

  it('falls back to the count threshold when no width is reported (#1257)', () => {
    // 无 ResizeObserver / 首帧回调前：保持节点数阈值 50 的旧行为。
    const { container, unmount } = render(<DagStepper nodes={makeMany(51)} />)
    expect(screen.getByText('11/51')).toBeInTheDocument()
    expect(container.querySelectorAll('[class*="stepBar"]')).toHaveLength(0)
    unmount()

    for (const count of [49, 50]) {
      const { container: c, unmount: u } = render(
        <DagStepper nodes={makeMany(count)} />
      )
      expect(c.querySelectorAll('[class*="stepBar"]')).toHaveLength(count)
      u()
    }
  })

  it('supports a custom compactThreshold as an explicit override', () => {
    // 显式 prop 覆盖宽度判定：宽轨道上 11 段也切紧凑。
    const { container } = render(
      <DagStepper nodes={makeMany(11)} compactThreshold={10} />
    )
    fireTrackWidth(800)
    expect(screen.getByText('11/11')).toBeInTheDocument()
    expect(container.querySelectorAll('[class*="stepBar"]')).toHaveLength(0)
  })

  it('reports the last node and a full bar when all nodes completed', () => {
    const nodes = makeMany(57).map((n) => ({ ...n, status: 'completed' }))
    render(<DagStepper nodes={nodes} />)
    fireTrackWidth(100)

    expect(screen.getByText('57/57')).toBeInTheDocument()
    expect(screen.getByText('节点 56')).toBeInTheDocument()
    const progress = screen.getByRole('progressbar', { name: '节点进度' })
    expect(progress).toHaveAttribute('aria-valuenow', '57')
  })
})
