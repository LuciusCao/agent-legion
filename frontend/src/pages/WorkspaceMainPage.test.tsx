import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import WorkspaceMainPage from './WorkspaceMainPage'
import { useWorkspaceStore } from '../stores/workspaceStore'

vi.mock('../stores/workspaceStore', () => ({
  useWorkspaceStore: vi.fn(() => ({
    fetchWorkspaceStats: vi.fn(),
    workspaceStats: {
      ws1: { job_stats: { pending: 0, running: 0, completed: 0, failed: 0 } },
    },
  })),
}))

const mockedUseWorkspaceStore = vi.mocked(useWorkspaceStore)

describe('WorkspaceMainPage', () => {
  it('renders all main sections', () => {
    render(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceMainPage />}
          />
        </Routes>
      </MemoryRouter>
    )
    expect(screen.getByText('统计栏占位')).toBeInTheDocument()
    expect(screen.getByText('智能体状态占位')).toBeInTheDocument()
    expect(screen.getByText('过滤 Chips 占位')).toBeInTheDocument()
    expect(screen.getByText('任务列表占位')).toBeInTheDocument()
  })

  it('fetches workspace stats on mount', () => {
    const fetchWorkspaceStats = vi.fn()
    mockedUseWorkspaceStore.mockReturnValue({
      fetchWorkspaceStats,
      workspaceStats: {},
    })
    render(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceMainPage />}
          />
        </Routes>
      </MemoryRouter>
    )
    expect(fetchWorkspaceStats).toHaveBeenCalledWith('ws1')
  })
})
