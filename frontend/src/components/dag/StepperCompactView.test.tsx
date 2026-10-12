import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StepperCompactView, stepperCompactInfo } from './StepperCompactView'

const seg = (key: string, status: string, label = key) => ({
  node_key: key,
  label,
  status,
})

describe('stepperCompactInfo (#1257)', () => {
  it('returns null for empty segments', () => {
    expect(stepperCompactInfo([])).toBeNull()
  })

  it('prefers the active node key and reports its 1-based position', () => {
    const segments = [
      seg('a', 'completed', '节点 A'),
      seg('b', 'running', '节点 B'),
      seg('c', 'pending', '节点 C'),
    ]
    const info = stepperCompactInfo(segments, 'c')
    expect(info).toMatchObject({
      current: 3,
      total: 3,
      completed: 1,
      currentKey: 'c',
      currentLabel: '节点 C',
      currentStatus: 'pending',
    })
  })

  it('falls back to running, then failed, then stale, then first pending', () => {
    expect(
      stepperCompactInfo([seg('a', 'completed'), seg('b', 'running')])?.current
    ).toBe(2)
    expect(
      stepperCompactInfo([seg('a', 'failed'), seg('b', 'pending')])?.current
    ).toBe(1)
    expect(
      stepperCompactInfo([seg('a', 'stale'), seg('b', 'pending')])?.current
    ).toBe(1)
    expect(
      stepperCompactInfo([seg('a', 'completed'), seg('b', 'pending')])?.current
    ).toBe(2)
  })

  it('uses the last node when everything is completed', () => {
    const info = stepperCompactInfo([
      seg('a', 'completed'),
      seg('b', 'completed'),
    ])
    expect(info?.current).toBe(2)
    expect(info?.completed).toBe(2)
  })

  it('ignores an activeNodeKey that is not in the segments', () => {
    const segments = [seg('a', 'completed'), seg('b', 'running')]
    expect(stepperCompactInfo(segments, 'missing')?.current).toBe(2)
  })
})

describe('StepperCompactView (#1257)', () => {
  const segments = [
    seg('a', 'completed', '节点 A'),
    seg('b', 'running', '节点 B'),
    seg('c', 'pending', '节点 C'),
  ]

  it('renders count with current-node status color and progressbar semantics', () => {
    render(<StepperCompactView segments={segments} showLabel />)

    const count = screen.getByText('2/3')
    expect(count).toHaveAttribute('data-status', 'running')
    expect(count).toHaveAttribute('title', '节点 B')

    const progress = screen.getByRole('progressbar', { name: '节点进度' })
    expect(progress).toHaveAttribute('aria-valuemin', '0')
    expect(progress).toHaveAttribute('aria-valuemax', '3')
    expect(progress).toHaveAttribute('aria-valuenow', '1')
    expect(progress).toHaveAttribute('aria-valuetext', '2/3 节点 B')
  })

  it('shows or hides the inline current-node label via showLabel', () => {
    const { unmount } = render(
      <StepperCompactView segments={segments} showLabel />
    )
    expect(screen.getByText('节点 B')).toBeInTheDocument()
    unmount()

    render(<StepperCompactView segments={segments} showLabel={false} />)
    expect(screen.queryByText('节点 B')).not.toBeInTheDocument()
    // 名字仍经 title / aria 暴露
    expect(screen.getByText('2/3')).toHaveAttribute('title', '节点 B')
  })

  it('marks data-active only when the resolved node matches activeNodeKey', () => {
    const { unmount } = render(
      <StepperCompactView segments={segments} activeNodeKey="b" showLabel />
    )
    expect(screen.getByText('2/3')).toHaveAttribute('data-active', 'true')
    unmount()

    render(<StepperCompactView segments={segments} showLabel />)
    expect(screen.getByText('2/3')).not.toHaveAttribute('data-active')
  })

  it('pulses the fill bar while the current node is running', () => {
    const { container, unmount } = render(
      <StepperCompactView segments={segments} showLabel />
    )
    expect(
      container.querySelector('[class*="compactFill"]')?.className
    ).toContain('pulse-blue')
    unmount()

    const allDone = segments.map((s) => ({ ...s, status: 'completed' }))
    const { container: done } = render(
      <StepperCompactView segments={allDone} showLabel />
    )
    expect(
      done.querySelector('[class*="compactFill"]')?.className
    ).not.toContain('pulse-blue')
  })

  it('renders nothing for empty segments', () => {
    const { container } = render(<StepperCompactView segments={[]} showLabel />)
    expect(container).toBeEmptyDOMElement()
  })
})
