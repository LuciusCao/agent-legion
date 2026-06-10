import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { JobList } from './JobList'
import { useJobStore } from '../stores/jobStore'
import type { JobRecord } from '../types'

vi.mock('../api', () => ({
  fetchJobs: vi.fn(),
  api: vi.fn(),
}))

import { fetchJobs } from '../api'

const mockFetchJobs = vi.mocked(fetchJobs)

const mockJobs: JobRecord[] = [
  {
    id: 'j1',
    workspace_id: 'ws1',
    pipeline_key: 'p1',
    source_id: 'Q100',
    title: 'Algebra',
    stem: '',
    status: 'running',
    created_at: new Date(Date.now() - 2 * 60 * 1000).toISOString(),
    completed_nodes: 2,
    total_nodes: 5,
  },
  {
    id: 'j2',
    workspace_id: 'ws1',
    pipeline_key: 'p1',
    source_id: 'Q200',
    title: 'Geometry',
    stem: '',
    status: 'completed',
    created_at: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
    completed_nodes: 5,
    total_nodes: 5,
  },
]

describe('JobList', () => {
  beforeEach(() => {
    mockFetchJobs.mockReset()
    useJobStore.setState({
      jobs: mockJobs,
      isLoading: false,
      error: null,
      selectedIds: new Set(),
      expandedId: null,
      statusFilter: 'all',
      searchQuery: '',
    })
  })

  it('renders one JobListItem per job', async () => {
    mockFetchJobs.mockResolvedValueOnce({ jobs: mockJobs })
    await act(async () => {
      render(
        <MemoryRouter initialEntries={['/workspaces/ws1']}>
          <Routes>
            <Route
              path="/workspaces/:workspaceId/*"
              element={<JobList workspaceId="ws1" />}
            />
          </Routes>
        </MemoryRouter>
      )
    })

    expect(screen.getByText('Algebra - Q100')).toBeInTheDocument()
    expect(screen.getByText('Geometry - Q200')).toBeInTheDocument()
  })

  it('expanding a job shows ExpandedJobPanel', async () => {
    mockFetchJobs.mockResolvedValueOnce({ jobs: mockJobs })
    await act(async () => {
      render(
        <MemoryRouter initialEntries={['/workspaces/ws1']}>
          <Routes>
            <Route
              path="/workspaces/:workspaceId/*"
              element={<JobList workspaceId="ws1" />}
            />
          </Routes>
        </MemoryRouter>
      )
    })

    expect(screen.queryByText('节点流水线')).not.toBeInTheDocument()

    fireEvent.click(screen.getAllByText('展开 ▼')[0])

    expect(screen.getByText('节点流水线')).toBeInTheDocument()
    expect(screen.getByText('运行记录')).toBeInTheDocument()
  })

  it('empty state shows 暂无任务', async () => {
    useJobStore.setState({ jobs: [] })
    mockFetchJobs.mockResolvedValueOnce({ jobs: [] })
    await act(async () => {
      render(
        <MemoryRouter initialEntries={['/workspaces/ws1']}>
          <Routes>
            <Route
              path="/workspaces/:workspaceId/*"
              element={<JobList workspaceId="ws1" />}
            />
          </Routes>
        </MemoryRouter>
      )
    })

    expect(screen.getByText('暂无任务')).toBeInTheDocument()
  })
})
