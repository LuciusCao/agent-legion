import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SettingsPage } from './SettingsPage'
import { useSettingStore } from '../stores/settingStore'
import { useUiStore } from '../stores/uiStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import {
  api,
  assignAgent,
  fetchAgents,
  fetchWorkspaceAgents,
  unassignAgent,
  updateWorkspace,
} from '../api'

vi.mock('../api', () => ({
  api: vi.fn(),
  fetchAgents: vi.fn(),
  fetchWorkspaceAgents: vi.fn(),
  assignAgent: vi.fn(),
  unassignAgent: vi.fn(),
  fetchWorkspaces: vi.fn(),
  updateWorkspace: vi.fn(),
}))

const mockApi = vi.mocked(api)
const mockFetchAgents = vi.mocked(fetchAgents)
const mockFetchWorkspaceAgents = vi.mocked(fetchWorkspaceAgents)
const mockUpdateWorkspace = vi.mocked(updateWorkspace)

const defaultState = {
  workspaceId: 'ws1',
  settings: {
    entityType: 'question' as const,
    intakeModes: [],
    labelOverrides: {},
    pipelineKey: '',
    agentIds: [],
    concurrencyLimit: 1,
    resources: {},
  },
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
  testStatus: { state: 'idle' as const },
  isSaving: false,
  saveError: null as string | null,
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
    vi.mocked(assignAgent).mockReset()
    vi.mocked(unassignAgent).mockReset()
  })

  it('renders all 5 cards', () => {
    renderPage()
    expect(screen.getByText('基本信息')).toBeInTheDocument()
    expect(screen.getByText('全局服务')).toBeInTheDocument()
    expect(screen.getByText('资源接口')).toBeInTheDocument()
    expect(screen.getByText('接入模式')).toBeInTheDocument()
    expect(screen.getAllByText('流水线').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('智能体')).toBeInTheDocument()
  })

  it('renders workspace name in header', () => {
    renderPage()
    expect(screen.getByText('测试空间 / 设置')).toBeInTheDocument()
  })

  it('updates workspace name and description on save', async () => {
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
    const saveBtns = screen.getAllByText('保存')
    fireEvent.click(saveBtns[0])
    await waitFor(() => {
      expect(mockUpdateWorkspace).toHaveBeenCalledWith('ws1', {
        name: '新名称',
        description: '新描述',
      })
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
    renderPage()
    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith('/api/workspaces/ws1/settings')
    })
  })

  it('calls fetchGlobalServices and fetchResourceProviders on mount', async () => {
    renderPage()
    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith('/api/global-services')
    })
    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith('/api/resource-providers')
    })
  })

  it('calls test connection and shows status change', async () => {
    renderPage()
    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith('/api/workspaces/ws1/settings')
    })
    mockApi.mockResolvedValueOnce({ ok: true, message: 'ok' })
    const btn = screen.getByText('测试连接')
    fireEvent.click(btn)
    await waitFor(() => {
      const successBadge = document.querySelector('.status-badge.success')
      expect(successBadge).toBeInTheDocument()
      expect(successBadge?.textContent).toContain('连接成功')
    })
    expect(mockApi).toHaveBeenCalledWith(
      '/api/workspaces/ws1/settings/test-connection',
      expect.objectContaining({ method: 'POST' })
    )
  })

  it('shows failed status and toast on test connection failure', async () => {
    renderPage()
    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith('/api/workspaces/ws1/settings')
    })
    mockApi.mockRejectedValueOnce(new Error('connection refused'))
    const btn = screen.getByText('测试连接')
    fireEvent.click(btn)
    await waitFor(() => {
      const failedBadge = document.querySelector('.status-badge.failed')
      expect(failedBadge).toBeInTheDocument()
      expect(failedBadge?.textContent).toContain('连接失败')
    })
    expect(useUiStore.getState().toast).toEqual({
      message: '连接测试失败：connection refused',
      type: 'error',
    })
  })

  it('calls saveSection when resources save is clicked', async () => {
    useSettingStore.setState({
      settings: {
        ...defaultState.settings,
        resources: {
          question_detail: { enabled: true, config: { bank_version: 'v5' } },
        },
      },
    })
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('测试连接')).toBeInTheDocument()
    })
    const resourceCard = screen
      .getByText('资源接口')
      .closest('.card-outlined') as HTMLElement
    const saveBtn = within(resourceCard).getByText('保存')
    fireEvent.click(saveBtn)
    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith(
        '/api/workspaces/ws1/settings/resources',
        expect.objectContaining({
          method: 'PATCH',
          body: JSON.stringify({
            resources: {
              question_detail: {
                enabled: true,
                config: { bank_version: 'v5' },
              },
            },
          }),
        })
      )
    })
  })

  it('displays save error when saveSection fails', async () => {
    useSettingStore.setState({
      settings: {
        ...defaultState.settings,
        resources: {
          question_detail: { enabled: true, config: {} },
        },
      },
    })
    renderPage()
    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith('/api/workspaces/ws1/settings')
    })
    mockApi.mockRejectedValueOnce(
      Object.assign(new Error('Server Error'), { status: 500 })
    )
    const resourceCard = screen
      .getByText('资源接口')
      .closest('.card-outlined') as HTMLElement
    const saveBtn = within(resourceCard).getByText('保存')
    fireEvent.click(saveBtn)
    await waitFor(() => {
      expect(within(resourceCard).getByText('Server Error')).toBeInTheDocument()
    })
  })

  it('updates labelOverrides state when textarea input is valid JSON', () => {
    renderPage()
    const textarea = document.querySelector(
      'md-outlined-text-field[label="标签覆盖 (JSON)"]'
    ) as HTMLElement
    expect(textarea).toBeTruthy()
    ;(textarea as HTMLInputElement).value = '{"direct_ids":"输入 ID"}'
    fireEvent.input(textarea)
    expect(useSettingStore.getState().settings.labelOverrides).toEqual({
      direct_ids: '输入 ID',
    })
  })

  it('renders AgentAllocationList instead of placeholder in agents card', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('可用智能体')).toBeInTheDocument()
    })
    expect(
      screen.queryByText(/智能体配置将在后续步骤实现/)
    ).not.toBeInTheDocument()
    expect(screen.getByText('当前工作空间未分配智能体')).toBeInTheDocument()
  })

  it('renders global services card when data is available', async () => {
    useSettingStore.setState({
      globalServices: {
        cms: {
          baseUrl: 'http://cms.example.com',
          tokenConfigured: true,
          env: 'prod',
          healthy: null,
          lastCheckedAt: null,
        },
      },
    })
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('http://cms.example.com')).toBeInTheDocument()
    })
    expect(screen.getByText('已配置')).toBeInTheDocument()
    expect(screen.getByText('prod')).toBeInTheDocument()
  })

  it('renders resource providers with checkboxes and inputs', async () => {
    useSettingStore.setState({
      resourceProviders: [
        {
          key: 'question_detail',
          provider: 'cms.question.detail',
          path: '/question/detail',
          defaultParams: { bank_version: 'v5' },
          paramKeys: ['bank_version', 'country_id'],
        },
      ],
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
