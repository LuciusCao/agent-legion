import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import WorkspaceLayout from './WorkspaceLayout'
import { useUiStore } from '../stores/uiStore'

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

describe('WorkspaceLayout token usage navigation', () => {
  beforeEach(() => {
    useUiStore.setState({
      fetchWorkerStatus: vi.fn().mockResolvedValue(undefined),
    })
  })

  it('navigates to token-usage page when analytics button is clicked', () => {
    render(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceLayout />}
          >
            <Route
              index
              element={<div data-testid="workspace-main">Main</div>}
            />
          </Route>
          <Route
            path="/workspaces/:workspaceId/token-usage"
            element={<div data-testid="token-usage-page">Token Usage</div>}
          />
        </Routes>
      </MemoryRouter>
    )

    fireEvent.click(screen.getByLabelText('Token 使用分析'))

    expect(screen.getByTestId('token-usage-page')).toBeInTheDocument()
  })
})
