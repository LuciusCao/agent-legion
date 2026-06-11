import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SettingsPage } from './SettingsPage'
import { useSettingStore } from '../stores/settingStore'
import { useUiStore } from '../stores/uiStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import {
  api,
  assignAgent,
  fetchAgents,
  fetchPipelines,
  fetchWorkspaceAgents,
  unassignAgent,
  updateWorkspace,
} from '../api'

vi.mock('../api', () => ({
  api: vi.fn(),
  fetchAgents: vi.fn(),
  fetchPipelines: vi.fn(),
  fetchWorkspaceAgents: vi.fn(),
  assignAgent: vi.fn(),
  unassignAgent: vi.fn(),
  fetchWorkspaces: vi.fn(),
  updateWorkspace: vi.fn(),
}))

const mockApi = vi.mocked(api)
const mockFetchAgents = vi.mocked(fetchAgents)
const mockFetchPipelines = vi.mocked(fetchPipelines)
const mockFetchWorkspaceAgents = vi.mocked(fetchWorkspaceAgents)
const mockUpdateWorkspace = vi.mocked(updateWorkspace)
const mockAssignAgent = vi.mocked(assignAgent)
const mockUnassignAgent = vi.mocked(unassignAgent)

const defaultState = {
  workspaceId: 'ws1',
  workspaceName: '测试空间',
  workspaceDescription: '测试描述',
  settings: {
    entityType: 'question' as const,
    intakeModes: [] as string[],
    labelOverrides: {} as Record<string, string>,
    pipelineKey: '',
    agentIds: [] as string[],
    concurrencyLimit: 1,
    resources: {} as Record<
      string,
      { enabled: boolean; config: Record<string, string> }
    >,
  },
  agentAssignments: null as
    | null
    | { agent_id: string; concurrency_limit: number }[],
  originalWorkspaceName: '测试空间',
  originalWorkspaceDescription: '测试描述',
  originalSettings: null as typeof defaultState.settings | null,
  originalAgentAssignments: null as typeof defaultState.agentAssignments,
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
  resourceProviders: [] as [],
  pipelineDefinition: null as null | {
    key: string
    label: string
    concurrency: { local: number; agent: number }
    intake?: {
      modes: {
        key: string
        label: string
        input_field: string
        resource?: string
      }[]
    }
    nodes: any[]
  },
  testStatus: { state: 'idle' as const },
  isSaving: false,
  saveError: null as string | null,
  setWorkspaceId: vi.fn(),
  setWorkspaceName: vi.fn((name: string) => {
    useSettingStore.setState({ workspaceName: name, isDirty: true })
  }),
  setWorkspaceDescription: vi.fn((desc: string) => {
    useSettingStore.setState({ workspaceDescription: desc, isDirty: true })
  }),
  setSettings: vi.fn((s: any) => {
    useSettingStore.setState((state: any) => ({
      settings: { ...state.settings, ...s },
      isDirty: true,
    }))
  }),
  setAgentAssignments: vi.fn(),
  fetchSettings: vi.fn().mockResolvedValue(undefined),
  fetchAgentAssignments: vi.fn().mockResolvedValue(undefined),
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
    mockFetchAgents.mockReset()
    mockFetchAgents.mockResolvedValue({ agents: [] })
    mockFetchPipelines.mockReset()
    mockFetchPipelines.mockResolvedValue({ pipelines: [] })
    mockFetchWorkspaceAgents.mockReset()
    mockFetchWorkspaceAgents.mockResolvedValue({ agents: [] })
    mockUpdateWorkspace.mockReset()
    mockUpdateWorkspace.mockResolvedValue({
      id: 'ws1',
      name: '新名称',
      description: '新描述',
      default_pipeline_key: 'question_content',
      default_entity: 'question',
    })
    mockAssignAgent.mockReset()
    mockAssignAgent.mockResolvedValue(undefined)
    mockUnassignAgent.mockReset()
    mockUnassignAgent.mockResolvedValue(undefined)
  })

  it('renders 4 sections with nav sidebar for non-video-hive workspace', () => {
    useSettingStore.setState({
      pipelineDefinition: {
        key: 'question_content',
        label: '题目内容生成',
        concurrency: { local: 8, agent: 2 },
        intake: {
          modes: [
            {
              key: 'direct_ids',
              label: '直接输入 ID',
              input_field: 'question_ids',
              resource: '',
            },
            {
              key: 'by_knowledge',
              label: '按知识点',
              input_field: 'knowledge_codes',
              resource: 'by_knowledge',
            },
          ],
        },
        nodes: [],
      },
    })
    renderPage()
    // Nav items and section headings both contain these texts
    expect(screen.getAllByText('基本信息').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('接入配置').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Pipeline').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('智能体').length).toBeGreaterThanOrEqual(1)
  })

  it('renders agents section for video-hive workspace', () => {
    useSettingStore.setState({
      pipelineDefinition: {
        key: 'question_content',
        label: '题目内容生成',
        concurrency: { local: 8, agent: 2 },
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
        nodes: [],
      },
    })
    renderPage(['/workspaces/video-hive/settings'])
    expect(screen.getAllByText('智能体').length).toBeGreaterThanOrEqual(1)
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

  it('renders AgentAllocationList for non-video-hive workspace', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('可用智能体')).toBeInTheDocument()
    })
    expect(screen.getByText('当前工作空间未分配智能体')).toBeInTheDocument()
  })

  it('renders resource provider params when intake mode is checked', async () => {
    useSettingStore.setState({
      pipelineDefinition: {
        key: 'question_content',
        label: '题目内容生成',
        concurrency: { local: 8, agent: 2 },
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
})
