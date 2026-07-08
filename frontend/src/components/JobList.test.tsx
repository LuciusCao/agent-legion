import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
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
    workflow_key: 'p1',
    source_id: 'Q100',
    source_type: 'question',
    title: 'Algebra',
    status: 'running',
    batch_id: 'b1',
    created_at: new Date(Date.now() - 2 * 60 * 1000).toISOString(),
    updated_at: new Date().toISOString(),
    storage_dir: '/tmp/j1',
    error_message: '',
    error_summary: '',
    completed_nodes: 2,
    total_nodes: 5,
    workflow_revision_id: '',
    workflow_version: null,
    workflow_definition_hash: '',
    outcome: '',
    current_workflow_revision_id: '',
    current_workflow_revision_version: null,
    is_workflow_outdated: false,
    packed: 0,
  },
  {
    id: 'j2',
    workspace_id: 'ws1',
    workflow_key: 'p1',
    source_id: 'Q200',
    source_type: 'question',
    title: 'Geometry',
    status: 'completed',
    batch_id: 'b1',
    created_at: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
    updated_at: new Date().toISOString(),
    storage_dir: '/tmp/j2',
    error_message: '',
    error_summary: '',
    completed_nodes: 5,
    total_nodes: 5,
    workflow_revision_id: '',
    workflow_version: null,
    workflow_definition_hash: '',
    outcome: '',
    current_workflow_revision_id: '',
    current_workflow_revision_version: null,
    is_workflow_outdated: false,
    packed: 0,
  },
]

describe('JobList', () => {
  beforeEach(() => {
    mockFetchJobs.mockReset()
    const jobIds = mockJobs.map((job) => job.id)
    const jobsById = Object.fromEntries(mockJobs.map((job) => [job.id, job]))
    useJobStore.setState({
      jobs: mockJobs,
      jobsById,
      jobIds,
      jobIndexById: Object.fromEntries(jobIds.map((id, index) => [id, index])),
      filteredJobIds: jobIds,
      filterCounts: {
        status: { all: 2, pending: 1, running: 1 },
        workflowVersion: { all: 2, none: 2 },
        activeNodeKey: { all: 2 },
      },
      isLoading: false,
      error: null,
      selectedIds: new Set(),
      expandedId: null,
      filterConfig: {
        status: null,
        search: '',
        workflowVersion: null,
        activeNodeKey: null,
      },
    })
  })

  it('renders one JobListItem per job', () => {
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

    expect(screen.getByText('Algebra')).toBeInTheDocument()
    expect(screen.getByText('Geometry')).toBeInTheDocument()
    expect(screen.getByText(/题目 · Q100/)).toBeInTheDocument()
    expect(screen.getByText(/题目 · Q200/)).toBeInTheDocument()
    expect(mockFetchJobs).not.toHaveBeenCalled()
  })

  it('empty state shows 暂无任务', () => {
    useJobStore.setState({
      jobs: [],
      jobsById: {},
      jobIds: [],
      jobIndexById: {},
      filteredJobIds: [],
    })
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

    expect(screen.getByText('暂无任务')).toBeInTheDocument()
    expect(mockFetchJobs).not.toHaveBeenCalled()
  })

  it('renders skeleton while loading', () => {
    useJobStore.setState({ jobs: [], filteredJobIds: [], isLoading: true })
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

    expect(screen.getByTestId('job-list-skeleton')).toBeInTheDocument()
    expect(screen.queryByText('暂无任务')).not.toBeInTheDocument()
  })
})
