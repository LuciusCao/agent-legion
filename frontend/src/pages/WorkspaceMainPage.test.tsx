import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent, waitFor } from '@testing-library/react'
import { Link, Routes, Route } from 'react-router-dom'
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
} from '../api'
import { EventSourceMock } from '../testing/eventSourceMock'
import type { WorkspaceStats } from '../types/workspaceTypes'
import { makeJob } from '../testing/fixtures'
import { makeAgentStatus } from '../testing/workspaceFixtures'

const mockApi = vi.fn()
const mockFetchJobsSnapshot = vi.fn()
const mockFetchJobFacets = vi.fn()
const mockFetchWorkspaceStats = vi.fn()
const mockFetchWorkspacePackages = vi.fn()
const mockFetchWorkflowDefinition = vi.fn()

vi.mock('../api', () => ({
  api: (...args: Parameters<typeof api>) => mockApi(...args),
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
  fetchActiveWorkflowRevision: (
    ...args: Parameters<typeof fetchActiveWorkflowRevision>
  ) => mockFetchWorkflowDefinition(...args),
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

describe('WorkspaceMainPage', () => {
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
      paused: null,
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
        paused: null,
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
      paused: null,
    })
    expect(screen.getByPlaceholderText('搜索 ID / 标题 / 批次')).toHaveValue('')
  })

  it('guides through Studio as step 1 for a workspace without a published workflow', async () => {
    // 引导导航细节见 WorkspaceMainPage.onboarding.test.tsx；这里保留一个
    // 主文件级冒烟：无 published workflow 时主页面正常渲染引导入口。
    mockFetchWorkspaceStats.mockResolvedValue({
      ...baseStats,
      workflow_key: null,
      workflow_label: null,
    } as unknown as WorkspaceStats)
    renderPage()
    await loadJobsViaSSE()

    const studioButton = await screen.findByRole('button', {
      name: '进入 Studio',
    })
    expect(studioButton).toBeEnabled()
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
