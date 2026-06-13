import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import WorkspaceMainPage from './WorkspaceMainPage'
import { useJobStore } from '../stores/jobStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useUiStore } from '../stores/uiStore'
import { useSettingStore } from '../stores/settingStore'
import { api, fetchJobs, fetchPipelineDefinition } from '../api'
import {
  batchRerunJobs,
  batchDeleteJobs,
  packageJobs,
  batchRunToJobs,
} from '../jobApi'
import type { WorkspaceStats } from '../workspaceTypes'
import { makeJob } from '../testing/fixtures'

const mockApi = vi.fn()
const mockFetchJobs = vi.fn()
const mockFetchPipelineDefinition = vi.fn()
const mockBatchRerunJobs = vi.fn()
const mockBatchDeleteJobs = vi.fn()
const mockPackageJobs = vi.fn()
const mockBatchRunToJobs = vi.fn()

vi.mock('../api', () => ({
  api: (...args: Parameters<typeof api>) => mockApi(...args),
  fetchJobs: (...args: Parameters<typeof fetchJobs>) => mockFetchJobs(...args),
  fetchPipelineDefinition: (
    ...args: Parameters<typeof fetchPipelineDefinition>
  ) => mockFetchPipelineDefinition(...args),
}))

vi.mock('../jobApi', () => ({
  batchRerunJobs: (...args: Parameters<typeof batchRerunJobs>) =>
    mockBatchRerunJobs(...args),
  batchDeleteJobs: (...args: Parameters<typeof batchDeleteJobs>) =>
    mockBatchDeleteJobs(...args),
  packageJobs: (...args: Parameters<typeof packageJobs>) =>
    mockPackageJobs(...args),
  batchRunToJobs: (...args: Parameters<typeof batchRunToJobs>) =>
    mockBatchRunToJobs(...args),
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

const pipelineDefinition = {
  key: 'question_content',
  label: 'Question Content',
  nodes: [
    {
      key: 'extract',
      label: '提取',
      after: [],
      capability: 'extract',
      inputs: [],
      outputs: [],
    },
    {
      key: 'generate',
      label: '生成',
      after: ['extract'],
      capability: 'generate',
      inputs: [],
      outputs: [],
    },
    {
      key: 'review',
      label: '审核',
      after: ['generate'],
      capability: 'review',
      inputs: [],
      outputs: [],
    },
  ],
}

describe('WorkspaceMainPage', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockFetchJobs.mockReset()
    mockFetchPipelineDefinition.mockReset()
    mockBatchRerunJobs.mockReset()
    mockBatchDeleteJobs.mockReset()
    mockPackageJobs.mockReset()
    mockBatchRunToJobs.mockReset()

    mockFetchJobs.mockResolvedValue({ jobs: [] })
    mockFetchPipelineDefinition.mockResolvedValue({
      pipeline: pipelineDefinition,
    })
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
      batchRerunLoading: false,
      batchPackageLoading: false,
      batchDeleteLoading: false,
      batchRunToLoading: false,
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
          workspace_id: 'ws1',
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
        resources: {},
      },
      originalWorkspaceName: 'WS One',
      originalWorkspaceDescription: '',
      originalSettings: {
        cmsUrl: '',
        cmsToken: '',
        entityType: 'question',
        intakeModes: [],
        labelOverrides: {},
        pipelineKey: '',
        resources: {},
      },
      isDirty: false,
      testStatus: { state: 'idle' },
      isSaving: false,
      saveError: null,
      executorCatalog: [],
      executorConfiguration: {
        allocations: [],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
      },
      originalExecutorConfiguration: null,
      pendingAllocationRemoval: null,
      pipelineDefinition: null,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders stat cards when jobs exist', async () => {
    useJobStore.setState({
      jobs: [
        makeJob({
          id: 'j1',
          source_id: 'Q100',
          title: 'Algebra',
          status: 'running',
        }),
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
      jobs: [
        makeJob({ id: 'j1', status: 'failed' }),
        makeJob({ id: 'j2', status: 'completed', source_id: 'Q2' }),
      ],
      selectedIds: new Set(['j1', 'j2']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    expect(screen.getByText(/已选择 2 项/)).toBeInTheDocument()
    expect(screen.getByText('重跑')).toBeInTheDocument()
    expect(screen.getByText('打包')).toBeInTheDocument()
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

  it('submits batch rerun with selected node key', async () => {
    mockBatchRerunJobs.mockResolvedValueOnce({
      results: [{ job_id: 'j1', operation: 'rerun', status: 'succeeded' }],
    })
    mockFetchJobs.mockResolvedValue({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'failed',
          pipeline_key: 'question_content',
        }),
      ],
    })
    useJobStore.setState({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'failed',
          pipeline_key: 'question_content',
        }),
      ],
      selectedIds: new Set(['j1']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    await waitFor(() =>
      expect(mockFetchPipelineDefinition).toHaveBeenCalledWith(
        'question_content'
      )
    )

    await act(async () => {
      screen.getByText('重跑').click()
    })

    expect(screen.getByText('选择重跑节点')).toBeInTheDocument()

    await act(async () => {
      screen.getByText('确认重跑').click()
    })

    expect(mockBatchRerunJobs).toHaveBeenCalledWith('ws1', 'extract', ['j1'])
  })

  it('opens package download URL after batch package', async () => {
    mockPackageJobs.mockResolvedValueOnce({
      download_url: '/api/workspaces/ws1/packages/pkg.zip',
      package_filename: 'pkg.zip',
      succeeded_count: 1,
      failed_count: 0,
      results: [{ job_id: 'j1', status: 'succeeded' }],
    })
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    mockFetchJobs.mockResolvedValue({
      jobs: [makeJob({ id: 'j1', status: 'completed' })],
    })
    useJobStore.setState({
      jobs: [makeJob({ id: 'j1', status: 'completed' })],
      selectedIds: new Set(['j1']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    await act(async () => {
      screen.getByText('打包').click()
    })

    expect(mockPackageJobs).toHaveBeenCalledWith('ws1', ['j1'])
    expect(openSpy).toHaveBeenCalledWith(
      '/api/workspaces/ws1/packages/pkg.zip',
      '_blank'
    )
    openSpy.mockRestore()
  })

  it('confirms and submits batch delete', async () => {
    mockBatchDeleteJobs.mockResolvedValueOnce({
      results: [{ job_id: 'j1', operation: 'delete', status: 'succeeded' }],
    })
    mockFetchJobs.mockResolvedValue({
      jobs: [makeJob({ id: 'j1', status: 'failed' })],
    })
    useJobStore.setState({
      jobs: [makeJob({ id: 'j1', status: 'failed' })],
      selectedIds: new Set(['j1']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    await act(async () => {
      screen.getByText('删除').click()
    })

    expect(screen.getByText('确认删除')).toBeInTheDocument()

    const deleteButtons = screen.getAllByText('删除')
    await act(async () => {
      fireEvent.click(deleteButtons[deleteButtons.length - 1])
    })

    expect(mockBatchDeleteJobs).toHaveBeenCalledWith('ws1', ['j1'])
  })

  it('submits batch run-to with selected target and optional start node', async () => {
    mockBatchRunToJobs.mockResolvedValueOnce({
      results: [{ job_id: 'j1', operation: 'run_to', status: 'succeeded' }],
    })
    mockFetchJobs.mockResolvedValue({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'failed',
          pipeline_key: 'question_content',
        }),
      ],
    })
    useJobStore.setState({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'failed',
          pipeline_key: 'question_content',
        }),
      ],
      selectedIds: new Set(['j1']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    await waitFor(() =>
      expect(mockFetchPipelineDefinition).toHaveBeenCalledWith(
        'question_content'
      )
    )

    await act(async () => {
      screen.getByText('运行到').click()
    })

    expect(screen.getByText('选择运行到节点')).toBeInTheDocument()

    await act(async () => {
      const chip = document.querySelector(
        '[data-testid="target-chip-review"]'
      ) as HTMLElement | null
      if (chip) fireEvent.click(chip)
    })

    await act(async () => {
      screen.getByText('确认运行到').click()
    })

    expect(mockBatchRunToJobs).toHaveBeenCalledWith(
      'ws1',
      'review',
      ['j1'],
      undefined
    )
  })

  it('preserves skipped selections after batch run-to partial results', async () => {
    mockBatchRunToJobs.mockResolvedValueOnce({
      results: [
        { job_id: 'j1', operation: 'run_to', status: 'succeeded' },
        { job_id: 'j2', operation: 'run_to', status: 'skipped' },
      ],
    })
    mockFetchJobs.mockResolvedValue({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'failed',
          pipeline_key: 'question_content',
        }),
        makeJob({
          id: 'j2',
          status: 'failed',
          source_id: 'Q2',
          pipeline_key: 'question_content',
        }),
      ],
    })
    useJobStore.setState({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'failed',
          pipeline_key: 'question_content',
        }),
        makeJob({
          id: 'j2',
          status: 'failed',
          source_id: 'Q2',
          pipeline_key: 'question_content',
        }),
      ],
      selectedIds: new Set(['j1', 'j2']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    await act(async () => {
      screen.getByText('运行到').click()
    })

    await act(async () => {
      const chip = document.querySelector(
        '[data-testid="target-chip-generate"]'
      ) as HTMLElement | null
      if (chip) fireEvent.click(chip)
    })

    await act(async () => {
      screen.getByText('确认运行到').click()
    })

    await waitFor(() => {
      expect(useJobStore.getState().selectedIds.has('j2')).toBe(true)
    })
    expect(useJobStore.getState().selectedIds.has('j1')).toBe(false)
  })

  it('refreshes jobs immediately after batch run-to', async () => {
    mockBatchRunToJobs.mockResolvedValueOnce({
      results: [{ job_id: 'j1', operation: 'run_to', status: 'succeeded' }],
    })
    mockFetchJobs.mockResolvedValue({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'failed',
          pipeline_key: 'question_content',
        }),
      ],
    })
    useJobStore.setState({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'failed',
          pipeline_key: 'question_content',
        }),
      ],
      selectedIds: new Set(['j1']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    await act(async () => {
      screen.getByText('运行到').click()
    })

    await act(async () => {
      const chip = document.querySelector(
        '[data-testid="target-chip-generate"]'
      ) as HTMLElement | null
      if (chip) fireEvent.click(chip)
    })

    await act(async () => {
      screen.getByText('确认运行到').click()
    })

    await waitFor(() => {
      expect(mockFetchJobs).toHaveBeenCalledWith('ws1')
    })
  })
})
