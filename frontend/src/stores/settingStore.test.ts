import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useSettingStore } from './settingStore'
import type { SettingState } from './settingStore'
import { useUiStore } from './uiStore'
import { createMockUiState } from '../testing/fixtures'
import { api } from '../api'
import type { WorkspaceSettings } from '../types'
import type { WorkspaceExecutorConfiguration } from '../types/executorTypes'

vi.mock('../api', () => ({
  api: vi.fn(),
}))

vi.mock('./uiStore', () => ({
  useUiStore: {
    getState: vi.fn(),
    setState: vi.fn(),
  },
}))

const mockApi = vi.mocked(api)
const mockShowToast = vi.fn()
const mockGetState = vi.mocked(useUiStore.getState)

const defaultSettings: WorkspaceSettings = {
  entityType: 'question',
  intakeModes: [],
  labelOverrides: {},
  workflowKey: '',
}

const initialExecutorConfiguration: WorkspaceExecutorConfiguration = {
  allocations: [
    { executor_id: 'code-default', workspace_id: 'ws1', concurrency_limit: 2 },
  ],
  bindings: [
    {
      workflow_key: 'question_content',
      node_key: 'ingest',
      executor_id: 'code-default',
    },
  ],
  node_limits: [
    {
      workflow_key: 'question_content',
      node_key: 'ingest',
      concurrency_limit: 1,
    },
  ],
  migration_warnings: [],
}

const defaultState: Partial<SettingState> = {
  workspaceId: 'ws1',
  workspaceName: '',
  workspaceDescription: '',
  settings: defaultSettings,
  originalWorkspaceName: '',
  originalWorkspaceDescription: '',
  originalSettings: null,
  isDirty: false,
  isSaving: false,
  saveError: null,
  executorConfiguration: initialExecutorConfiguration,
  originalExecutorConfiguration: null,
  pendingAllocationRemoval: null,
}

describe('settingStore', () => {
  beforeEach(() => {
    useSettingStore.setState(defaultState)
    mockApi.mockReset()
    mockShowToast.mockReset()
    mockGetState.mockReturnValue(
      createMockUiState({ showToast: mockShowToast })
    )
  })

  it('updates settings via setSettings', () => {
    useSettingStore.getState().setSettings({ workflowKey: 'knowledge_content' })
    expect(useSettingStore.getState().settings.workflowKey).toBe(
      'knowledge_content'
    )
  })

  it('clears stale node configuration when the workflow changes', () => {
    useSettingStore.setState({
      executorConfiguration: initialExecutorConfiguration,
      originalSettings: defaultSettings,
      originalExecutorConfiguration: initialExecutorConfiguration,
    })

    useSettingStore.getState().setSettings({ workflowKey: 'legacy_workflow' })

    const state = useSettingStore.getState()
    expect(state.executorConfiguration.allocations).toEqual(
      initialExecutorConfiguration.allocations
    )
    expect(state.executorConfiguration.bindings).toEqual([])
    expect(state.executorConfiguration.node_limits).toEqual([])
  })

  it('updates labelOverrides via setSettings', () => {
    useSettingStore.getState().setSettings({ labelOverrides: { a: 'B' } })
    expect(useSettingStore.getState().settings.labelOverrides).toEqual({
      a: 'B',
    })
  })

  it('updates workspaceName and workspaceDescription', () => {
    useSettingStore.getState().setWorkspaceName('New Name')
    useSettingStore.getState().setWorkspaceDescription('New Desc')
    expect(useSettingStore.getState().workspaceName).toBe('New Name')
    expect(useSettingStore.getState().workspaceDescription).toBe('New Desc')
  })

  it('isDirty is true when workspaceName differs from original', () => {
    useSettingStore.setState({
      originalWorkspaceName: 'Old Name',
      originalSettings: defaultSettings,
      originalExecutorConfiguration: initialExecutorConfiguration,
    })
    useSettingStore.getState().setWorkspaceName('New Name')
    expect(useSettingStore.getState().isDirty).toBe(true)
  })

  it('isDirty is true when settings differ from original', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      originalExecutorConfiguration: initialExecutorConfiguration,
    })
    useSettingStore.getState().setSettings({ workflowKey: 'knowledge_content' })
    expect(useSettingStore.getState().isDirty).toBe(true)
  })

  it('isDirty is false when values match originals', () => {
    useSettingStore.setState({
      workspaceName: 'Name',
      workspaceDescription: 'Desc',
      originalWorkspaceName: 'Name',
      originalWorkspaceDescription: 'Desc',
      originalSettings: defaultSettings,
      originalExecutorConfiguration: initialExecutorConfiguration,
      executorConfiguration: initialExecutorConfiguration,
    })
    useSettingStore.getState().setWorkspaceName('Name')
    expect(useSettingStore.getState().isDirty).toBe(false)
  })

  it('isDirty is true when executor allocation limit differs from original', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      originalExecutorConfiguration: initialExecutorConfiguration,
    })
    useSettingStore.getState().setExecutorAllocation('code-default', 5)
    expect(useSettingStore.getState().isDirty).toBe(true)
  })

  it('isDirty is true when node binding differs from original', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      originalExecutorConfiguration: initialExecutorConfiguration,
    })
    useSettingStore
      .getState()
      .setNodeBinding('question_content', 'ingest', 'other-executor')
    expect(useSettingStore.getState().isDirty).toBe(true)
  })

  it('isDirty is true when node limit differs from original', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      originalExecutorConfiguration: initialExecutorConfiguration,
    })
    useSettingStore.getState().setNodeLimit('question_content', 'ingest', 3)
    expect(useSettingStore.getState().isDirty).toBe(true)
  })

  it('hydrateSettings writes draft and original snapshots and clears saveError', () => {
    useSettingStore.setState({
      saveError: 'HTTP 500: Internal Server Error',
      workspaceName: '编辑中',
    })
    const snapshot = {
      workspaceName: 'Test Workspace',
      workspaceDescription: 'A workspace',
      settings: {
        entityType: 'knowledge' as const,
        intakeModes: ['direct_ids'],
        labelOverrides: { direct_ids: '输入 ID' },
        workflowKey: 'knowledge_content',
      },
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'code-default',
            workspace_id: 'ws1',
            concurrency_limit: 3,
          },
        ],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
        agent_capacity: 7,
      },
    }

    useSettingStore.getState().hydrateSettings('ws2', snapshot)

    const state = useSettingStore.getState()
    expect(state.workspaceId).toBe('ws2')
    expect(state.workspaceName).toBe('Test Workspace')
    expect(state.originalWorkspaceName).toBe('Test Workspace')
    expect(state.settings.workflowKey).toBe('knowledge_content')
    expect(state.originalSettings).toEqual(state.settings)
    expect(state.executorConfiguration.agent_capacity).toBe(7)
    expect(state.originalExecutorConfiguration).toEqual(
      state.executorConfiguration
    )
    expect(state.isDirty).toBe(false)
    expect(state.saveError).toBeNull()
  })

  it('hydrateSettings recomputes isDirty from the hydrated baseline', () => {
    useSettingStore.setState({ isDirty: true })
    useSettingStore.getState().hydrateSettings('ws1', {
      workspaceName: '',
      workspaceDescription: '',
      settings: defaultSettings,
      executorConfiguration: initialExecutorConfiguration,
    })
    expect(useSettingStore.getState().isDirty).toBe(false)
  })

  it('setAgentCapacity updates executorConfiguration and marks dirty', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      executorConfiguration: {
        allocations: [],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
        agent_capacity: null,
      },
      originalExecutorConfiguration: {
        allocations: [],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
        agent_capacity: null,
      },
    })

    useSettingStore.getState().setAgentCapacity(5)

    const state = useSettingStore.getState()
    expect(state.executorConfiguration.agent_capacity).toBe(5)
    expect(state.isDirty).toBe(true)
  })

  it('saveAll sends agent_capacity only when it is set', async () => {
    mockApi.mockResolvedValue({
      workspace: { name: 'Test', description: '' },
      settings: defaultSettings,
      executor_configuration: {
        allocations: [],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
        agent_capacity: 6,
      },
    })
    useSettingStore.setState({
      executorConfiguration: {
        allocations: [],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
        agent_capacity: 6,
      },
    })

    await useSettingStore.getState().saveAll()
    let body = JSON.parse(mockApi.mock.calls[0][1]?.body as string)
    expect(body.agent_capacity).toBe(6)
    expect(
      useSettingStore.getState().executorConfiguration.agent_capacity
    ).toBe(6)

    mockApi.mockClear()
    mockApi.mockResolvedValue({
      workspace: { name: 'Test', description: '' },
      settings: defaultSettings,
      executor_configuration: {
        allocations: [],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
        agent_capacity: null,
      },
    })
    useSettingStore.setState({
      executorConfiguration: {
        allocations: [],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
        agent_capacity: null,
      },
    })

    await useSettingStore.getState().saveAll()
    body = JSON.parse(mockApi.mock.calls[0][1]?.body as string)
    expect('agent_capacity' in body).toBe(false)
  })

  it('saveAll sends exactly one PUT body containing executor_allocations, node_bindings, node_limits', async () => {
    mockApi.mockResolvedValue({
      workspace: { name: 'Test', description: 'Desc' },
      settings: {
        ...defaultSettings,
        workflowKey: 'question_content',
        intakeModes: ['direct_ids'],
      },
      executor_configuration: {
        allocations: [
          {
            executor_id: 'code-default',
            workspace_id: 'ws1',
            concurrency_limit: 4,
          },
        ],
        bindings: [
          {
            workflow_key: 'question_content',
            node_key: 'ingest',
            executor_id: 'code-default',
          },
        ],
        node_limits: [
          {
            workflow_key: 'question_content',
            node_key: 'ingest',
            concurrency_limit: 2,
          },
        ],
        migration_warnings: [],
      },
    })
    useSettingStore.setState({
      workspaceName: 'Test',
      workspaceDescription: 'Desc',
      originalWorkspaceName: '',
      originalWorkspaceDescription: '',
      originalSettings: defaultSettings,
      settings: {
        ...defaultSettings,
        workflowKey: 'question_content',
        intakeModes: ['direct_ids'],
      },
      originalExecutorConfiguration: {
        allocations: [],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
      },
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'code-default',
            workspace_id: 'ws1',
            concurrency_limit: 4,
          },
        ],
        bindings: [
          {
            workflow_key: 'question_content',
            node_key: 'ingest',
            executor_id: 'code-default',
          },
        ],
        node_limits: [
          {
            workflow_key: 'question_content',
            node_key: 'ingest',
            concurrency_limit: 2,
          },
        ],
        migration_warnings: [],
      },
    })
    await useSettingStore.getState().saveAll()
    expect(mockApi).toHaveBeenCalledTimes(1)
    expect(mockApi).toHaveBeenCalledWith(
      '/api/workspaces/ws1/configuration',
      expect.objectContaining({
        method: 'PUT',
        body: expect.stringContaining('"executor_allocations"'),
      })
    )
    const body = JSON.parse(mockApi.mock.calls[0][1]?.body as string)
    expect(body).toMatchObject({
      name: 'Test',
      description: 'Desc',
      executor_allocations: [
        { executor_id: 'code-default', concurrency_limit: 4 },
      ],
      node_bindings: [
        {
          workflow_key: 'question_content',
          node_key: 'ingest',
          executor_id: 'code-default',
        },
      ],
      node_limits: [
        {
          workflow_key: 'question_content',
          node_key: 'ingest',
          concurrency_limit: 2,
        },
      ],
    })
    expect(mockShowToast).toHaveBeenCalledWith('设置已保存', 'success')
  })

  it('saveAll replaces original snapshots from the response', async () => {
    const responseConfiguration: WorkspaceExecutorConfiguration = {
      allocations: [
        {
          executor_id: 'code-default',
          workspace_id: 'ws1',
          concurrency_limit: 4,
        },
      ],
      bindings: [],
      node_limits: [],
      migration_warnings: [],
    }
    mockApi.mockResolvedValue({
      workspace: { name: 'Saved', description: 'Saved Desc' },
      settings: {
        ...defaultSettings,
        workflowKey: 'question_content',
      },
      executor_configuration: responseConfiguration,
    })
    useSettingStore.setState({
      workspaceName: 'Test',
      workspaceDescription: 'Desc',
      originalWorkspaceName: '',
      originalWorkspaceDescription: '',
      originalSettings: defaultSettings,
      settings: {
        ...defaultSettings,
        workflowKey: 'question_content',
      },
      originalExecutorConfiguration: {
        allocations: [],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
      },
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'code-default',
            workspace_id: 'ws1',
            concurrency_limit: 4,
          },
        ],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
      },
    })
    await useSettingStore.getState().saveAll()
    const state = useSettingStore.getState()
    expect(state.workspaceName).toBe('Saved')
    expect(state.originalWorkspaceName).toBe('Saved')
    expect(state.originalExecutorConfiguration).toEqual({
      ...responseConfiguration,
      agent_capacity: null,
    })
    expect(state.isDirty).toBe(false)
  })

  it('saveAll surfaces errors and shows error toast', async () => {
    const err = Object.assign(new Error('Server Error'), { status: 500 })
    mockApi.mockRejectedValueOnce(err)
    await useSettingStore.getState().saveAll()
    expect(useSettingStore.getState().isSaving).toBe(false)
    expect(useSettingStore.getState().saveError).toBe('Server Error')
    expect(mockShowToast).toHaveBeenCalledWith('Server Error', 'error')
  })

  it('saveAll reports success/failure via its return value', async () => {
    mockApi.mockResolvedValueOnce({
      workspace: { name: 'Test', description: '' },
      settings: defaultSettings,
      executor_configuration: {
        allocations: [],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
        agent_capacity: null,
      },
    })
    await expect(useSettingStore.getState().saveAll()).resolves.toBe(true)

    mockApi.mockRejectedValueOnce(new Error('boom'))
    await expect(useSettingStore.getState().saveAll()).resolves.toBe(false)
  })

  it('saveAll refuses reentry while a save is in flight', async () => {
    // 重入守卫：并发 PUT 乱序会让先发的旧响应回写覆盖新快照。
    let release!: (value: unknown) => void
    mockApi.mockReturnValueOnce(
      new Promise((resolve) => {
        release = resolve
      })
    )
    const first = useSettingStore.getState().saveAll()
    expect(useSettingStore.getState().isSaving).toBe(true)

    await expect(useSettingStore.getState().saveAll()).resolves.toBe(false)
    expect(mockApi).toHaveBeenCalledTimes(1)

    release({
      workspace: { name: 'Test', description: '' },
      settings: defaultSettings,
      executor_configuration: {
        allocations: [],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
        agent_capacity: null,
      },
    })
    await expect(first).resolves.toBe(true)
    expect(useSettingStore.getState().isSaving).toBe(false)
  })

  it('setExecutorAllocation updates or creates an allocation', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      originalExecutorConfiguration: initialExecutorConfiguration,
    })
    useSettingStore.getState().setExecutorAllocation('code-default', 5)
    const allocation = useSettingStore
      .getState()
      .executorConfiguration.allocations.find(
        (a) => a.executor_id === 'code-default'
      )
    expect(allocation?.concurrency_limit).toBe(5)
  })

  it('setNodeBinding updates or creates a binding', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      originalExecutorConfiguration: initialExecutorConfiguration,
    })
    useSettingStore
      .getState()
      .setNodeBinding('question_content', 'parse', 'other-executor')
    const binding = useSettingStore
      .getState()
      .executorConfiguration.bindings.find(
        (b) => b.workflow_key === 'question_content' && b.node_key === 'parse'
      )
    expect(binding).toEqual({
      workflow_key: 'question_content',
      node_key: 'parse',
      executor_id: 'other-executor',
    })
  })

  it('setNodeBinding with null unbinds a node and removes its node limit', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      originalExecutorConfiguration: initialExecutorConfiguration,
    })
    useSettingStore
      .getState()
      .setNodeBinding('question_content', 'ingest', null)
    const state = useSettingStore.getState()
    expect(
      state.executorConfiguration.bindings.some(
        (b) => b.workflow_key === 'question_content' && b.node_key === 'ingest'
      )
    ).toBe(false)
    expect(
      state.executorConfiguration.node_limits.some(
        (l) => l.workflow_key === 'question_content' && l.node_key === 'ingest'
      )
    ).toBe(false)
  })

  it('setNodeLimit updates or creates a node limit', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      originalExecutorConfiguration: initialExecutorConfiguration,
    })
    useSettingStore.getState().setNodeLimit('question_content', 'ingest', 3)
    const limit = useSettingStore
      .getState()
      .executorConfiguration.node_limits.find(
        (l) => l.workflow_key === 'question_content' && l.node_key === 'ingest'
      )
    expect(limit?.concurrency_limit).toBe(3)
  })

  it('setNodeLimit with null removes a node limit', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      originalExecutorConfiguration: initialExecutorConfiguration,
    })
    useSettingStore.getState().setNodeLimit('question_content', 'ingest', null)
    expect(
      useSettingStore
        .getState()
        .executorConfiguration.node_limits.some(
          (l) =>
            l.workflow_key === 'question_content' && l.node_key === 'ingest'
        )
    ).toBe(false)
  })

  it('requestExecutorRemoval sets pending allocation removal', () => {
    useSettingStore.getState().requestExecutorRemoval('code-default')
    expect(useSettingStore.getState().pendingAllocationRemoval).toBe(
      'code-default'
    )
  })

  it('cancelExecutorRemoval clears pending allocation removal', () => {
    useSettingStore.setState({ pendingAllocationRemoval: 'code-default' })
    useSettingStore.getState().cancelExecutorRemoval()
    expect(useSettingStore.getState().pendingAllocationRemoval).toBeNull()
  })

  it('confirmExecutorRemoval removes allocation, dependent bindings, and node limits only after confirmation', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      originalExecutorConfiguration: initialExecutorConfiguration,
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'code-default',
            workspace_id: 'ws1',
            concurrency_limit: 2,
          },
          {
            executor_id: 'other-executor',
            workspace_id: 'ws1',
            concurrency_limit: 1,
          },
        ],
        bindings: [
          {
            workflow_key: 'question_content',
            node_key: 'ingest',
            executor_id: 'code-default',
          },
          {
            workflow_key: 'question_content',
            node_key: 'parse',
            executor_id: 'other-executor',
          },
        ],
        node_limits: [
          {
            workflow_key: 'question_content',
            node_key: 'ingest',
            concurrency_limit: 1,
          },
          {
            workflow_key: 'question_content',
            node_key: 'parse',
            concurrency_limit: 1,
          },
        ],
        migration_warnings: [],
      },
      pendingAllocationRemoval: 'code-default',
    })
    useSettingStore.getState().confirmExecutorRemoval()
    const state = useSettingStore.getState()
    expect(
      state.executorConfiguration.allocations.some(
        (a) => a.executor_id === 'code-default'
      )
    ).toBe(false)
    expect(
      state.executorConfiguration.bindings.some(
        (b) => b.executor_id === 'code-default'
      )
    ).toBe(false)
    expect(
      state.executorConfiguration.node_limits.some(
        (l) => l.node_key === 'ingest'
      )
    ).toBe(false)
    expect(
      state.executorConfiguration.allocations.some(
        (a) => a.executor_id === 'other-executor'
      )
    ).toBe(true)
    expect(
      state.executorConfiguration.bindings.some(
        (b) => b.executor_id === 'other-executor'
      )
    ).toBe(true)
    expect(
      state.executorConfiguration.node_limits.some(
        (l) => l.node_key === 'parse'
      )
    ).toBe(true)
    expect(state.pendingAllocationRemoval).toBeNull()
  })
})
