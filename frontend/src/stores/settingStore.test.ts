import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useSettingStore } from './settingStore'
import type { SettingState } from './settingStore'
import { useUiStore } from './uiStore'
import { createMockUiState } from '../testing/fixtures'
import { api } from '../api'
import type { WorkspaceSettings } from '../types'
import type { WorkspaceExecutionConfiguration } from '../types/agentCatalogTypes'

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

// P-0.5：执行配置只剩节点并发上限 + Agent 容量。
const initialExecutionConfiguration: WorkspaceExecutionConfiguration = {
  node_limits: [
    {
      workflow_key: 'question_content',
      node_key: 'ingest',
      concurrency_limit: 1,
    },
  ],
  migration_warnings: [],
  agent_capacity: null,
}

const emptyExecutionConfiguration: WorkspaceExecutionConfiguration = {
  node_limits: [],
  migration_warnings: [],
  agent_capacity: null,
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
  executionConfiguration: initialExecutionConfiguration,
  originalExecutionConfiguration: null,
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
      executionConfiguration: initialExecutionConfiguration,
      originalSettings: defaultSettings,
      originalExecutionConfiguration: initialExecutionConfiguration,
    })

    useSettingStore.getState().setSettings({ workflowKey: 'legacy_workflow' })

    const state = useSettingStore.getState()
    expect(state.executionConfiguration.node_limits).toEqual([])
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
      originalExecutionConfiguration: initialExecutionConfiguration,
    })
    useSettingStore.getState().setWorkspaceName('New Name')
    expect(useSettingStore.getState().isDirty).toBe(true)
  })

  it('isDirty is true when settings differ from original', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      originalExecutionConfiguration: initialExecutionConfiguration,
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
      originalExecutionConfiguration: initialExecutionConfiguration,
      executionConfiguration: initialExecutionConfiguration,
    })
    useSettingStore.getState().setWorkspaceName('Name')
    expect(useSettingStore.getState().isDirty).toBe(false)
  })

  it('isDirty is true when node limit differs from original', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      originalExecutionConfiguration: initialExecutionConfiguration,
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
      executionConfiguration: {
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
    expect(state.executionConfiguration.agent_capacity).toBe(7)
    expect(state.originalExecutionConfiguration).toEqual(
      state.executionConfiguration
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
      executionConfiguration: initialExecutionConfiguration,
    })
    expect(useSettingStore.getState().isDirty).toBe(false)
  })

  it('setAgentCapacity updates executionConfiguration and marks dirty', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      executionConfiguration: emptyExecutionConfiguration,
      originalExecutionConfiguration: emptyExecutionConfiguration,
    })

    useSettingStore.getState().setAgentCapacity(5)

    const state = useSettingStore.getState()
    expect(state.executionConfiguration.agent_capacity).toBe(5)
    expect(state.isDirty).toBe(true)
  })

  it('saveAll sends agent_capacity only when it is set', async () => {
    mockApi.mockResolvedValue({
      workspace: { name: 'Test', description: '' },
      settings: defaultSettings,
      execution_configuration: {
        node_limits: [],
        migration_warnings: [],
        agent_capacity: 6,
      },
    })
    useSettingStore.setState({
      executionConfiguration: {
        node_limits: [],
        migration_warnings: [],
        agent_capacity: 6,
      },
    })

    await useSettingStore.getState().saveAll()
    let body = JSON.parse(mockApi.mock.calls[0][1]?.body as string)
    expect(body.agent_capacity).toBe(6)
    expect(
      useSettingStore.getState().executionConfiguration.agent_capacity
    ).toBe(6)

    mockApi.mockClear()
    mockApi.mockResolvedValue({
      workspace: { name: 'Test', description: '' },
      settings: defaultSettings,
      execution_configuration: emptyExecutionConfiguration,
    })
    useSettingStore.setState({
      executionConfiguration: emptyExecutionConfiguration,
    })

    await useSettingStore.getState().saveAll()
    body = JSON.parse(mockApi.mock.calls[0][1]?.body as string)
    expect('agent_capacity' in body).toBe(false)
  })

  it('saveAll sends exactly one PUT body containing node_limits', async () => {
    mockApi.mockResolvedValue({
      workspace: { name: 'Test', description: 'Desc' },
      settings: {
        ...defaultSettings,
        workflowKey: 'question_content',
        intakeModes: ['direct_ids'],
      },
      execution_configuration: {
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
      originalExecutionConfiguration: emptyExecutionConfiguration,
      executionConfiguration: {
        node_limits: [
          {
            workflow_key: 'question_content',
            node_key: 'ingest',
            concurrency_limit: 2,
          },
        ],
        migration_warnings: [],
        agent_capacity: null,
      },
    })
    await useSettingStore.getState().saveAll()
    expect(mockApi).toHaveBeenCalledTimes(1)
    expect(mockApi).toHaveBeenCalledWith(
      '/api/workspaces/ws1/configuration',
      expect.objectContaining({
        method: 'PUT',
        body: expect.stringContaining('"node_limits"'),
      })
    )
    const body = JSON.parse(mockApi.mock.calls[0][1]?.body as string)
    expect(body).toMatchObject({
      name: 'Test',
      description: 'Desc',
      node_limits: [
        {
          workflow_key: 'question_content',
          node_key: 'ingest',
          concurrency_limit: 2,
        },
      ],
    })
    expect('executor_allocations' in body).toBe(false)
    expect('node_bindings' in body).toBe(false)
    expect(mockShowToast).toHaveBeenCalledWith('设置已保存', 'success')
  })

  it('saveAll replaces original snapshots from the response', async () => {
    const responseConfiguration: WorkspaceExecutionConfiguration = {
      node_limits: [],
      migration_warnings: [],
      agent_capacity: null,
    }
    mockApi.mockResolvedValue({
      workspace: { name: 'Saved', description: 'Saved Desc' },
      settings: {
        ...defaultSettings,
        workflowKey: 'question_content',
      },
      execution_configuration: responseConfiguration,
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
      originalExecutionConfiguration: emptyExecutionConfiguration,
      executionConfiguration: emptyExecutionConfiguration,
    })
    await useSettingStore.getState().saveAll()
    const state = useSettingStore.getState()
    expect(state.workspaceName).toBe('Saved')
    expect(state.originalWorkspaceName).toBe('Saved')
    expect(state.originalExecutionConfiguration).toEqual(responseConfiguration)
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
      execution_configuration: emptyExecutionConfiguration,
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
      execution_configuration: emptyExecutionConfiguration,
    })
    await expect(first).resolves.toBe(true)
    expect(useSettingStore.getState().isSaving).toBe(false)
  })

  it('setNodeLimit updates or creates a node limit', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      originalExecutionConfiguration: initialExecutionConfiguration,
    })
    useSettingStore.getState().setNodeLimit('question_content', 'ingest', 3)
    const limit = useSettingStore
      .getState()
      .executionConfiguration.node_limits.find(
        (l) => l.workflow_key === 'question_content' && l.node_key === 'ingest'
      )
    expect(limit?.concurrency_limit).toBe(3)
  })

  it('setNodeLimit with null removes a node limit', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
      originalExecutionConfiguration: initialExecutionConfiguration,
    })
    useSettingStore.getState().setNodeLimit('question_content', 'ingest', null)
    expect(
      useSettingStore
        .getState()
        .executionConfiguration.node_limits.some(
          (l) =>
            l.workflow_key === 'question_content' && l.node_key === 'ingest'
        )
    ).toBe(false)
  })
})
