import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent, waitFor } from '@testing-library/react'
import { Link, Routes, Route } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import WorkspaceMainPage from './WorkspaceMainPage'
import { useJobStore } from '../stores/jobStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useAgentsStore } from '../stores/agentsStore'
import { useUiStore } from '../stores/uiStore'
import { useSettingStore } from '../stores/settingStore'
import {
  api,
  fetchJobs,
  fetchWorkflowDefinition,
  fetchWorkspacePackages,
} from '../api'
import {
  batchRerunJobs,
  batchDeleteJobs,
  packageJobs,
  batchRunToJobs,
} from '../api/jobApi'
import { upgradeJobWorkflow } from '../api/jobWorkflowUpgradeApi'
import { EventSourceMock } from '../testing/eventSourceMock'
import type { WorkspaceStats } from '../types/workspaceTypes'
import type { JobSummary } from '../types'
import { makeJob } from '../testing/fixtures'
import { makeAgentStatus } from '../testing/workspaceFixtures'

const mockApi = vi.fn()
const mockFetchJobs = vi.fn()
const mockFetchJobsSnapshot = vi.fn()
const mockFetchJobFacets = vi.fn()
const mockFetchWorkspaceStats = vi.fn()
const mockFetchWorkspacePackages = vi.fn()
const mockFetchWorkflowDefinition = vi.fn()
const mockBatchRerunJobs = vi.fn()
const mockBatchDeleteJobs = vi.fn()
const mockPackageJobs = vi.fn()
const mockBatchRunToJobs = vi.fn()
const mockUpgradeJobWorkflow = vi.fn()

vi.mock('../api', () => ({
  api: (...args: Parameters<typeof api>) => mockApi(...args),
  fetchJobs: (...args: Parameters<typeof fetchJobs>) => mockFetchJobs(...args),
  fetchJobsSnapshot: (
    ...args: Parameters<typeof import('../api').fetchJobsSnapshot>
  ) => mockFetchJobsSnapshot(...args),
  fetchJobFacets: (
    ...args: Parameters<typeof import('../api').fetchJobFacets>
  ) => mockFetchJobFacets(...args),
  fetchWorkspaceStats: (
    ...args: Parameters<typeof import('../api').fetchWorkspaceStats>
  ) => mockFetchWorkspaceStats(...args),
  fetchWorkspacePackages: (
    ...args: Parameters<typeof fetchWorkspacePackages>
  ) => mockFetchWorkspacePackages(...args),
  fetchWorkflowDefinition: (
    ...args: Parameters<typeof fetchWorkflowDefinition>
  ) => mockFetchWorkflowDefinition(...args),
}))

vi.mock('../api/jobApi', () => ({
  batchRerunJobs: (...args: Parameters<typeof batchRerunJobs>) =>
    mockBatchRerunJobs(...args),
  batchDeleteJobs: (...args: Parameters<typeof batchDeleteJobs>) =>
    mockBatchDeleteJobs(...args),
  packageJobs: (...args: Parameters<typeof packageJobs>) =>
    mockPackageJobs(...args),
  batchRunToJobs: (...args: Parameters<typeof batchRunToJobs>) =>
    mockBatchRunToJobs(...args),
}))

vi.mock('../api/jobWorkflowUpgradeApi', () => ({
  upgradeJobWorkflow: (...args: Parameters<typeof upgradeJobWorkflow>) =>
    mockUpgradeJobWorkflow(...args),
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

function renderPageWithWorkspaceSwitcher() {
  return render(
    <MemoryRouter initialEntries={['/workspaces/ws1']}>
      <Link to="/workspaces/ws2">切换 Workspace</Link>
      <Routes>
        <Route
          path="/workspaces/:workspaceId/*"
          element={<WorkspaceMainPage />}
        />
      </Routes>
    </MemoryRouter>
  )
}

async function loadJobsViaSSE() {
  const source = EventSourceMock.instances[0]
  await act(async () => {
    source.onopen?.()
  })
  await waitFor(() => {
    expect(useJobStore.getState().isLoading).toBe(false)
  })
}

function seedJobs(jobs: JobSummary[]) {
  act(() => {
    useJobStore.setState({
      jobs,
      jobsById: Object.fromEntries(jobs.map((job) => [job.id, job])),
      jobIds: jobs.map((job) => job.id),
    })
  })
}

const baseStats: WorkspaceStats = {
  workspace_id: 'ws1',
  name: 'WS One',
  workflow_key: 'question_content',
  workflow_label: 'Question Content',
  job_stats: { pending: 1, running: 2, completed: 3, failed: 1 },
  executor_status: {
    executors: [
      {
        executor_id: 'code-default',
        kind: 'code',
        global_capacity: 16,
        workspace_limit: 4,
        running: 1,
        available: 3,
        binding_count: 1,
      },
    ],
  },
  latest_run: null,
}

const workflowDefinition = {
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
  const originalEventSource = globalThis.EventSource

  beforeEach(() => {
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource

    mockApi.mockReset()
    mockFetchJobs.mockReset()
    mockFetchJobsSnapshot.mockReset()
    mockFetchJobFacets.mockReset()
    mockFetchWorkspaceStats.mockReset()
    mockFetchWorkspacePackages.mockReset()
    mockFetchWorkflowDefinition.mockReset()
    mockBatchRerunJobs.mockReset()
    mockBatchDeleteJobs.mockReset()
    mockPackageJobs.mockReset()
    mockBatchRunToJobs.mockReset()
    mockUpgradeJobWorkflow.mockReset()

    mockFetchJobs.mockResolvedValue({ jobs: [] })
    mockFetchJobsSnapshot.mockImplementation(() =>
      Promise.resolve({
        workspace_id: 'ws1',
        revision: 1,
        stats: baseStats.job_stats,
        jobs: useJobStore.getState().jobs,
        next_cursor: null,
      })
    )
    mockFetchJobFacets.mockResolvedValue({
      workspace_id: 'ws1',
      total: 0,
      status_counts: {},
      version_counts: {},
      node_counts: {},
    })
    mockFetchWorkspaceStats.mockResolvedValue(baseStats)
    mockFetchWorkspacePackages.mockResolvedValue({ packages: [] })
    mockFetchWorkflowDefinition.mockResolvedValue({
      workflow: workflowDefinition,
    })
    mockApi.mockImplementation((path: string) => {
      if (path === '/api/workspaces/ws1/stats') {
        return Promise.resolve(baseStats)
      }
      return Promise.resolve({})
    })

    useJobStore.setState({
      jobs: [],
      jobsById: {},
      jobIds: [],
      revision: 0,
      jobsWorkspaceId: 'ws1',
      isLoading: false,
      error: null,
      selectedIds: new Set(),
      selectionMode: 'explicit',
      selectionFilter: null,
      excludedIds: new Set(),
      selectionCount: null,
      expandedId: null,
      filterConfig: {
        status: null,
        search: '',
        workflowVersion: null,
        activeNodeKey: null,
      },
      batchRerunLoading: false,
      batchPackageLoading: false,
      batchDeleteLoading: false,
      batchRunToLoading: false,
      batchUpgradeWorkflowLoading: false,
    })
    useWorkspaceStore.setState({
      workspaces: [],
      currentWorkspace: null,
      workspaceStats: { ws1: baseStats },
      loading: false,
      error: null,
    })
    useAgentsStore.setState({
      agents: [
        makeAgentStatus({
          id: 'agent-a',
          name: 'Agent A',
          workspace_id: 'ws1',
        }),
      ],
      workerPausedByWorkspace: {},
    })
    useUiStore.setState({
      addDialogOpen: false,
      addContentType: 'knowledge',
      workspacePackageDialogOpen: false,
      tokenUsageDialogOpen: false,
      toast: null,
    })
    useSettingStore.setState({
      workspaceId: 'ws1',
      workspaceName: 'WS One',
      workspaceDescription: '',
      settings: {
        entityType: 'question',
        intakeModes: [],
        labelOverrides: {},
        workflowKey: '',
      },
      originalWorkspaceName: 'WS One',
      originalWorkspaceDescription: '',
      originalSettings: {
        entityType: 'question',
        intakeModes: [],
        labelOverrides: {},
        workflowKey: '',
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
      workflowDefinition: null,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    globalThis.EventSource = originalEventSource
  })

  it('renders filter bar when jobs exist', async () => {
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

    expect(screen.getByLabelText('状态')).toBeInTheDocument()
    expect(
      screen.getByPlaceholderText('搜索 ID / 标题 / 批次')
    ).toBeInTheDocument()
  })

  it('shows empty message when no jobs', async () => {
    const emptyStats = {
      ...baseStats,
      job_stats: { pending: 0, running: 0, completed: 0, failed: 0 },
    }
    useWorkspaceStore.setState({
      workspaceStats: {
        ws1: emptyStats,
      },
    })
    mockFetchWorkspaceStats.mockResolvedValue(emptyStats)
    mockApi.mockImplementation((path: string) => {
      if (path === '/api/workspaces/ws1/stats') {
        return Promise.resolve(emptyStats)
      }
      return Promise.resolve({})
    })

    await act(async () => {
      renderPage()
    })

    const source = EventSourceMock.instances[0]
    await act(async () => {
      source.onopen?.()
    })

    await waitFor(() => {
      expect(useJobStore.getState().isLoading).toBe(false)
    })

    expect(
      screen.getByRole('heading', {
        name: '开始使用 Workspace',
        level: 2,
      })
    ).toBeInTheDocument()
    expect(screen.getByText('去配置')).toBeInTheDocument()
  })

  it('shows skeleton while loading jobs', async () => {
    useJobStore.setState({ isLoading: true, jobs: [] })

    await act(async () => {
      renderPage()
    })

    expect(screen.getByTestId('job-list-skeleton')).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: '开始使用 Workspace' })
    ).not.toBeInTheDocument()
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
    expect(screen.getByText('仅未打包')).toBeInTheDocument()
    expect(screen.getByText('仅失败')).toBeInTheDocument()
  })

  it('loads jobs snapshot once on mount via SSE without polling', async () => {
    await act(async () => {
      renderPage()
    })

    const source = EventSourceMock.instances[0]
    await act(async () => {
      source.onopen?.()
    })

    await waitFor(() => {
      expect(mockFetchJobsSnapshot).toHaveBeenCalledTimes(1)
    })
    expect(mockFetchJobsSnapshot).toHaveBeenCalledWith('ws1', 500, undefined, {
      status: null,
      search: null,
      workflow_version: null,
      workflow_version_none: false,
      active_node_key: null,
    })
  })

  it('search input updates query after debounce', async () => {
    vi.useFakeTimers()

    await act(async () => {
      renderPage()
    })

    const search = screen.getByPlaceholderText(
      '搜索 ID / 标题 / 批次'
    ) as HTMLInputElement
    expect(search).toBeInTheDocument()

    await act(async () => {
      fireEvent.change(search, { target: { value: 'algebra' } })
    })

    await act(async () => {
      vi.advanceTimersByTime(250)
    })

    expect(useJobStore.getState().filterConfig.search).toBe('algebra')
  })

  it('clears job filters when switching workspaces', async () => {
    useJobStore.setState({
      jobsWorkspaceId: 'ws1',
      filterConfig: {
        status: 'failed',
        search: 'algebra',
        workflowVersion: null,
        activeNodeKey: null,
      },
    })

    await act(async () => {
      renderPageWithWorkspaceSwitcher()
    })
    expect(screen.getByPlaceholderText('搜索 ID / 标题 / 批次')).toHaveValue(
      'algebra'
    )

    await act(async () => {
      screen.getByRole('link', { name: '切换 Workspace' }).click()
    })

    expect(useJobStore.getState().filterConfig).toEqual({
      status: null,
      search: '',
      workflowVersion: null,
      activeNodeKey: null,
    })
    expect(screen.getByPlaceholderText('搜索 ID / 标题 / 批次')).toHaveValue('')
  })

  it('submits batch rerun with selected node key', async () => {
    mockBatchRerunJobs.mockResolvedValueOnce({
      results: [{ job_id: 'j1', operation: 'rerun', status: 'succeeded' }],
    })
    const seed = [
      makeJob({
        id: 'j1',
        status: 'failed',
        workflow_key: 'question_content',
        node_summaries: [
          {
            node_key: 'extract',
            label: '提取',
            status: 'failed',
            error_message: 'boom',
          },
        ],
      }),
      makeJob({
        id: 'j2',
        status: 'queued',
        workflow_key: 'question_content',
        node_summaries: [
          {
            node_key: 'extract',
            label: '提取',
            status: 'stale',
            error_message: '',
          },
        ],
      }),
    ]
    mockFetchJobs.mockResolvedValue({ jobs: seed })
    useJobStore.setState({
      jobs: seed,
      selectedIds: new Set(['j1', 'j2']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    seedJobs(seed)
    await loadJobsViaSSE()

    await waitFor(() =>
      expect(mockFetchWorkflowDefinition).toHaveBeenCalledWith(
        'question_content'
      )
    )

    await act(async () => {
      screen.getByText('重跑').click()
    })

    expect(screen.getByText('选择重跑节点')).toBeInTheDocument()
    expect(
      screen.getByText('已选择 2 个任务，可重跑 1 个，1 个尚未执行到所选节点')
    ).toBeInTheDocument()

    await act(async () => {
      screen.getByText('重跑 1 个任务').click()
    })

    expect(mockBatchRerunJobs).toHaveBeenCalledWith(
      'ws1',
      'extract',
      { jobIds: ['j1'] },
      {
        fromFailedNode: false,
      }
    )
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
    const seed = [makeJob({ id: 'j1', status: 'completed' })]
    mockFetchJobs.mockResolvedValue({ jobs: seed })
    useJobStore.setState({
      jobs: seed,
      selectedIds: new Set(['j1']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    seedJobs(seed)
    await loadJobsViaSSE()

    await act(async () => {
      screen.getByText('打包').click()
    })

    expect(mockPackageJobs).toHaveBeenCalledWith('ws1', { jobIds: ['j1'] })
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
    const seed = [makeJob({ id: 'j1', status: 'failed' })]
    mockFetchJobs.mockResolvedValue({ jobs: seed })
    useJobStore.setState({
      jobs: seed,
      selectedIds: new Set(['j1']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    seedJobs(seed)
    await loadJobsViaSSE()

    await act(async () => {
      screen.getByText('删除').click()
    })

    expect(screen.getByText('确认删除')).toBeInTheDocument()

    const deleteButtons = screen.getAllByText('删除')
    await act(async () => {
      fireEvent.click(deleteButtons[deleteButtons.length - 1])
    })

    expect(mockBatchDeleteJobs).toHaveBeenCalledWith('ws1', { jobIds: ['j1'] })
  })

  it('submits batch run-to with selected target and optional start node', async () => {
    mockBatchRunToJobs.mockResolvedValueOnce({
      results: [{ job_id: 'j1', operation: 'run_to', status: 'succeeded' }],
    })
    const seed = [
      makeJob({
        id: 'j1',
        status: 'failed',
        workflow_key: 'question_content',
      }),
    ]
    mockFetchJobs.mockResolvedValue({ jobs: seed })
    useJobStore.setState({
      jobs: seed,
      selectedIds: new Set(['j1']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    seedJobs(seed)
    await loadJobsViaSSE()

    await waitFor(() =>
      expect(mockFetchWorkflowDefinition).toHaveBeenCalledWith(
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
      { jobIds: ['j1'] },
      undefined
    )
  })

  it('clears the selection after batch run-to partial results', async () => {
    mockBatchRunToJobs.mockResolvedValueOnce({
      results: [
        { job_id: 'j1', operation: 'run_to', status: 'succeeded' },
        { job_id: 'j2', operation: 'run_to', status: 'skipped' },
      ],
    })
    const seed = [
      makeJob({
        id: 'j1',
        status: 'failed',
        workflow_key: 'question_content',
      }),
      makeJob({
        id: 'j2',
        status: 'failed',
        source_id: 'Q2',
        workflow_key: 'question_content',
      }),
    ]
    mockFetchJobs.mockResolvedValue({ jobs: seed })
    useJobStore.setState({
      jobs: seed,
      selectedIds: new Set(['j1', 'j2']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    seedJobs(seed)
    await loadJobsViaSSE()

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
      expect(useJobStore.getState().selectedIds.size).toBe(0)
    })
  })

  it('refreshes the first page immediately after batch run-to', async () => {
    mockBatchRunToJobs.mockResolvedValueOnce({
      results: [{ job_id: 'j1', operation: 'run_to', status: 'succeeded' }],
    })
    const seed = [
      makeJob({
        id: 'j1',
        status: 'failed',
        workflow_key: 'question_content',
      }),
    ]
    mockFetchJobs.mockResolvedValue({ jobs: seed })
    useJobStore.setState({
      jobs: seed,
      selectedIds: new Set(['j1']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    seedJobs(seed)
    await loadJobsViaSSE()

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
      expect(mockFetchJobsSnapshot).toHaveBeenCalledTimes(2)
    })
    expect(mockFetchJobs).not.toHaveBeenCalled()
  })

  it('submits batch workflow upgrade for outdated jobs', async () => {
    mockUpgradeJobWorkflow.mockResolvedValueOnce({
      job_id: 'j1',
      operation: 'upgrade_workflow',
      status: 'succeeded',
    })
    const seed = [
      makeJob({
        id: 'j1',
        status: 'completed',
        is_workflow_outdated: true,
      }),
    ]
    mockFetchJobs.mockResolvedValue({ jobs: seed })
    useJobStore.setState({
      jobs: seed,
      selectedIds: new Set(['j1']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    seedJobs(seed)
    await loadJobsViaSSE()

    await act(async () => {
      screen.getByText('升级 workflow').click()
    })

    expect(screen.getByText('确认升级 workflow')).toBeInTheDocument()

    await act(async () => {
      screen.getByText('升级 1 个任务').click()
    })

    await waitFor(() => {
      expect(mockUpgradeJobWorkflow).toHaveBeenCalledWith('j1')
    })
  })

  it('selects all matching jobs and deletes them via a filter payload', async () => {
    mockBatchDeleteJobs.mockResolvedValueOnce({
      results: [{ job_id: 'j1', operation: 'delete', status: 'succeeded' }],
    })
    const seed = [
      makeJob({ id: 'j1', status: 'failed' }),
      makeJob({ id: 'j2', status: 'failed', source_id: 'Q2' }),
    ]
    useJobStore.setState({
      jobs: seed,
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

    seedJobs(seed)
    await loadJobsViaSSE()
    act(() => {
      useJobStore.setState({ totalJobs: 25 })
    })

    await act(async () => {
      screen.getByText('全选').click()
    })

    expect(useJobStore.getState().selectionMode).toBe('allMatching')
    expect(screen.getByText(/已选择 25 项/)).toBeInTheDocument()
    expect(screen.getByText('运行到')).toHaveAttribute('disabled')
    expect(screen.getByText('升级 workflow')).toHaveAttribute('disabled')

    await act(async () => {
      screen.getByText('删除').click()
    })
    expect(
      screen.getByText(/将对符合筛选条件的 25 个 job 执行删除/)
    ).toBeInTheDocument()

    const deleteButtons = screen.getAllByText('删除')
    await act(async () => {
      fireEvent.click(deleteButtons[deleteButtons.length - 1])
    })

    expect(mockBatchDeleteJobs).toHaveBeenCalledWith('ws1', {
      filter: {
        status: null,
        search: null,
        workflow_version: null,
        workflow_version_none: false,
        active_node_key: null,
      },
      excludeIds: [],
    })
    await waitFor(() => {
      expect(useJobStore.getState().selectionMode).toBe('explicit')
    })
  })

  it('renders workspace package history dialog when open', async () => {
    mockFetchWorkspacePackages.mockResolvedValue({
      packages: [
        {
          id: 1,
          workspace_id: 'ws1',
          name: '批次 1',
          path: '/data/packages/ws1.zip',
          video_count: 3,
          size_bytes: 1024,
          locked: 0,
          created_at: new Date().toISOString(),
        },
      ],
    })
    useUiStore.setState({ workspacePackageDialogOpen: true })

    await act(async () => {
      renderPage()
    })

    await waitFor(() => {
      expect(screen.getByText('包历史')).toBeInTheDocument()
    })
    expect(screen.getByText('批次 1')).toBeInTheDocument()
  })
})
