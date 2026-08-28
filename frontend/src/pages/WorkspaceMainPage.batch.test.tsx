import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, waitFor, fireEvent } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import WorkspaceMainPage from './WorkspaceMainPage'
import { useJobStore } from '../stores/jobStore'
import { useAgentsStore } from '../stores/agentsStore'
import { useUiStore } from '../stores/uiStore'
import { useSettingStore } from '../stores/settingStore'
import {
  api,
  fetchActiveWorkflowRevision,
  fetchWorkspacePackages,
  fetchWorkspaceStats,
} from '../api'
import {
  batchRerunJobs,
  batchDeleteJobs,
  packageJobs,
  batchRunToJobs,
} from '../api/jobApi'
import { upgradeJobWorkflow } from '../api/jobWorkflowUpgradeApi'
import { batchUpgradeJobsWorkflow } from '../api/jobBatchUpgradeWorkflowApi'
import { EventSourceMock } from '../testing/eventSourceMock'
import type { WorkspaceStats } from '../types/workspaceTypes'
import { makeJob } from '../testing/fixtures'
import { makeAgentStatus } from '../testing/workspaceFixtures'

// WorkspaceMainPage 批量操作面的集成测试：批量重跑/打包/删除/运行到/
// workflow 升级（含 allMatching 筛选载荷）。渲染、筛选与空态冒烟留在
// WorkspaceMainPage.test.tsx，onboarding 引导见
// WorkspaceMainPage.onboarding.test.tsx。mock/setup 骨架与主文件一致
// （vitest 模块 mock 按文件隔离，无法跨测试文件共享）。

const mockApi = vi.fn()
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
const mockBatchUpgradeJobsWorkflow = vi.fn()

vi.mock('../api', () => ({
  api: (...args: Parameters<typeof api>) => mockApi(...args),
  fetchJobsSnapshot: (
    ...args: Parameters<typeof import('../api').fetchJobsSnapshot>
  ) => mockFetchJobsSnapshot(...args),
  fetchJobFacets: (
    ...args: Parameters<typeof import('../api').fetchJobFacets>
  ) => mockFetchJobFacets(...args),
  fetchWorkspaceStats: (...args: Parameters<typeof fetchWorkspaceStats>) =>
    mockFetchWorkspaceStats(...args),
  fetchWorkspacePackages: (
    ...args: Parameters<typeof fetchWorkspacePackages>
  ) => mockFetchWorkspacePackages(...args),
  fetchActiveWorkflowRevision: (
    ...args: Parameters<typeof fetchActiveWorkflowRevision>
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

vi.mock('../api/jobBatchUpgradeWorkflowApi', () => ({
  batchUpgradeJobsWorkflow: (
    ...args: Parameters<typeof batchUpgradeJobsWorkflow>
  ) => mockBatchUpgradeJobsWorkflow(...args),
}))

const mockGetWorkspaceExecutionConfiguration = vi.fn()

vi.mock('../api/agentCatalogApi', () => ({
  getWorkspaceExecutionConfiguration: (...args: unknown[]) =>
    mockGetWorkspaceExecutionConfiguration(...args),
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

async function loadJobsViaSSE() {
  const source = EventSourceMock.instances[0]
  await act(async () => {
    source.onopen?.()
  })
  await waitFor(() => {
    expect(useJobStore.getState().isLoading).toBe(false)
  })
}

function seedJobs(jobs: ReturnType<typeof makeJob>[]) {
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
  code_pool: { capacity: 16, running: 1, available: 15 },
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

describe('WorkspaceMainPage batch operations', () => {
  const originalEventSource = globalThis.EventSource

  beforeEach(() => {
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource

    mockApi.mockReset()
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
    mockBatchUpgradeJobsWorkflow.mockReset()
    mockGetWorkspaceExecutionConfiguration.mockReset()
    mockGetWorkspaceExecutionConfiguration.mockResolvedValue({
      node_limits: [],
      migration_warnings: [],
      agent_capacity: null,
    })

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
        paused: null,
      },
      batchRerunLoading: false,
      batchPackageLoading: false,
      batchDeleteLoading: false,
      batchRunToLoading: false,
      batchUpgradeWorkflowLoading: false,
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
      isSaving: false,
      saveError: null,
      executionConfiguration: {
        node_limits: [],
        migration_warnings: [],
        agent_capacity: null,
      },
      originalExecutionConfiguration: null,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    globalThis.EventSource = originalEventSource
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
      expect(mockFetchWorkflowDefinition).toHaveBeenCalledWith('ws1')
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
      expect(mockFetchWorkflowDefinition).toHaveBeenCalledWith('ws1')
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
    expect(screen.getByText('升级 workflow')).not.toHaveAttribute('disabled')

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
        paused: null,
      },
      excludeIds: [],
    })
    await waitFor(() => {
      expect(useJobStore.getState().selectionMode).toBe('explicit')
    })
  })

  it('upgrades all matching jobs via the batch endpoint after confirmation', async () => {
    mockBatchUpgradeJobsWorkflow.mockResolvedValueOnce({
      results: [
        { job_id: 'j1', operation: 'upgrade_workflow', status: 'succeeded' },
      ],
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
    expect(screen.getByText('升级 workflow')).not.toHaveAttribute('disabled')

    await act(async () => {
      screen.getByText('升级 workflow').click()
    })
    expect(
      screen.getByText(/将对符合筛选条件的 25 个 job 执行 workflow/)
    ).toBeInTheDocument()

    await act(async () => {
      screen.getByText('确认升级').click()
    })

    expect(mockBatchUpgradeJobsWorkflow).toHaveBeenCalledWith('ws1', {
      filter: {
        status: null,
        search: null,
        workflow_version: null,
        workflow_version_none: false,
        active_node_key: null,
        paused: null,
      },
      excludeIds: [],
    })
    await waitFor(() => {
      expect(useJobStore.getState().selectionMode).toBe('explicit')
    })
  })
})
