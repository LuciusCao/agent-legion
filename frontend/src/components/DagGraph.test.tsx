import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { DagGraph } from './DagGraph'
import styles from './DagGraph.module.css'

const nodes = [
  { key: 'a', label: '提取', status: 'completed' as const, x: 50, y: 50 },
  { key: 'b', label: '生成', status: 'running' as const, x: 200, y: 50 },
]
const edges = [{ from: 'a', to: 'b' }]

describe('DagGraph', () => {
  it('renders nodes', () => {
    render(<DagGraph nodes={nodes} edges={edges} />)
    expect(screen.getByText('提取')).toBeInTheDocument()
    expect(screen.getByText('生成')).toBeInTheDocument()
  })

  it('calls onNodeClick when a node is clicked', () => {
    const onClick = vi.fn()
    render(<DagGraph nodes={nodes} edges={edges} onNodeClick={onClick} />)
    fireEvent.click(screen.getByText('提取'))
    expect(onClick).toHaveBeenCalledWith('a')
  })

  it('highlights selected node with thicker stroke', () => {
    const { container } = render(
      <DagGraph nodes={nodes} edges={edges} selectedNodeKey="a" />
    )
    const nodeARect = container.querySelector('[data-node="a"] rect')
    expect(nodeARect).toHaveAttribute('stroke-width', '3')
  })

  it('renders empty state when no nodes are provided', () => {
    const { container } = render(<DagGraph nodes={[]} edges={[]} />)
    expect(container.querySelector('svg')).toBeInTheDocument()
    expect(container.querySelectorAll('[data-node]')).toHaveLength(0)
  })

  it('renders edges as lines', () => {
    const { container } = render(<DagGraph nodes={nodes} edges={edges} />)
    const lines = container.querySelectorAll('line')
    expect(lines).toHaveLength(1)
  })

  it('renders status icons', () => {
    const { container } = render(<DagGraph nodes={nodes} edges={edges} />)
    const nodeA = container.querySelector('[data-node="a"]')
    const nodeB = container.querySelector('[data-node="b"]')
    expect(nodeA).toHaveTextContent('check_circle')
    expect(nodeB).toHaveTextContent('hourglass_empty')
  })

  it('renders duration when provided', () => {
    const nodesWithDuration = [
      {
        key: 'a',
        label: '提取',
        status: 'completed' as const,
        x: 50,
        y: 50,
        duration: 45,
      },
      { key: 'b', label: '生成', status: 'running' as const, x: 200, y: 50 },
    ]
    render(<DagGraph nodes={nodesWithDuration} edges={edges} />)
    expect(screen.getByText('45s')).toBeInTheDocument()
  })

  it('applies status fill and stroke colors', () => {
    const { container } = render(<DagGraph nodes={nodes} edges={edges} />)
    const nodeARect = container.querySelector('[data-node="a"] rect')
    const nodeBRect = container.querySelector('[data-node="b"] rect')
    expect(nodeARect).toHaveAttribute('fill', '#dcfce7')
    expect(nodeARect).toHaveAttribute('stroke', '#15803d')
    expect(nodeBRect).toHaveAttribute('fill', '#dbeafe')
    expect(nodeBRect).toHaveAttribute('stroke', '#1d4ed8')
  })

  it('uses dashed line for pending edge and solid for completed edge', () => {
    const mixedNodes = [
      { key: 'a', label: '提取', status: 'completed' as const, x: 50, y: 50 },
      { key: 'b', label: '生成', status: 'running' as const, x: 200, y: 50 },
      { key: 'c', label: '审核', status: 'pending' as const, x: 350, y: 50 },
    ]
    const mixedEdges = [
      { from: 'a', to: 'b' },
      { from: 'b', to: 'c' },
    ]
    const { container } = render(
      <DagGraph nodes={mixedNodes} edges={mixedEdges} />
    )
    const lines = container.querySelectorAll('line')
    expect(lines[0]).toHaveClass(styles.edgeLineCompleted)
    expect(lines[1]).toHaveClass(styles.edgeLinePending)
  })

  it('does not call onNodeClick when not provided', () => {
    render(<DagGraph nodes={nodes} edges={edges} />)
    expect(() => fireEvent.click(screen.getByText('提取'))).not.toThrow()
  })

  it('calls onNodeClick when Enter is pressed on a node', () => {
    const onClick = vi.fn()
    render(<DagGraph nodes={nodes} edges={edges} onNodeClick={onClick} />)
    const nodeA = screen.getByRole('button', { name: '提取' })
    fireEvent.keyDown(nodeA, { key: 'Enter', code: 'Enter' })
    expect(onClick).toHaveBeenCalledWith('a')
  })

  it('calls onNodeClick when Space is pressed on a node', () => {
    const onClick = vi.fn()
    render(<DagGraph nodes={nodes} edges={edges} onNodeClick={onClick} />)
    const nodeA = screen.getByRole('button', { name: '提取' })
    fireEvent.keyDown(nodeA, { key: ' ', code: 'Space' })
    expect(onClick).toHaveBeenCalledWith('a')
  })

  it('does not call onNodeClick for unrelated keys', () => {
    const onClick = vi.fn()
    render(<DagGraph nodes={nodes} edges={edges} onNodeClick={onClick} />)
    const nodeA = screen.getByRole('button', { name: '提取' })
    fireEvent.keyDown(nodeA, { key: 'ArrowDown', code: 'ArrowDown' })
    expect(onClick).not.toHaveBeenCalled()
  })

  it('exposes nodes as buttons with accessible labels', () => {
    render(<DagGraph nodes={nodes} edges={edges} />)
    expect(screen.getByRole('button', { name: '提取' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '生成' })).toBeInTheDocument()
  })

  it('skips edges with unknown nodes', () => {
    const badEdges = [{ from: 'a', to: 'unknown' }]
    const { container } = render(<DagGraph nodes={nodes} edges={badEdges} />)
    expect(container.querySelectorAll('line')).toHaveLength(0)
  })
})
