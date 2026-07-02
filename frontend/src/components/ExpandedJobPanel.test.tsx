import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { ExpandedJobPanel } from './ExpandedJobPanel'
import type { JobRecord } from '../types'

const mockJob: JobRecord = {
  id: 'j1',
  workspace_id: 'ws1',
  workflow_key: 'p1',
  source_id: 'Q100',
  source_type: 'question',
  title: 'Algebra Problem',
  status: 'running',
  batch_id: 'b1',
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  storage_dir: '/tmp/j1',
  error_message: '',
  error_summary: '',
  completed_nodes: 2,
  total_nodes: 5,
  workflow_revision_id: '',
  workflow_definition_hash: '',
  outcome: '',
  current_workflow_revision_id: '',
  current_workflow_revision_version: null,
}

function renderPanel(
  props: Partial<Parameters<typeof ExpandedJobPanel>[0]> = {}
) {
  return render(
    <MemoryRouter initialEntries={['/workspaces/ws1/jobs']}>
      <Routes>
        <Route
          path="/workspaces/:workspaceId/jobs"
          element={
            <ExpandedJobPanel
              job={mockJob}
              onViewDetail={props.onViewDetail ?? vi.fn()}
              onRerun={props.onRerun ?? vi.fn()}
              onRunTo={props.onRunTo ?? vi.fn()}
              onDelete={props.onDelete ?? vi.fn()}
              workspaceId={props.workspaceId}
            />
          }
        />
        <Route
          path="/workspaces/:workspaceId/jobs/:jobId"
          element={<div data-testid="job-detail">Job Detail</div>}
        />
      </Routes>
    </MemoryRouter>
  )
}

describe('ExpandedJobPanel', () => {
  it('renders MiniDag with default nodes', () => {
    const { container } = renderPanel()

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
    renderPanel()

    expect(screen.getByText('节点')).toBeInTheDocument()
    expect(screen.getByText('状态')).toBeInTheDocument()
    expect(screen.getByText('时间')).toBeInTheDocument()
    expect(screen.getByText('耗时')).toBeInTheDocument()
  })

  it('renders action buttons', () => {
    renderPanel()

    expect(screen.getByText('查看完整详情')).toBeInTheDocument()
    expect(screen.getByText('重跑')).toBeInTheDocument()
    expect(screen.getByText('运行到...')).toBeInTheDocument()
    expect(screen.getByText('删除')).toBeInTheDocument()
  })

  it('calls respective handlers when action buttons are clicked', () => {
    const onViewDetail = vi.fn()
    const onRerun = vi.fn()
    const onRunTo = vi.fn()
    const onDelete = vi.fn()

    renderPanel({ onViewDetail, onRerun, onRunTo, onDelete })

    fireEvent.click(screen.getByText('查看完整详情'))
    expect(onViewDetail).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByText('重跑'))
    expect(onRerun).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByText('运行到...'))
    expect(onRunTo).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByText('删除'))
    expect(onDelete).toHaveBeenCalledTimes(1)
  })

  it('navigates to job detail when workspaceId is provided', () => {
    renderPanel({ workspaceId: 'ws1' })

    fireEvent.click(screen.getByText('查看完整详情'))
    expect(screen.getByTestId('job-detail')).toBeInTheDocument()
  })
})
