import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, waitFor } from '@testing-library/react'
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
  fetchJobsSnapshot,
  fetchJobFacets,
  fetchWorkspacePackages,
  fetchWorkspaceStats,
} from '../api'
import { EventSourceMock } from '../testing/eventSourceMock'
import type { WorkspaceStats } from '../types/workspaceTypes'

// 新 workspace 空态分步引导（EmptyStateGuide）的集成测试：2 步形态（#333）
// 与引导导航。就绪判定与展示判定的分支细节在 lib/onboardingReadiness.test.ts。

const mockApi = vi.fn()
const mockFetchJobsSnapshot = vi.fn()
const mockFetchJobFacets = vi.fn()
const mockFetchWorkspaceStats = vi.fn()
const mockFetchWorkspacePackages = vi.fn()
const mockFetchWorkflowDefinition = vi.fn()

vi.mock('../api', () => ({
  api: (...args: Parameters<typeof api>) => mockApi(...args),
  fetchJobsSnapshot: (...args: Parameters<typeof fetchJobsSnapshot>) =>
    mockFetchJobsSnapshot(...args),
  fetchJobFacets: (...args: Parameters<typeof fetchJobFacets>) =>
    mockFetchJobFacets(...args),
  fetchWorkspaceStats: (...args: Parameters<typeof fetchWorkspaceStats>) =>
    mockFetchWorkspaceStats(...args),
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

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/workspaces/ws1']}>
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
  job_stats: { pending: 0, running: 0, completed: 0, failed: 0 },
  code_pool: { capacity: 16, running: 0, available: 16 },
  latest_run: null,
}

const workflowDefinition = {
  key: 'question_content',
  label: 'Question Content',
  nodes: [
    {
      key: 'review',
      label: '审核',
      after: [],
      capability: 'review',
      inputs: [],
      outputs: [],
    },
    {
      key: 'agent_review',
      label: 'Agent 审核',
      after: ['review'],
      capability: 'agent_review',
      inputs: [],
      outputs: [],
      execution: {
        provider: 'openai',
        model: 'gpt-5',
        thinking: '',
        prompt: '',
      },
    },
  ],
}

describe('WorkspaceMainPage onboarding guide', () => {
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
      agents: [],
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
        workflowKey: '',
      },
      originalWorkspaceName: 'WS One',
      originalWorkspaceDescription: '',
      originalSettings: {
        entityType: 'question',
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

  it('guides through Studio as step 1 for a workspace without a published workflow', async () => {
    mockFetchWorkspaceStats.mockResolvedValue({
      ...baseStats,
      workflow_key: null,
      workflow_label: null,
    } as unknown as WorkspaceStats)
    mockFetchWorkflowDefinition.mockResolvedValue({ workflow: null })
    render(
      <MemoryRouter initialEntries={['/workspaces/ws1']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/workflow-studio"
            element={<div>Studio 页面</div>}
          />
          <Route
            path="/workspaces/:workspaceId/*"
            element={<WorkspaceMainPage />}
          />
        </Routes>
      </MemoryRouter>
    )

    await loadJobsViaSSE()

    const studioButton = await screen.findByRole('button', {
      name: '进入 Studio',
    })
    expect(studioButton).toBeEnabled()

    await act(async () => {
      studioButton.click()
    })

    expect(await screen.findByText('Studio 页面')).toBeInTheDocument()
  })

  it('marks the Studio step completed when the workspace has a published workflow', async () => {
    renderPage()
    await loadJobsViaSSE()

    expect(
      await screen.findByRole('button', { name: '进入 Studio' })
    ).toBeInTheDocument()
    // 2 步形态（#333）：仅步骤 1 有完成态；发布后「添加条目」直接解锁。
    expect(screen.getAllByText('已完成')).toHaveLength(1)
    expect(screen.getByRole('button', { name: '添加条目' })).toBeEnabled()
  })

  it('unlocks the add-item step even when an agent node lacks provider/model', async () => {
    // #333：agent 节点 execution 缺口不再阻塞引导（原步骤 2 已移除），
    // 真实缺口由 Studio 画布实时警报承载；agent 路由快照也不再被请求。
    mockFetchWorkflowDefinition.mockResolvedValue({
      workflow: {
        ...workflowDefinition,
        nodes: [
          workflowDefinition.nodes[0],
          { ...workflowDefinition.nodes[1], execution: undefined },
        ],
      },
    })

    renderPage()
    await loadJobsViaSSE()

    expect(
      await screen.findByRole('button', { name: '添加条目' })
    ).toBeEnabled()
    expect(
      screen.queryByRole('button', { name: '去配置' })
    ).not.toBeInTheDocument()
  })

  it('never fetches the settings snapshot for the onboarding guide', async () => {
    // #333：引导不再消费 agent 路由/设置快照——无论引导展示（无任务）还是
    // 隐藏（有任务），这些请求都不应发出。
    const settingsPaths = [
      '/api/workspaces/ws1',
      '/api/workspaces/ws1/settings',
      '/api/workspaces/ws1/agent-routes',
    ]
    const settingsCalls: string[] = []
    mockApi.mockImplementation((path: string) => {
      if (settingsPaths.includes(path)) {
        settingsCalls.push(path)
      }
      if (path === '/api/workspaces/ws1/stats') {
        return Promise.resolve(baseStats)
      }
      return Promise.resolve({})
    })

    renderPage()
    await loadJobsViaSSE()

    // 引导展示态（默认 mock 无任务）。
    expect(
      await screen.findByRole('heading', { name: '开始使用 Workspace' })
    ).toBeInTheDocument()
    expect(settingsCalls).toEqual([])
    expect(mockGetWorkspaceExecutionConfiguration).not.toHaveBeenCalled()
  })

  it('hides the guide when jobs exist', async () => {
    mockFetchJobsSnapshot.mockImplementation(() =>
      Promise.resolve({
        workspace_id: 'ws1',
        revision: 1,
        stats: baseStats.job_stats,
        jobs: [
          {
            id: 'j1',
            workspace_id: 'ws1',
            source_id: 'Q1',
            title: 'Job',
            status: 'pending',
          },
        ],
        total: 1,
        next_cursor: null,
      })
    )

    renderPage()
    await loadJobsViaSSE()

    expect(
      screen.queryByRole('heading', { name: '开始使用 Workspace' })
    ).not.toBeInTheDocument()
  })
})
