import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import WorkspaceMainPage from './WorkspaceMainPage'
import { useJobStore } from '../stores/jobStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useUiStore } from '../stores/uiStore'
import { useSettingStore } from '../stores/settingStore'
import { api, fetchJobs } from '../api'
import type { JobRecord, WorkspaceStats } from '../types'

const mockApi = vi.fn()
const mockFetchJobs = vi.fn()

vi.mock('../api', () => ({
  api: (...args: Parameters<typeof api>) => mockApi(...args),
  fetchJobs: (...args: Parameters<typeof fetchJobs>) => mockFetchJobs(...args),
}))

function renderPage(workspaceId = 'ws1') {
  return render(
    <MemoryRouter initialEntries={[`/workspaces/${workspaceId}`]}>
      <Routes>
        <Route
          path="/workspaces/:workspaceId/*"
          element={<WorkspaceMainPage />}
        />
      </Routes>
    </MemoryRouter>
  )
}

const baseStats: WorkspaceStats = {
  workspace_id: 'ws1',
  name: 'WS One',
  pipeline_key: 'question_content',
  pipeline_label: 'Question Content',
  job_stats: { pending: 1, running: 2, completed: 3, failed: 1 },
  agent_status: {
    total: 2,
    busy: 1,
    idle: 1,
    agents: [
      { id: 'agent-a', name: 'Agent A', busy: false },
      { id: 'agent-b', name: 'Agent B', busy: true },
    ],
  },
  latest_run: null,
}

describe('WorkspaceMainPage', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockFetchJobs.mockReset()
    mockFetchJobs.mockResolvedValue({ jobs: [] })
    mockApi.mockImplementation((path: string) => {
      if (path === '/api/workspaces/ws1/stats') {
        return Promise.resolve(baseStats)
      }
      return Promise.resolve({})
    })

    useJobStore.setState({
      jobs: [],
      isLoading: false,
      error: null,
      selectedIds: new Set(),
      expandedId: null,
      statusFilter: 'all',
      searchQuery: '',
    })
    useWorkspaceStore.setState({
      workspaces: [],
      currentWorkspace: null,
      workspaceStats: { ws1: baseStats },
      loading: false,
      error: null,
    })
    useUiStore.setState({
      agents: [
        {
          id: 'agent-a',
          name: 'Agent A',
          busy: false,
          task_count: 0,
          max_tasks: 1,
          current_video_id: null,
        },
      ],
      addDialogOpen: false,
      addContentType: 'knowledge',
      rerunDialogOpen: false,
      deleteDialogOpen: false,
      workerPaused: false,
      toast: null,
    })
    useSettingStore.setState({
      workspaceId: 'ws1',
      workspaceName: 'WS One',
      workspaceDescription: '',
      settings: {
        cmsUrl: '',
        cmsToken: '',
        entityType: 'question',
        intakeModes: [],
        labelOverrides: {},
        pipelineKey: '',
        agentIds: [],
        concurrencyLimit: 2,
        resources: {},
      },
      agentAssignments: null,
      originalWorkspaceName: 'WS One',
      originalWorkspaceDescription: '',
      originalSettings: {
        cmsUrl: '',
        cmsToken: '',
        entityType: 'question',
        intakeModes: [],
        labelOverrides: {},
        pipelineKey: '',
        agentIds: [],
        concurrencyLimit: 2,
        resources: {},
      },
      originalAgentAssignments: null,
      isDirty: false,
      testStatus: { state: 'idle' },
      isSaving: false,
      saveError: null,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders stat cards when jobs exist', async () => {
    useJobStore.setState({
      jobs: [
        {
          id: 'j1',
          workspace_id: 'ws1',
          pipeline_key: 'p1',
          source_id: 'Q100',
          title: 'Algebra',
          status: 'running',
        } as JobRecord,
      ],
    })

    await act(async () => {
      renderPage()
    })

    expect(screen.getByText('全部（7）')).toBeInTheDocument()
  })

  it('shows empty message when no jobs', async () => {
    useWorkspaceStore.setState({
      workspaceStats: {
        ws1: {
          ...baseStats,
          job_stats: { pending: 0, running: 0, completed: 0, failed: 0 },
        },
      },
    })
    mockApi.mockImplementation((path: string) => {
      if (path === '/api/workspaces/ws1/stats') {
        return Promise.resolve({
          ...baseStats,
          job_stats: { pending: 0, running: 0, completed: 0, failed: 0 },
        })
      }
      return Promise.resolve({})
    })

    await act(async () => {
      renderPage()
    })

    expect(
      screen.getByRole('heading', {
        name: '开始使用 Workspace',
        level: 2,
      })
    ).toBeInTheDocument()
    expect(screen.getByText('去配置')).toBeInTheDocument()
  })

  it('batch toolbar appears when items selected', async () => {
    useJobStore.setState({
      selectedIds: new Set(['j1', 'j2']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    expect(screen.getByText('已选择 2 项')).toBeInTheDocument()
    expect(screen.getByText('重跑')).toBeInTheDocument()
    expect(screen.getByText('删除')).toBeInTheDocument()
    expect(screen.getByText('全选')).toBeInTheDocument()
    expect(screen.getByText('仅失败')).toBeInTheDocument()
  })

  it('live polling fetches jobs repeatedly', async () => {
    vi.useFakeTimers()

    await act(async () => {
      renderPage()
    })

    expect(mockFetchJobs).toHaveBeenCalledWith('ws1')
    const callsBefore = mockFetchJobs.mock.calls.length

    await act(async () => {
      vi.advanceTimersByTime(5000)
    })

    expect(mockFetchJobs.mock.calls.length).toBeGreaterThan(callsBefore)
  })

  it('search input updates query after debounce', async () => {
    vi.useFakeTimers()

    await act(async () => {
      renderPage()
    })

    const search = document.querySelector('md-outlined-text-field') as
      | (HTMLElement & { value: string })
      | null
    expect(search).toBeInTheDocument()

    await act(async () => {
      if (search) {
        search.value = 'algebra'
        search.dispatchEvent(new Event('input', { bubbles: true }))
      }
    })

    await act(async () => {
      vi.advanceTimersByTime(250)
    })

    expect(useJobStore.getState().searchQuery).toBe('algebra')
  })
})
