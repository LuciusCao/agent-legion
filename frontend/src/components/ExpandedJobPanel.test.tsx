import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ExpandedJobPanel } from './ExpandedJobPanel'
import type { JobRecord } from '../types'

const mockJob: JobRecord = {
  id: 'j1',
  workspace_id: 'ws1',
  pipeline_key: 'p1',
  source_id: 'Q100',
  title: 'Algebra Problem',
  status: 'running',
  completed_nodes: 2,
  total_nodes: 5,
}

describe('ExpandedJobPanel', () => {
  it('renders MiniDag with default nodes', () => {
    const { container } = render(
      <ExpandedJobPanel
        job={mockJob}
        onViewDetail={vi.fn()}
        onRerun={vi.fn()}
        onRunTo={vi.fn()}
        onDelete={vi.fn()}
      />
    )

    const dagNodes = container.querySelectorAll('[data-node]')
    expect(dagNodes).toHaveLength(5)
    const track = dagNodes[0]?.parentElement?.parentElement
    expect(track?.textContent).toContain('提取')
    expect(track?.textContent).toContain('生成')
    expect(track?.textContent).toContain('审核')
    expect(track?.textContent).toContain('组装')
    expect(track?.textContent).toContain('打包')
  })

  it('renders node runs table', () => {
    render(
      <ExpandedJobPanel
        job={mockJob}
        onViewDetail={vi.fn()}
        onRerun={vi.fn()}
        onRunTo={vi.fn()}
        onDelete={vi.fn()}
      />
    )

    expect(screen.getByText('节点')).toBeInTheDocument()
    expect(screen.getByText('状态')).toBeInTheDocument()
    expect(screen.getByText('时间')).toBeInTheDocument()
    expect(screen.getByText('耗时')).toBeInTheDocument()
  })

  it('renders action buttons', () => {
    render(
      <ExpandedJobPanel
        job={mockJob}
        onViewDetail={vi.fn()}
        onRerun={vi.fn()}
        onRunTo={vi.fn()}
        onDelete={vi.fn()}
      />
    )

    expect(screen.getByText('查看产物')).toBeInTheDocument()
    expect(screen.getByText('重跑')).toBeInTheDocument()
    expect(screen.getByText('运行到...')).toBeInTheDocument()
    expect(screen.getByText('删除')).toBeInTheDocument()
  })

  it('calls respective handlers when action buttons are clicked', () => {
    const onViewDetail = vi.fn()
    const onRerun = vi.fn()
    const onRunTo = vi.fn()
    const onDelete = vi.fn()

    render(
      <ExpandedJobPanel
        job={mockJob}
        onViewDetail={onViewDetail}
        onRerun={onRerun}
        onRunTo={onRunTo}
        onDelete={onDelete}
      />
    )

    fireEvent.click(screen.getByText('查看产物'))
    expect(onViewDetail).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByText('重跑'))
    expect(onRerun).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByText('运行到...'))
    expect(onRunTo).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByText('删除'))
    expect(onDelete).toHaveBeenCalledTimes(1)
  })
})
