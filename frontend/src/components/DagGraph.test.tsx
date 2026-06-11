import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { DagGraph } from './DagGraph'

const nodes = [
  {
    key: 'a',
    label: '提取',
    status: 'completed' as const,
    inputs: [],
    outputs: ['out.json'],
  },
  {
    key: 'b',
    label: '生成',
    status: 'running' as const,
    inputs: ['out.json'],
    outputs: ['gen.json'],
  },
  {
    key: 'c',
    label: '审核',
    status: 'pending' as const,
    inputs: ['gen.json'],
    outputs: [],
  },
]
const edges = [
  { from: 'a', to: 'b' },
  { from: 'b', to: 'c' },
]

describe('DagGraph', () => {
  it('renders nodes with labels', () => {
    render(<DagGraph nodes={nodes} edges={edges} />)
    expect(screen.getByText('提取')).toBeInTheDocument()
    expect(screen.getByText('生成')).toBeInTheDocument()
    expect(screen.getByText('审核')).toBeInTheDocument()
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

  it('renders empty state when no nodes', () => {
    const { container } = render(<DagGraph nodes={[]} edges={[]} />)
    expect(container.querySelector('svg')).toBeInTheDocument()
    expect(container.querySelectorAll('[data-node]')).toHaveLength(0)
  })

  it('renders edges', () => {
    const { container } = render(<DagGraph nodes={nodes} edges={edges} />)
    expect(container.querySelectorAll('path[data-testid="edge"]')).toHaveLength(
      2
    )
  })

  it('renders status icons', () => {
    const { container } = render(<DagGraph nodes={nodes} edges={edges} />)
    const nodeA = container.querySelector('[data-node="a"]')
    const nodeB = container.querySelector('[data-node="b"]')
    expect(nodeA).toHaveTextContent('check_circle')
    expect(nodeB).toHaveTextContent('hourglass_empty')
  })

  it('renders inputs/outputs chips', () => {
    const { container } = render(<DagGraph nodes={nodes} edges={edges} />)
    expect(container.querySelector('[data-node="a"]')).toHaveTextContent(
      'out.json'
    )
    expect(container.querySelector('[data-node="b"]')).toHaveTextContent(
      'out.json'
    )
    expect(container.querySelector('[data-node="b"]')).toHaveTextContent(
      'gen.json'
    )
  })

  it('does not call onNodeClick when not provided', () => {
    render(<DagGraph nodes={nodes} edges={edges} />)
    expect(() => fireEvent.click(screen.getByText('提取'))).not.toThrow()
  })

  it('calls onNodeClick on Enter key', () => {
    const onClick = vi.fn()
    render(<DagGraph nodes={nodes} edges={edges} onNodeClick={onClick} />)
    const nodeA = screen.getByRole('button', { name: '提取' })
    fireEvent.keyDown(nodeA, { key: 'Enter' })
    expect(onClick).toHaveBeenCalledWith('a')
  })

  it('calls onNodeClick on Space key', () => {
    const onClick = vi.fn()
    render(<DagGraph nodes={nodes} edges={edges} onNodeClick={onClick} />)
    const nodeA = screen.getByRole('button', { name: '提取' })
    fireEvent.keyDown(nodeA, { key: ' ' })
    expect(onClick).toHaveBeenCalledWith('a')
  })

  it('exposes accessible labels', () => {
    render(<DagGraph nodes={nodes} edges={edges} />)
    expect(screen.getByRole('button', { name: '提取' })).toBeInTheDocument()
  })
})
