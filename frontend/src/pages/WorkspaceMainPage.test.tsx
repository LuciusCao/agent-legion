import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent, waitFor } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import WorkspaceMainPage from './WorkspaceMainPage'
import { useJobStore } from '../stores/jobStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useUiStore } from '../stores/uiStore'
import { useSettingStore } from '../stores/settingStore'
import { api, fetchJobs, fetchWorkflowDefinition } from '../api'
import {
  batchRerunJobs,
  batchDeleteJobs,
  packageJobs,
  batchRunToJobs,
} from '../jobApi'
import { upgradeJobWorkflow } from '../jobWorkflowUpgradeApi'
import { EventSourceMock } from '../testing/eventSourceMock'
import type { WorkspaceStats } from '../workspaceTypes'
import { makeJob } from '../testing/fixtures'

const mockApi = vi.fn()
const mockFetchJobs = vi.fn()
const mockFetchWorkspaceStats = vi.fn()
const mockFetchWorkflowDefinition = vi.fn()
const mockBatchRerunJobs = vi.fn()
const mockBatchDeleteJobs = vi.fn()
const mockPackageJobs = vi.fn()
const mockBatchRunToJobs = vi.fn()
const mockUpgradeJobWorkflow = vi.fn()

vi.mock('../api', () => ({
  api: (...args: Parameters<typeof api>) => mockApi(...args),
  fetchJobs: (...args: Parameters<typeof fetchJobs>) => mockFetchJobs(...args),
  fetchWorkspaceStats: (
    ...args: Parameters<typeof import('../api').fetchWorkspaceStats>
  ) => mockFetchWorkspaceStats(...args),
  fetchWorkflowDefinition: (
    ...args: Parameters<typeof fetchWorkflowDefinition>
  ) => mockFetchWorkflowDefinition(...args),
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

vi.mock('../jobWorkflowUpgradeApi', () => ({
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

async function loadJobsViaSSE() {
  const source = EventSourceMock.instances[0]
  await act(async () => {
    source.onopen?.()
  })
  await waitFor(() => {
    expect(useJobStore.getState().jobs.length).toBeGreaterThan(0)
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
        executor_id: 'local-default',
        kind: 'local',
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
    mockFetchWorkspaceStats.mockReset()
    mockFetchWorkflowDefinition.mockReset()
    mockBatchRerunJobs.mockReset()
    mockBatchDeleteJobs.mockReset()
    mockPackageJobs.mockReset()
    mockBatchRunToJobs.mockReset()
    mockUpgradeJobWorkflow.mockReset()

    mockFetchJobs.mockResolvedValue({ jobs: [] })
    mockFetchWorkspaceStats.mockResolvedValue(baseStats)
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
      isLoading: false,
      error: null,
      selectedIds: new Set(),
      expandedId: null,
      filterConfig: {
        status: 'all',
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
      workspacePackageDialogOpen: false,
      workerPausedByWorkspace: {},
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
        workflowKey: '',
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
        workflowKey: '',
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

  it('fetches jobs once on mount via SSE without polling', async () => {
    await act(async () => {
      renderPage()
    })

    const source = EventSourceMock.instances[0]
    await act(async () => {
      source.onopen?.()
    })

    await waitFor(() => {
      expect(mockFetchJobs).toHaveBeenCalledTimes(1)
    })
    expect(mockFetchJobs).toHaveBeenCalledWith('ws1')
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

  it('submits batch rerun with selected node key', async () => {
    mockBatchRerunJobs.mockResolvedValueOnce({
      results: [{ job_id: 'j1', operation: 'rerun', status: 'succeeded' }],
    })
    mockFetchJobs.mockResolvedValue({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'failed',
          workflow_key: 'question_content',
        }),
      ],
    })
    useJobStore.setState({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'failed',
          workflow_key: 'question_content',
        }),
      ],
      selectedIds: new Set(['j1']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

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

    await act(async () => {
      screen.getByText('确认重跑').click()
    })

    expect(mockBatchRerunJobs).toHaveBeenCalledWith('ws1', 'extract', ['j1'], {
      fromFailedNode: false,
    })
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

    await loadJobsViaSSE()

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

    await loadJobsViaSSE()

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
          workflow_key: 'question_content',
        }),
      ],
    })
    useJobStore.setState({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'failed',
          workflow_key: 'question_content',
        }),
      ],
      selectedIds: new Set(['j1']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

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
          workflow_key: 'question_content',
        }),
        makeJob({
          id: 'j2',
          status: 'failed',
          source_id: 'Q2',
          workflow_key: 'question_content',
        }),
      ],
    })
    useJobStore.setState({
      jobs: [
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
      ],
      selectedIds: new Set(['j1', 'j2']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

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
          workflow_key: 'question_content',
        }),
      ],
    })
    useJobStore.setState({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'failed',
          workflow_key: 'question_content',
        }),
      ],
      selectedIds: new Set(['j1']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

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
      expect(mockFetchJobs).toHaveBeenCalledWith('ws1')
    })
  })

  it('submits batch workflow upgrade for outdated jobs', async () => {
    mockUpgradeJobWorkflow.mockResolvedValueOnce({
      job_id: 'j1',
      operation: 'upgrade_workflow',
      status: 'succeeded',
    })
    mockFetchJobs.mockResolvedValue({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'completed',
          is_workflow_outdated: true,
        }),
      ],
    })
    useJobStore.setState({
      jobs: [
        makeJob({
          id: 'j1',
          status: 'completed',
          is_workflow_outdated: true,
        }),
      ],
      selectedIds: new Set(['j1']),
      selectMode: true,
    })

    await act(async () => {
      renderPage()
    })

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

  it('renders workspace package history dialog when open', async () => {
    mockApi.mockImplementation((path: string) => {
      if (path === '/api/workspaces/ws1/stats') {
        return Promise.resolve(baseStats)
      }
      if (path === '/api/workspaces/ws1/packages') {
        return Promise.resolve({
          packages: [
            {
              id: 1,
              name: '批次 1',
              path: '/data/packages/ws1.zip',
              video_count: 3,
              size_bytes: 1024,
              locked: 0,
              created_at: new Date().toISOString(),
            },
          ],
        })
      }
      return Promise.resolve({})
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
