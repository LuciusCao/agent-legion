import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
  within,
} from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { SettingsPage } from './SettingsPage'
import { useSettingStore } from '../stores/settingStore'
import type { SettingState } from '../stores/settingStore'
import type { WorkspaceSettings } from '../types'
import { useUiStore } from '../stores/uiStore'
import { useExecutorsStore } from '../stores/executorsStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { makeWorkspace } from '../testing/workspaceFixtures'
import { api, fetchWorkflows } from '../api'
import { expectConsoleWarning } from '../test-setup'

vi.mock('../api', () => ({
  api: vi.fn(),
  fetchWorkflows: vi.fn(),
  fetchWorkspaces: vi.fn(),
}))

const mockApi = vi.mocked(api)
const mockFetchWorkflows = vi.mocked(fetchWorkflows)

// Capture the real store actions before beforeEach replaces them with mocks.
const originalActions = { ...useSettingStore.getState() }

const defaultState: SettingState = {
  workspaceId: 'ws1',
  workspaceName: '测试空间',
  workspaceDescription: '测试描述',
  settings: {
    entityType: 'question',
    intakeModes: [],
    labelOverrides: {},
    workflowKey: '',
    resources: {},
  },
  originalWorkspaceName: '测试空间',
  originalWorkspaceDescription: '测试描述',
  originalSettings: null,
  isDirty: false,
  globalServices: {
    cms: {
      baseUrl: 'http://cms.example.com',
      tokenConfigured: true,
      env: 'prod',
      healthy: null,
      lastCheckedAt: null,
    },
  },
  resourceProviders: [],
  workflowDefinition: null,
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
  agentRoutes: [],
  setWorkspaceId: vi.fn(),
  setWorkspaceName: vi.fn((name: string) => {
    useSettingStore.setState({ workspaceName: name, isDirty: true })
  }),
  setWorkspaceDescription: vi.fn((desc: string) => {
    useSettingStore.setState({ workspaceDescription: desc, isDirty: true })
  }),
  setSettings: vi.fn((s) => {
    useSettingStore.setState((state) => ({
      settings: { ...state.settings, ...s },
      isDirty: true,
    }))
  }),
  setExecutorAllocation: vi.fn(),
  requestExecutorRemoval: vi.fn(),
  confirmExecutorRemoval: vi.fn(),
  cancelExecutorRemoval: vi.fn(),
  setNodeBinding: vi.fn(),
  setNodeLimit: vi.fn(),
  setAgentCapacity: vi.fn(),
  fetchSettings: vi.fn().mockResolvedValue(undefined),
  fetchGlobalServices: vi.fn().mockResolvedValue(undefined),
  fetchResourceProviders: vi.fn().mockResolvedValue(undefined),
  fetchWorkflowDefinition: vi.fn().mockResolvedValue(undefined),
  saveAll: vi.fn().mockResolvedValue(undefined),
  testConnection: vi.fn().mockResolvedValue(undefined),
  resetTestStatus: vi.fn(),
}

function renderPage(initialEntries = ['/workspaces/ws1/settings']) {
  return render(
    <MemoryRouter
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      initialEntries={initialEntries}
    >
      <Routes>
        <Route
          path="/workspaces/:workspaceId/settings"
          element={<SettingsPage />}
        />
        <Route
          path="/workspaces/:workspaceId"
          element={<div>Workspace main</div>}
        />
        <Route path="/" element={<div>Dashboard</div>} />
      </Routes>
    </MemoryRouter>
  )
}

describe('SettingsPage', () => {
  beforeEach(() => {
    useSettingStore.setState(defaultState)
    useUiStore.setState({ toast: null })
    // Stub the worker refresh so its debounced api('/api/agent-workers')
    // call never lands in the shared mocked api call history.
    useExecutorsStore.setState({
      workers: [],
      connectionStatus: {},
      refreshWorkers: vi.fn().mockResolvedValue(undefined),
    })
    useWorkspaceStore.setState({
      workspaces: [
        makeWorkspace({
          id: 'ws1',
          name: '测试空间',
          description: '测试描述',
          default_workflow_key: 'question_content',
        }),
      ],
      currentWorkspace: null,
      workspaceStats: {},
      loading: false,
      error: null,
      deleteWorkspace: vi.fn().mockResolvedValue(undefined),
    })
    mockApi.mockReset()
    mockApi.mockResolvedValue({})
    mockFetchWorkflows.mockReset()
    mockFetchWorkflows.mockResolvedValue({ workflows: [] })
  })

  it('renders all sections in order', async () => {
    useSettingStore.setState({
      workflowDefinition: {
        key: 'question_content',
        label: '题目内容生成',
        intake: {
          modes: [
            {
              key: 'direct_ids',
              label: '直接输入 ID',
              input_field: 'question_ids',
              resource: '',
            },
          ],
        },
        edges: [],
        nodes: [
          {
            key: 'fetch_questions',
            label: '获取题目',
            capability: 'fetch_questions',
            after: [],
            inputs: [],
            outputs: [],
          },
        ],
      },
      executorCatalog: [
        {
          id: 'local-default',
          kind: 'local',
          capabilities: ['fetch_questions'],
          global_capacity: 4,
        },
      ],
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'local-default',
            workspace_id: 'ws1',
            concurrency_limit: 4,
          },
        ],
        bindings: [
          {
            workflow_key: 'question_content',
            node_key: 'fetch_questions',
            executor_id: 'local-default',
          },
        ],
        node_limits: [],
        migration_warnings: [],
      },
    })
    renderPage()
    await act(async () => {})

    const headings = screen.getAllByRole('heading', { level: 2 })
    expect(headings.map((h) => h.textContent)).toEqual([
      '基本信息',
      '接入与资源',
      '工作流',
      '执行器分配',
      '节点绑定',
      'Agent 执行',
      'Worker Token',
      '本地节点并发',
      '危险操作',
    ])
  })

  it('renders workspace name in header', async () => {
    renderPage()
    await act(async () => {})
    expect(screen.getByText('测试空间 / 设置')).toBeInTheDocument()
  })

  it('updates workspace name and description on save', async () => {
    const saveAll = vi.fn().mockResolvedValue(undefined)
    useSettingStore.setState({
      isDirty: true,
      saveAll,
    })
    renderPage()
    await waitFor(() => {
      expect(screen.getByLabelText('Workspace 名称')).toBeInTheDocument()
    })
    const nameField = screen.getByLabelText(
      'Workspace 名称'
    ) as HTMLInputElement
    const descField = screen.getByLabelText('描述') as HTMLInputElement
    fireEvent.change(nameField, { target: { value: '新名称' } })
    fireEvent.change(descField, { target: { value: '新描述' } })
    const saveBtn = screen.getByLabelText('保存')
    fireEvent.click(saveBtn)
    await waitFor(() => {
      expect(saveAll).toHaveBeenCalled()
    })
  })

  it('navigates back to workspace main page', async () => {
    renderPage()
    await act(async () => {})
    const back = screen.getByTestId('app-bar-back')
    expect(back).toBeTruthy()
    fireEvent.click(back)
    expect(screen.getByText('Workspace main')).toBeInTheDocument()
  })

  it('calls fetchSettings on mount', async () => {
    const fetchSettings = vi.fn().mockResolvedValue(undefined)
    useSettingStore.setState({ fetchSettings })
    renderPage()
    await waitFor(() => {
      expect(fetchSettings).toHaveBeenCalledWith('ws1')
    })
  })

  it('calls fetchGlobalServices and fetchResourceProviders on mount', async () => {
    const fetchGlobalServices = vi.fn().mockResolvedValue(undefined)
    const fetchResourceProviders = vi.fn().mockResolvedValue(undefined)
    useSettingStore.setState({ fetchGlobalServices, fetchResourceProviders })
    renderPage()
    await waitFor(() => {
      expect(fetchGlobalServices).toHaveBeenCalled()
    })
    await waitFor(() => {
      expect(fetchResourceProviders).toHaveBeenCalled()
    })
  })

  it('calls test connection and shows status change', async () => {
    const testConnection = vi.fn().mockResolvedValue(undefined)
    useSettingStore.setState({ testConnection })
    renderPage()
    await waitFor(() => {
      expect(testConnection).toBeDefined()
    })
    const btn = screen.getByText('测试连接')
    fireEvent.click(btn)
    await waitFor(() => {
      expect(testConnection).toHaveBeenCalled()
    })
  })

  it('shows failed status and toast on test connection failure', async () => {
    const testConnection = vi.fn().mockImplementation(() => {
      useSettingStore.setState({
        testStatus: { state: 'failed', message: 'connection refused' },
      })
      useUiStore
        .getState()
        .showToast('连接测试失败：connection refused', 'error')
      return Promise.resolve()
    })
    useSettingStore.setState({ testConnection })
    renderPage()
    await waitFor(() => {
      expect(testConnection).toBeDefined()
    })
    const btn = screen.getByText('测试连接')
    fireEvent.click(btn)
    await waitFor(() => {
      const failedBadge = document.querySelector('.status-badge.failed')
      expect(failedBadge).toBeInTheDocument()
    })
  })

  it('calls saveAll when global save is clicked', async () => {
    const saveAll = vi.fn().mockResolvedValue(undefined)
    useSettingStore.setState({
      isDirty: true,
      saveAll,
    })
    renderPage()
    const saveBtn = screen.getByLabelText('保存')
    fireEvent.click(saveBtn)
    await waitFor(() => {
      expect(saveAll).toHaveBeenCalled()
    })
  })

  it('displays save error when save fails', async () => {
    const saveAll = vi.fn().mockImplementation(() => {
      useSettingStore.setState({ saveError: 'Server Error' })
      return Promise.resolve()
    })
    useSettingStore.setState({
      isDirty: true,
      saveAll,
    })
    renderPage()
    const saveBtn = screen.getByLabelText('保存')
    fireEvent.click(saveBtn)
    await waitFor(() => {
      expect(screen.getByText('Server Error')).toBeInTheDocument()
    })
  })

  it('renders resource provider params when intake mode is checked', async () => {
    useSettingStore.setState({
      workflowDefinition: {
        key: 'question_content',
        label: '题目内容生成',
        intake: {
          modes: [
            {
              key: 'by_knowledge',
              label: '按知识点',
              input_field: 'knowledge_codes',
              resource: 'question_detail',
            },
          ],
        },
        edges: [],
        nodes: [],
      },
      resourceProviders: [
        {
          key: 'question_detail',
          provider: 'cms.question.detail',
          path: '/question/detail',
          defaultParams: { bank_version: 'v5' },
          paramKeys: ['bank_version', 'country_id'],
        },
      ],
      settings: {
        ...defaultState.settings,
        intakeModes: ['by_knowledge'],
        resources: {
          question_detail: { enabled: true, config: {} },
        },
      },
    })
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('cms.question.detail')).toBeInTheDocument()
    })
    expect(screen.getByLabelText('bank_version')).toBeInTheDocument()
    expect(screen.getByLabelText('country_id')).toBeInTheDocument()
  })

  it('renders checked checkbox for enabled intake modes', async () => {
    useSettingStore.setState({
      workflowDefinition: {
        key: 'question_comprehension_info',
        label: '题目审题信息生成',
        intake: {
          modes: [
            {
              key: 'batch_by_knowledge',
              label: '按知识点批量',
              input_field: 'knowledge_codes',
              resource: '',
            },
            {
              key: 'batch_by_ids',
              label: '按题目ID批量',
              input_field: 'question_ids',
              resource: '',
            },
          ],
        },
        edges: [],
        nodes: [],
      },
      settings: {
        ...defaultState.settings,
        intakeModes: ['batch_by_ids'],
      },
    })
    const { container } = renderPage()
    await act(async () => {})
    const checkboxes = Array.from(
      container.querySelectorAll('input[type="checkbox"]')
    ) as HTMLInputElement[]
    expect(checkboxes[0].checked).toBe(false)
    expect(checkboxes[1].checked).toBe(true)
  })

  it('renders unchecked checkbox for disabled intake modes', async () => {
    useSettingStore.setState({
      workflowDefinition: {
        key: 'question_comprehension_info',
        label: '题目审题信息生成',
        intake: {
          modes: [
            {
              key: 'batch_by_ids',
              label: '按题目ID批量',
              input_field: 'question_ids',
              resource: '',
            },
          ],
        },
        edges: [],
        nodes: [],
      },
      settings: {
        ...defaultState.settings,
        intakeModes: [],
      },
    })
    const { container } = renderPage()
    await act(async () => {})
    const checkbox = container.querySelector(
      'input[type="checkbox"]'
    ) as HTMLInputElement
    expect(checkbox).toBeTruthy()
    expect(checkbox.checked).toBe(false)
  })

  it('renders executor binding section between allocation and local limit sections', async () => {
    useSettingStore.setState({
      workflowDefinition: {
        key: 'question_content',
        label: '题目内容生成',
        intake: { modes: [] },
        edges: [],
        nodes: [
          {
            key: 'fetch_questions',
            label: '获取题目',
            capability: 'fetch_questions',
            after: [],
            inputs: [],
            outputs: [],
          },
        ],
      },
      executorCatalog: [
        {
          id: 'local-default',
          kind: 'local',
          capabilities: ['fetch_questions'],
          global_capacity: 4,
        },
      ],
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'local-default',
            workspace_id: 'ws1',
            concurrency_limit: 4,
          },
        ],
        bindings: [
          {
            workflow_key: 'question_content',
            node_key: 'fetch_questions',
            executor_id: 'local-default',
          },
        ],
        node_limits: [],
        migration_warnings: [],
      },
    })
    renderPage()
    await act(async () => {})

    const headings = screen.getAllByRole('heading', { level: 2 })
    const labels = headings.map((h) => h.textContent)
    expect(labels.indexOf('节点绑定')).toBeGreaterThan(
      labels.indexOf('执行器分配')
    )
    expect(labels.indexOf('本地节点并发')).toBeGreaterThan(
      labels.indexOf('节点绑定')
    )
  })

  it('saves the complete executor aggregate in one PUT request', async () => {
    const settings: WorkspaceSettings = {
      entityType: 'question',
      intakeModes: [],
      labelOverrides: {},
      workflowKey: 'sample_workflow',
      resources: {},
    }
    useSettingStore.setState({
      ...defaultState,
      workspaceName: 'Flow Workspace',
      originalWorkspaceName: 'Flow Workspace',
      workspaceDescription: '',
      originalWorkspaceDescription: '',
      settings,
      originalSettings: settings,
      executorCatalog: [
        {
          id: 'local-default',
          kind: 'local' as const,
          capabilities: ['fetch_questions'],
          global_capacity: 4,
        },
      ],
      workflowDefinition: {
        key: 'sample_workflow',
        label: '示例工作流',
        intake: { modes: [] },
        edges: [],
        nodes: [
          {
            key: 'fetch_questions',
            label: '获取题目',
            capability: 'fetch_questions',
            after: [],
            inputs: [],
            outputs: ['questions.json'],
          },
        ],
      },
      executorConfiguration: {
        allocations: [],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
      },
      originalExecutorConfiguration: {
        allocations: [],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
      },
      setExecutorAllocation: originalActions.setExecutorAllocation,
      setNodeBinding: originalActions.setNodeBinding,
      setNodeLimit: originalActions.setNodeLimit,
      saveAll: originalActions.saveAll,
    })

    mockFetchWorkflows.mockResolvedValueOnce({
      workflows: [{ key: 'sample_workflow', label: '示例工作流' }],
    })
    expectConsoleWarning(/out-of-range value/)

    mockApi.mockResolvedValueOnce({
      workspace: { name: 'Flow Workspace', description: '' },
      settings,
      executor_configuration: {
        allocations: [
          {
            executor_id: 'local-default',
            workspace_id: 'ws1',
            concurrency_limit: 1,
          },
        ],
        bindings: [
          {
            workflow_key: 'sample_workflow',
            node_key: 'fetch_questions',
            executor_id: 'local-default',
          },
        ],
        node_limits: [
          {
            workflow_key: 'sample_workflow',
            node_key: 'fetch_questions',
            concurrency_limit: 2,
          },
        ],
        migration_warnings: [],
      },
    })

    renderPage()
    await act(async () => {})

    await waitFor(() => {
      expect(
        screen.getByRole('checkbox', { name: '分配 local-default' })
      ).toBeInTheDocument()
    })

    const switchEl = screen.getByRole('checkbox', {
      name: '分配 local-default',
    })
    await act(async () => {
      fireEvent.click(switchEl)
    })

    await waitFor(() => {
      expect(
        useSettingStore.getState().executorConfiguration.allocations
      ).toHaveLength(1)
    })

    const select = screen.getByRole('combobox', {
      name: '绑定 fetch_questions',
    })
    await act(async () => {
      fireEvent.mouseDown(select)
    })
    await act(async () => {
      fireEvent.click(
        screen.getByRole('option', { name: 'local-default (local)' })
      )
    })

    await waitFor(() => {
      expect(
        useSettingStore.getState().executorConfiguration.bindings
      ).toHaveLength(1)
    })

    const limitInput = screen.getByLabelText(
      '获取题目 并发上限'
    ) as HTMLInputElement
    await act(async () => {
      fireEvent.change(limitInput, { target: { value: '2' } })
    })

    await waitFor(() => {
      expect(
        useSettingStore.getState().executorConfiguration.node_limits
      ).toEqual([
        {
          workflow_key: 'sample_workflow',
          node_key: 'fetch_questions',
          concurrency_limit: 2,
        },
      ])
    })

    const saveBtn = screen.getByLabelText('保存')
    await act(async () => {
      fireEvent.click(saveBtn)
    })

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledTimes(1)
    })

    const call = mockApi.mock.calls[0]
    expect(call[0]).toBe('/api/workspaces/ws1/configuration')
    expect(call[1]).toMatchObject({ method: 'PUT' })
    const body = JSON.parse(call[1]!.body as string)
    expect(body).toMatchObject({
      name: 'Flow Workspace',
      description: '',
      executor_allocations: [
        { executor_id: 'local-default', concurrency_limit: 1 },
      ],
      node_bindings: [
        {
          workflow_key: 'sample_workflow',
          node_key: 'fetch_questions',
          executor_id: 'local-default',
        },
      ],
      node_limits: [
        {
          workflow_key: 'sample_workflow',
          node_key: 'fetch_questions',
          concurrency_limit: 2,
        },
      ],
    })
  })

  it('confirms executor allocation removal from SettingsPage', async () => {
    useSettingStore.setState({
      ...defaultState,
      executorCatalog: [
        {
          id: 'local-default',
          kind: 'local' as const,
          capabilities: ['fetch_questions'],
          global_capacity: 4,
        },
      ],
      workflowDefinition: {
        key: 'sample_workflow',
        label: '示例工作流',
        intake: { modes: [] },
        edges: [],
        nodes: [
          {
            key: 'fetch_questions',
            label: '获取题目',
            capability: 'fetch_questions',
            after: [],
            inputs: [],
            outputs: ['questions.json'],
          },
        ],
      },
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'local-default',
            workspace_id: 'ws1',
            concurrency_limit: 2,
          },
        ],
        bindings: [
          {
            workflow_key: 'sample_workflow',
            node_key: 'fetch_questions',
            executor_id: 'local-default',
          },
        ],
        node_limits: [],
        migration_warnings: [],
      },
      originalExecutorConfiguration: {
        allocations: [
          {
            executor_id: 'local-default',
            workspace_id: 'ws1',
            concurrency_limit: 2,
          },
        ],
        bindings: [
          {
            workflow_key: 'sample_workflow',
            node_key: 'fetch_questions',
            executor_id: 'local-default',
          },
        ],
        node_limits: [],
        migration_warnings: [],
      },
      requestExecutorRemoval: originalActions.requestExecutorRemoval,
      confirmExecutorRemoval: originalActions.confirmExecutorRemoval,
      cancelExecutorRemoval: originalActions.cancelExecutorRemoval,
    })

    renderPage()
    await act(async () => {})

    const switchEl = screen.getByRole('checkbox', {
      name: '分配 local-default',
    })
    await act(async () => {
      fireEvent.click(switchEl)
    })

    await waitFor(() => {
      expect(
        screen.getByText('移除执行器会同时清除以下节点绑定')
      ).toBeInTheDocument()
      expect(
        screen.getByText('sample_workflow / fetch_questions')
      ).toBeInTheDocument()
    })

    await act(async () => {
      fireEvent.click(screen.getByText('取消'))
    })
    await waitFor(() => {
      expect(
        useSettingStore.getState().executorConfiguration.allocations
      ).toHaveLength(1)
      expect(
        useSettingStore.getState().executorConfiguration.bindings
      ).toHaveLength(1)
      expect(useSettingStore.getState().pendingAllocationRemoval).toBeNull()
    })

    await act(async () => {
      fireEvent.click(switchEl)
    })
    await waitFor(() => {
      expect(screen.getByText('确认')).toBeInTheDocument()
    })

    await act(async () => {
      fireEvent.click(screen.getByText('确认'))
    })
    await waitFor(() => {
      expect(
        useSettingStore.getState().executorConfiguration.allocations
      ).toEqual([])
      expect(useSettingStore.getState().executorConfiguration.bindings).toEqual(
        []
      )
      expect(useSettingStore.getState().pendingAllocationRemoval).toBeNull()
    })
  })

  it('shows delete workspace button for non-default workspace', async () => {
    renderPage()
    await act(async () => {})
    expect(
      screen.getByRole('button', { name: '删除 Workspace' })
    ).toBeInTheDocument()
  })

  it('hides delete workspace button for default workspace', async () => {
    renderPage(['/workspaces/default/settings'])
    await act(async () => {})
    expect(
      screen.queryByRole('button', { name: '删除 Workspace' })
    ).not.toBeInTheDocument()
  })

  it('disables delete workspace button while workspace name is not loaded', async () => {
    useSettingStore.setState({ workspaceName: '' })
    renderPage()
    await act(async () => {})
    expect(
      screen.getByRole('button', { name: '删除 Workspace' })
    ).toBeDisabled()
  })

  it('opens delete dialog and deletes workspace on confirm', async () => {
    const deleteWorkspace = vi.fn().mockResolvedValue(undefined)
    useWorkspaceStore.setState({ deleteWorkspace })

    renderPage()
    await act(async () => {})

    fireEvent.click(screen.getByRole('button', { name: '删除 Workspace' }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toBeInTheDocument()

    const input = within(dialog).getByLabelText('Workspace 名称')
    await act(async () => {
      ;(input as HTMLInputElement).value = '测试空间'
      input.dispatchEvent(new InputEvent('input', { bubbles: true }))
    })

    fireEvent.click(within(dialog).getByText('确认删除'))
    await waitFor(() => {
      expect(deleteWorkspace).toHaveBeenCalledWith('ws1')
    })
    await waitFor(() => {
      expect(screen.getByText('Dashboard')).toBeInTheDocument()
    })
  })

  it('shows error when delete workspace fails', async () => {
    const deleteWorkspace = vi
      .fn()
      .mockRejectedValue(new Error('Cannot delete workspace with running jobs'))
    useWorkspaceStore.setState({ deleteWorkspace })

    renderPage()
    await act(async () => {})

    fireEvent.click(screen.getByRole('button', { name: '删除 Workspace' }))
    const dialog = screen.getByRole('dialog')
    const input = within(dialog).getByLabelText('Workspace 名称')
    await act(async () => {
      ;(input as HTMLInputElement).value = '测试空间'
      input.dispatchEvent(new InputEvent('input', { bubbles: true }))
    })

    fireEvent.click(within(dialog).getByText('确认删除'))
    await waitFor(() => {
      expect(
        within(dialog).getByText('Cannot delete workspace with running jobs')
      ).toBeInTheDocument()
    })
  })

  it('does not expose the legacy workflow local concurrency control', async () => {
    renderPage()
    await act(async () => {})

    expect(screen.queryByText('本地并发限制')).not.toBeInTheDocument()
  })
})
