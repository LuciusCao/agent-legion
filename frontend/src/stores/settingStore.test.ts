import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useSettingStore } from './settingStore'
import { useUiStore } from './uiStore'
import { api, assignAgent, unassignAgent } from '../api'

vi.mock('../api', () => ({
  api: vi.fn(),
  assignAgent: vi.fn(),
  unassignAgent: vi.fn(),
}))

vi.mock('./uiStore', () => ({
  useUiStore: {
    getState: vi.fn(),
    setState: vi.fn(),
  },
}))

const mockApi = vi.mocked(api)
const mockAssignAgent = vi.mocked(assignAgent)
const mockUnassignAgent = vi.mocked(unassignAgent)
const mockShowToast = vi.fn()
const mockGetState = vi.mocked(useUiStore.getState)

const defaultSettings = {
  entityType: 'question' as const,
  intakeModes: [],
  labelOverrides: {},
  pipelineKey: '',
  agentIds: [],
  concurrencyLimit: 1,
  resources: {},
}

const defaultState = {
  workspaceId: 'ws1',
  workspaceName: '',
  workspaceDescription: '',
  settings: defaultSettings,
  agentAssignments: null as null,
  originalWorkspaceName: '',
  originalWorkspaceDescription: '',
  originalSettings: null as typeof defaultSettings | null,
  originalAgentAssignments: null as null,
  isDirty: false,
  globalServices: null as null,
  resourceProviders: [] as [],
  testStatus: { state: 'idle' as const },
  isSaving: false,
  saveError: null as string | null,
}

describe('settingStore', () => {
  beforeEach(() => {
    useSettingStore.setState(defaultState)
    mockApi.mockReset()
    mockAssignAgent.mockReset()
    mockUnassignAgent.mockReset()
    mockShowToast.mockReset()
    mockGetState.mockReturnValue({ showToast: mockShowToast })
  })

  it('updates settings via setSettings', () => {
    useSettingStore.getState().setSettings({ pipelineKey: 'knowledge_content' })
    expect(useSettingStore.getState().settings.pipelineKey).toBe(
      'knowledge_content'
    )
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
    })
    useSettingStore.getState().setWorkspaceName('New Name')
    expect(useSettingStore.getState().isDirty).toBe(true)
  })

  it('isDirty is true when settings differ from original', () => {
    useSettingStore.setState({
      originalSettings: defaultSettings,
    })
    useSettingStore.getState().setSettings({ pipelineKey: 'knowledge_content' })
    expect(useSettingStore.getState().isDirty).toBe(true)
  })

  it('isDirty is false when values match originals', () => {
    useSettingStore.setState({
      workspaceName: 'Name',
      workspaceDescription: 'Desc',
      originalWorkspaceName: 'Name',
      originalWorkspaceDescription: 'Desc',
      originalSettings: defaultSettings,
      originalAgentAssignments: null,
    })
    useSettingStore.getState().setWorkspaceName('Name')
    expect(useSettingStore.getState().isDirty).toBe(false)
  })

  it('cycles through testConnection states', async () => {
    mockApi.mockResolvedValueOnce({ ok: true, message: 'connected' })
    const promise = useSettingStore.getState().testConnection()
    expect(useSettingStore.getState().testStatus.state).toBe('testing')
    await promise
    expect(useSettingStore.getState().testStatus.state).toBe('success')
    expect(useSettingStore.getState().testStatus.message).toBe('connected')
    expect(mockShowToast).toHaveBeenCalledWith('连接成功', 'success')
  })

  it('sets failed on testConnection error and shows toast', async () => {
    mockApi.mockRejectedValueOnce(new Error('network error'))
    await useSettingStore.getState().testConnection()
    expect(useSettingStore.getState().testStatus.state).toBe('failed')
    expect(useSettingStore.getState().testStatus.message).toBe('network error')
    expect(mockShowToast).toHaveBeenCalledWith(
      '连接测试失败：network error',
      'error'
    )
  })

  it('saveAll calls PATCH endpoints and shows success toast for non-video-hive', async () => {
    mockApi.mockResolvedValue(undefined)
    useSettingStore.setState({
      workspaceName: 'Test',
      workspaceDescription: 'Desc',
      originalWorkspaceName: '',
      originalWorkspaceDescription: '',
      originalSettings: defaultSettings,
      settings: {
        ...defaultSettings,
        pipelineKey: 'question_content',
        intakeModes: ['direct_ids'],
        resources: { question_detail: { enabled: true, config: {} } },
      },
      agentAssignments: [{ agent_id: 'agent-1', concurrency_limit: 2 }],
      originalAgentAssignments: [],
    })
    await useSettingStore.getState().saveAll()
    expect(mockApi).toHaveBeenCalledWith(
      '/api/workspaces/ws1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ name: 'Test', description: 'Desc' }),
      })
    )
    expect(mockApi).toHaveBeenCalledWith(
      '/api/workspaces/ws1',
      expect.objectContaining({
        method: 'PATCH',
        body: expect.stringContaining('resource_config'),
      })
    )
    expect(mockApi).toHaveBeenCalledWith(
      '/api/workspaces/ws1/settings/pipeline',
      expect.objectContaining({
        method: 'PATCH',
        body: expect.stringContaining('pipelineKey'),
      })
    )
    expect(mockAssignAgent).toHaveBeenCalledWith('ws1', 'agent-1', 2)
    expect(mockShowToast).toHaveBeenCalledWith('设置已保存', 'success')
  })

  it('saveAll assigns agents for video-hive workspace', async () => {
    mockApi.mockResolvedValue(undefined)
    mockAssignAgent.mockResolvedValue({
      agent_id: 'agent-1',
      workspace_id: 'video-hive',
      concurrency_limit: 2,
    })
    useSettingStore.setState({
      workspaceId: 'video-hive',
      workspaceName: 'Test',
      workspaceDescription: 'Desc',
      originalWorkspaceName: '',
      originalWorkspaceDescription: '',
      originalSettings: defaultSettings,
      settings: {
        ...defaultSettings,
        pipelineKey: 'question_content',
        intakeModes: ['direct_ids'],
        resources: { question_detail: { enabled: true, config: {} } },
      },
      agentAssignments: [{ agent_id: 'agent-1', concurrency_limit: 2 }],
      originalAgentAssignments: [],
    })
    await useSettingStore.getState().saveAll()
    expect(mockAssignAgent).toHaveBeenCalledWith('video-hive', 'agent-1', 2)
    expect(mockShowToast).toHaveBeenCalledWith('设置已保存', 'success')
  })

  it('saveAll unassigns removed agents for video-hive workspace', async () => {
    mockApi.mockResolvedValue(undefined)
    mockUnassignAgent.mockResolvedValue({
      agent_id: 'agent-1',
      workspace_id: 'video-hive',
      removed: true,
    })
    useSettingStore.setState({
      workspaceId: 'video-hive',
      originalSettings: defaultSettings,
      settings: defaultSettings,
      agentAssignments: [],
      originalAgentAssignments: [{ agent_id: 'agent-1', concurrency_limit: 2 }],
    })
    await useSettingStore.getState().saveAll()
    expect(mockUnassignAgent).toHaveBeenCalledWith('video-hive', 'agent-1')
  })

  it('saveAll surfaces errors and shows error toast', async () => {
    const err = Object.assign(new Error('Server Error'), { status: 500 })
    mockApi.mockRejectedValueOnce(err)
    await useSettingStore.getState().saveAll()
    expect(useSettingStore.getState().isSaving).toBe(false)
    expect(useSettingStore.getState().saveError).toBe('Server Error')
    expect(mockShowToast).toHaveBeenCalledWith('Server Error', 'error')
  })

  it('fetchSettings hydrates settings and workspace name from API', async () => {
    mockApi.mockImplementation((path: string) => {
      if (path === '/api/workspaces/ws1') {
        return Promise.resolve({
          workspace: { name: 'Test Workspace', description: 'A workspace' },
        })
      }
      if (path === '/api/workspaces/ws1/settings') {
        return Promise.resolve({
          entityType: 'knowledge',
          intakeModes: ['direct_ids'],
          labelOverrides: { direct_ids: '输入 ID' },
          pipelineKey: 'knowledge_content',
          resources: {
            question_detail: { enabled: true, config: { bank_version: 'v5' } },
          },
        })
      }
      return Promise.resolve({})
    })
    await useSettingStore.getState().fetchSettings('ws1')
    const state = useSettingStore.getState()
    expect(state.settings.entityType).toBe('knowledge')
    expect(state.settings.intakeModes).toEqual(['direct_ids'])
    expect(state.settings.labelOverrides).toEqual({ direct_ids: '输入 ID' })
    expect(state.settings.pipelineKey).toBe('knowledge_content')
    expect(state.settings.resources).toEqual({
      question_detail: { enabled: true, config: { bank_version: 'v5' } },
    })
    expect(state.workspaceName).toBe('Test Workspace')
    expect(state.workspaceDescription).toBe('A workspace')
    expect(state.originalWorkspaceName).toBe('Test Workspace')
    expect(state.originalSettings).toEqual(state.settings)
    expect(state.isDirty).toBe(false)
  })

  it('fetchSettings keeps defaults on 404', async () => {
    const err = Object.assign(new Error('Not Found'), { status: 404 })
    mockApi.mockRejectedValueOnce(err)
    await useSettingStore.getState().fetchSettings('ws1')
    expect(useSettingStore.getState().settings).toEqual(defaultState.settings)
    expect(useSettingStore.getState().saveError).toBeNull()
  })

  it('fetchSettings keeps defaults on empty response', async () => {
    mockApi.mockResolvedValue({})
    await useSettingStore.getState().fetchSettings('ws1')
    expect(useSettingStore.getState().settings).toEqual(defaultState.settings)
  })

  it('fetchAgentAssignments hydrates agent assignments', async () => {
    mockApi.mockResolvedValueOnce({
      agents: [{ agent_id: 'agent-1', concurrency_limit: 2 }],
    })
    await useSettingStore.getState().fetchAgentAssignments('ws1')
    const state = useSettingStore.getState()
    expect(state.agentAssignments).toEqual([
      { agent_id: 'agent-1', concurrency_limit: 2 },
    ])
    expect(state.originalAgentAssignments).toEqual([
      { agent_id: 'agent-1', concurrency_limit: 2 },
    ])
  })

  it('fetchGlobalServices hydrates global services from API', async () => {
    mockApi.mockResolvedValueOnce({
      cms: {
        url: 'http://cms.example.com',
        tokenConfigured: true,
        env: 'prod',
        healthy: null,
        lastCheckedAt: null,
      },
    })
    await useSettingStore.getState().fetchGlobalServices()
    expect(useSettingStore.getState().globalServices).toEqual({
      cms: {
        url: 'http://cms.example.com',
        tokenConfigured: true,
        env: 'prod',
        healthy: null,
        lastCheckedAt: null,
      },
    })
  })

  it('fetchResourceProviders hydrates providers from API', async () => {
    mockApi.mockResolvedValueOnce({
      providers: [
        {
          key: 'question_detail',
          provider: 'cms.question.detail',
          apiUrl: 'http://api.example.com',
          defaultParams: { bank_version: 'v5' },
          paramKeys: ['bank_version', 'country_id'],
        },
      ],
    })
    await useSettingStore.getState().fetchResourceProviders()
    expect(useSettingStore.getState().resourceProviders).toEqual([
      {
        key: 'question_detail',
        provider: 'cms.question.detail',
        apiUrl: 'http://api.example.com',
        defaultParams: { bank_version: 'v5' },
        paramKeys: ['bank_version', 'country_id'],
      },
    ])
  })
})
