import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Routes, Route, useNavigate } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import WorkspaceLayout from './WorkspaceLayout'
import { useUiStore } from '../stores/uiStore'

function LayoutWithNavigator() {
  const navigate = useNavigate()
  return (
    <>
      <button data-testid="navigate-job" onClick={() => navigate('jobs/j1')}>
        Go to job
      </button>
      <WorkspaceLayout />
    </>
  )
}

vi.mock('../stores/workspaceStore', () => ({
  useWorkspaceStore: () => ({
    workspaces: [
      {
        id: 'ws1',
        name: '测试空间',
        default_workflow_key: 'question_content',
        default_entity: 'question',
      },
    ],
    currentWorkspace: {
      id: 'ws1',
      name: '测试空间',
      default_workflow_key: 'question_content',
      default_entity: 'question',
    },
    workspaceStats: {
      ws1: { workflow_key: 'question_content', job_stats: {} },
    },
    fetchWorkspaces: vi.fn(),
    setCurrentWorkspace: vi.fn(),
    fetchWorkspaceStats: vi.fn(),
  }),
}))

vi.mock('../views/WorkspaceJobList', () => ({
  default: () => <div data-testid="job-list">JobList</div>,
}))

vi.mock('../views/WorkspaceJobDetail', () => ({
  default: () => <div data-testid="job-detail">JobDetail</div>,
}))

vi.mock('../components/AgentStatusIndicator', () => ({
  AgentStatusIndicator: () => <div data-testid="agent-status">Agent</div>,
}))

describe('WorkspaceLayout token usage dialog (real uiStore)', () => {
  beforeEach(() => {
    useUiStore.setState({
      tokenUsageDialogOpen: false,
      fetchWorkerStatus: vi.fn(),
    })
  })

  it('keeps the token usage dialog open after the analytics button is clicked', () => {
    render(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceLayout />}
          />
        </Routes>
      </MemoryRouter>
    )

    fireEvent.click(screen.getByLabelText('Token 使用分析'))

    expect(useUiStore.getState().tokenUsageDialogOpen).toBe(true)
  })

  it('closes the token usage dialog when the route changes', () => {
    render(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<LayoutWithNavigator />}
          />
        </Routes>
      </MemoryRouter>
    )

    fireEvent.click(screen.getByLabelText('Token 使用分析'))
    expect(useUiStore.getState().tokenUsageDialogOpen).toBe(true)

    fireEvent.click(screen.getByTestId('navigate-job'))

    expect(useUiStore.getState().tokenUsageDialogOpen).toBe(false)
  })
})
