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

// 新 workspace 空态分步引导（EmptyStateGuide）的集成测试：步骤解锁链
// （resolve_execution_block 同链）、设置快照请求门控、引导导航。就绪判定的
// 分支细节在 lib/onboardingReadiness.test.ts。

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

const mockGetWorkspaceExecutorConfiguration = vi.fn()

vi.mock('../api/executorApi', () => ({
  getWorkspaceExecutorConfiguration: (...args: unknown[]) =>
    mockGetWorkspaceExecutorConfiguration(...args),
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
    mockGetWorkspaceExecutorConfiguration.mockReset()
    mockGetWorkspaceExecutorConfiguration.mockResolvedValue({
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
      executorConfiguration: {
        node_limits: [],
        migration_warnings: [],
        agent_capacity: null,
      },
      originalExecutorConfiguration: null,
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
    expect(screen.getByText('已完成')).toBeInTheDocument()
  })

  it('unlocks step 3 when agent nodes carry execution overrides even without workspace defaults', async () => {
    // agent 节点 execution.* 已配齐：即使 workspace 默认为空（settings 快照
    // 返回空 agentDefaults），解析链（节点覆盖优先）也应判定就绪。
    mockApi.mockImplementation((path: string) => {
      if (path === '/api/workspaces/ws1/stats') {
        return Promise.resolve(baseStats)
      }
      if (path === '/api/workspaces/ws1/settings') {
        return Promise.resolve({
          entityType: 'question',
          intakeModes: ['manual'],
          labelOverrides: {},
          workflowKey: 'question_content',
          agentDefaults: { provider: '', model: '', thinking: '' },
        })
      }
      if (path === '/api/workspaces/ws1/agent-routes') {
        return Promise.resolve({
          routes: [
            {
              workflow_key: 'question_content',
              node_key: 'agent_review',
              node_label: 'Agent 审核',
              capability: 'agent_review',
              agent_id: 'agent-1',
              agent_skill: 'review',
            },
          ],
        })
      }
      return Promise.resolve({})
    })

    renderPage()
    await loadJobsViaSSE()

    expect(
      await screen.findByRole('button', { name: '添加条目' })
    ).toBeEnabled()
    // 解析链就绪 + 接入模式已勾选 → 步骤 1、2 均完成。
    expect(screen.getAllByText('已完成')).toHaveLength(2)
  })

  it('keeps step 3 locked when an agent node lacks provider/model resolution', async () => {
    // agent 节点存在（agentRoutes 命中）但 execution 未覆盖且 workspace
    // 默认为空：解析链两端都缺，步骤 2/3 保持锁定。
    mockApi.mockImplementation((path: string) => {
      if (path === '/api/workspaces/ws1/stats') {
        return Promise.resolve(baseStats)
      }
      if (path === '/api/workspaces/ws1/settings') {
        return Promise.resolve({
          entityType: 'question',
          intakeModes: ['manual'],
          labelOverrides: {},
          workflowKey: 'question_content',
          agentDefaults: { provider: '', model: '', thinking: '' },
        })
      }
      if (path === '/api/workspaces/ws1/agent-routes') {
        return Promise.resolve({
          routes: [
            {
              workflow_key: 'question_content',
              node_key: 'agent_review',
              node_label: 'Agent 审核',
              capability: 'agent_review',
              agent_id: 'agent-1',
              agent_skill: 'review',
            },
          ],
        })
      }
      return Promise.resolve({})
    })
    // 覆盖 revision：去掉 agent 节点的 execution 覆盖。
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
    ).toBeDisabled()
    expect(screen.getByText('已完成')).toBeInTheDocument()
  })

  it('does not fetch the settings snapshot when jobs exist and the guide is hidden', async () => {
    // 引导隐藏（有任务）：settings 快照的四个请求（workspace / settings /
    // executor-configuration / agent-routes）都不应发出。
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

    expect(
      screen.queryByRole('heading', { name: '开始使用 Workspace' })
    ).not.toBeInTheDocument()
    expect(settingsCalls).toEqual([])
    expect(mockGetWorkspaceExecutorConfiguration).not.toHaveBeenCalled()
  })
})
