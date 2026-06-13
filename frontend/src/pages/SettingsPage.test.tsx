import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SettingsPage } from './SettingsPage'
import { useSettingStore } from '../stores/settingStore'
import type { SettingState } from '../stores/settingStore'
import type { WorkspaceSettings } from '../types'
import { useUiStore } from '../stores/uiStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { api, fetchPipelines } from '../api'

vi.mock('../api', () => ({
  api: vi.fn(),
  fetchPipelines: vi.fn(),
  fetchWorkspaces: vi.fn(),
}))

const mockApi = vi.mocked(api)
const mockFetchPipelines = vi.mocked(fetchPipelines)

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
    pipelineKey: '',
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
  pipelineDefinition: null,
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
  fetchSettings: vi.fn().mockResolvedValue(undefined),
  fetchGlobalServices: vi.fn().mockResolvedValue(undefined),
  fetchResourceProviders: vi.fn().mockResolvedValue(undefined),
  fetchPipelineDefinition: vi.fn().mockResolvedValue(undefined),
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
      </Routes>
    </MemoryRouter>
  )
}

describe('SettingsPage', () => {
  beforeEach(() => {
    useSettingStore.setState(defaultState)
    useUiStore.setState({ toast: null })
    useWorkspaceStore.setState({
      workspaces: [
        {
          id: 'ws1',
          name: '测试空间',
          description: '测试描述',
          default_pipeline_key: 'question_content',
          default_entity: 'question',
        },
      ],
      currentWorkspace: null,
      workspaceStats: {},
      loading: false,
      error: null,
    })
    mockApi.mockReset()
    mockApi.mockResolvedValue({})
    mockFetchPipelines.mockReset()
    mockFetchPipelines.mockResolvedValue({ pipelines: [] })
  })

  it('renders all six sections in order', () => {
    useSettingStore.setState({
      pipelineDefinition: {
        key: 'question_content',
        label: '题目内容生成',
        concurrency: { local: 8, agent: 2, nodes: {} },
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
        nodes: [
          {
            key: 'fetch_questions',
            label: '获取题目',
            capability: 'fetch_questions',
            runner: 'local',
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
            pipeline_key: 'question_content',
            node_key: 'fetch_questions',
            executor_id: 'local-default',
          },
        ],
        node_limits: [],
        migration_warnings: [],
      },
    })
    renderPage()

    const headings = screen.getAllByRole('heading', { level: 2 })
    expect(headings.map((h) => h.textContent)).toEqual([
      '基本信息',
      '接入与资源',
      'Pipeline',
      '执行器分配',
      '节点绑定',
      '本地节点并发',
    ])
  })

  it('renders workspace name in header', () => {
    renderPage()
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
      expect(
        document.querySelector('md-outlined-text-field[label="Workspace 名称"]')
      ).toBeTruthy()
    })
    const nameField = document.querySelector(
      'md-outlined-text-field[label="Workspace 名称"]'
    ) as HTMLInputElement
    const descField = document.querySelector(
      'md-outlined-text-field[label="描述"]'
    ) as HTMLInputElement
    nameField.value = '新名称'
    descField.value = '新描述'
    fireEvent.input(nameField)
    fireEvent.input(descField)
    const saveBtn = screen.getByLabelText('保存')
    fireEvent.click(saveBtn)
    await waitFor(() => {
      expect(saveAll).toHaveBeenCalled()
    })
  })

  it('navigates back to workspace main page', () => {
    renderPage()
    const back = document.querySelector('md-icon-button')
    expect(back).toBeTruthy()
    fireEvent.click(back!)
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
      pipelineDefinition: {
        key: 'question_content',
        label: '题目内容生成',
        concurrency: { local: 8, agent: 2, nodes: {} },
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
    expect(
      document.querySelector('md-outlined-text-field[label="bank_version"]')
    ).toBeTruthy()
    expect(
      document.querySelector('md-outlined-text-field[label="country_id"]')
    ).toBeTruthy()
  })

  it('renders executor binding section between allocation and local limit sections', () => {
    useSettingStore.setState({
      pipelineDefinition: {
        key: 'question_content',
        label: '题目内容生成',
        concurrency: { local: 8, agent: 2, nodes: {} },
        intake: { modes: [] },
        nodes: [
          {
            key: 'fetch_questions',
            label: '获取题目',
            capability: 'fetch_questions',
            runner: 'local',
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
            pipeline_key: 'question_content',
            node_key: 'fetch_questions',
            executor_id: 'local-default',
          },
        ],
        node_limits: [],
        migration_warnings: [],
      },
    })
    renderPage()

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
      pipelineKey: 'reading_analysis',
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
      pipelineDefinition: {
        key: 'reading_analysis',
        label: '阅读分析',
        concurrency: { local: 4, agent: 2, nodes: {} },
        intake: { modes: [] },
        nodes: [
          {
            key: 'fetch_questions',
            label: '获取题目',
            capability: 'fetch_questions',
            runner: 'local' as const,
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
            pipeline_key: 'reading_analysis',
            node_key: 'fetch_questions',
            executor_id: 'local-default',
          },
        ],
        node_limits: [
          {
            pipeline_key: 'reading_analysis',
            node_key: 'fetch_questions',
            concurrency_limit: 2,
          },
        ],
        migration_warnings: [],
      },
    })

    renderPage()

    await waitFor(() => {
      expect(
        document.querySelector('md-switch[aria-label="分配 local-default"]')
      ).toBeTruthy()
    })

    const switchEl = document.querySelector(
      'md-switch[aria-label="分配 local-default"]'
    ) as HTMLElement
    fireEvent.click(switchEl)

    await waitFor(() => {
      expect(
        useSettingStore.getState().executorConfiguration.allocations
      ).toHaveLength(1)
    })

    const select = document.querySelector(
      'md-outlined-select[aria-label="绑定 fetch_questions"]'
    ) as HTMLElement
    await act(async () => {
      select.dispatchEvent(
        new CustomEvent('change', {
          detail: { value: 'local-default' },
          bubbles: true,
        })
      )
    })

    await waitFor(() => {
      expect(
        useSettingStore.getState().executorConfiguration.bindings
      ).toHaveLength(1)
    })

    const limitInput = document.querySelector(
      'md-outlined-text-field[label="获取题目 并发上限"]'
    ) as HTMLInputElement
    limitInput.value = '2'
    fireEvent.input(limitInput)

    await waitFor(() => {
      expect(
        useSettingStore.getState().executorConfiguration.node_limits
      ).toEqual([
        {
          pipeline_key: 'reading_analysis',
          node_key: 'fetch_questions',
          concurrency_limit: 2,
        },
      ])
    })

    const saveBtn = screen.getByLabelText('保存')
    fireEvent.click(saveBtn)

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
          pipeline_key: 'reading_analysis',
          node_key: 'fetch_questions',
          executor_id: 'local-default',
        },
      ],
      node_limits: [
        {
          pipeline_key: 'reading_analysis',
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
      pipelineDefinition: {
        key: 'reading_analysis',
        label: '阅读分析',
        concurrency: { local: 4, agent: 2, nodes: {} },
        intake: { modes: [] },
        nodes: [
          {
            key: 'fetch_questions',
            label: '获取题目',
            capability: 'fetch_questions',
            runner: 'local' as const,
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
            pipeline_key: 'reading_analysis',
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
            pipeline_key: 'reading_analysis',
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

    const switchEl = document.querySelector(
      'md-switch[aria-label="分配 local-default"]'
    ) as HTMLElement
    fireEvent.click(switchEl)

    await waitFor(() => {
      expect(
        screen.getByText('移除执行器会同时清除以下节点绑定')
      ).toBeInTheDocument()
      expect(
        screen.getByText('reading_analysis / fetch_questions')
      ).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('取消'))
    await waitFor(() => {
      expect(
        useSettingStore.getState().executorConfiguration.allocations
      ).toHaveLength(1)
      expect(
        useSettingStore.getState().executorConfiguration.bindings
      ).toHaveLength(1)
      expect(useSettingStore.getState().pendingAllocationRemoval).toBeNull()
    })

    fireEvent.click(switchEl)
    await waitFor(() => {
      expect(screen.getByText('确认')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('确认'))
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

  it('does not expose the legacy pipeline local concurrency control', () => {
    renderPage()

    expect(screen.queryByText('本地并发限制')).not.toBeInTheDocument()
  })
})
