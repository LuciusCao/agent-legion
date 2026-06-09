import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { NodeDetailPanel } from './NodeDetailPanel'

describe('NodeDetailPanel', () => {
  const baseNode = {
    key: 'extract',
    label: '提取',
    status: 'completed',
    startedAt: '2026-06-09T08:00:00Z',
    endedAt: '2026-06-09T08:00:45Z',
    duration: 45,
    agentId: 'agent-1',
  }

  it('renders node info', () => {
    render(
      <NodeDetailPanel
        node={baseNode}
        onViewLogs={vi.fn()}
        onRerunNode={vi.fn()}
      />
    )

    expect(screen.getByText('提取')).toBeInTheDocument()
    expect(screen.getByText('已完成', { selector: 'dd' })).toBeInTheDocument()
    expect(screen.getByText('45秒')).toBeInTheDocument()
    expect(screen.getByText('agent-1')).toBeInTheDocument()
  })

  it('renders buttons and calls handlers', () => {
    const onViewLogs = vi.fn()
    const onRerunNode = vi.fn()

    render(
      <NodeDetailPanel
        node={baseNode}
        onViewLogs={onViewLogs}
        onRerunNode={onRerunNode}
      />
    )

    fireEvent.click(screen.getByTestId('view-logs-btn'))
    expect(onViewLogs).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByTestId('rerun-node-btn'))
    expect(onRerunNode).toHaveBeenCalledTimes(1)
  })

  it('handles null node gracefully', () => {
    render(
      <NodeDetailPanel node={null} onViewLogs={vi.fn()} onRerunNode={vi.fn()} />
    )

    expect(screen.getByText('选择一个节点查看详情')).toBeInTheDocument()
    expect(screen.queryByTestId('view-logs-btn')).not.toBeInTheDocument()
  })

  it('shows dashes for missing optional fields', () => {
    render(
      <NodeDetailPanel
        node={{
          key: 'pending-node',
          label: '待处理节点',
          status: 'pending',
        }}
        onViewLogs={vi.fn()}
        onRerunNode={vi.fn()}
      />
    )

    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThanOrEqual(4)
  })
})
