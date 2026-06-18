import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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
})
