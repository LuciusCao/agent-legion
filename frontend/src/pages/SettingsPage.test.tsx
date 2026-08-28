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
import type { WorkspaceSettings, WorkflowDefinitionRecord } from '../types'
import { useUiStore } from '../stores/uiStore'
import { api, deleteWorkspace } from '../api'
import { expectConsoleWarning } from '../test-setup'
import { useSettingStoreHydration } from '../hooks/useWorkspaceSettingsQuery'
import type { WorkspaceSettingsSnapshot } from '../hooks/useWorkspaceSettingsQuery'

// 服务端快照（agentRoutes）与工作流定义已迁入 react-query；
// 本测试 mock 两个 query hook，draft 状态仍直接写 store。
const mockQueryData = vi.hoisted(() => ({
  settingsSnapshot: { current: null as WorkspaceSettingsSnapshot | null },
  workflowDefinition: { current: null as WorkflowDefinitionRecord | null },
}))

vi.mock('../hooks/useWorkspaceSettingsQuery', () => ({
  useSettingStoreHydration: vi.fn(() => ({
    data: mockQueryData.settingsSnapshot.current,
    error: null,
  })),
  useWorkspaceSettingsQuery: vi.fn(() => ({
    data: mockQueryData.settingsSnapshot.current,
  })),
  useWorkspaceSettingsSnapshot: vi.fn(() => ({
    workflowDefinition: mockQueryData.workflowDefinition.current,
    agentRoutes: mockQueryData.settingsSnapshot.current?.agentRoutes ?? [],
  })),
}))

vi.mock('../hooks/useWorkflowDefinitionQuery', () => ({
  useWorkflowDefinitionQuery: vi.fn(() => ({
    data: mockQueryData.workflowDefinition.current,
  })),
}))

vi.mock('../api', () => ({
  api: vi.fn(),
  fetchWorkspaces: vi.fn(),
  deleteWorkspace: vi.fn(),
  listRegisterTokens: vi.fn().mockResolvedValue([]),
  listAgentWorkers: vi.fn().mockResolvedValue([]),
  createRegisterToken: vi.fn(),
  deleteRegisterToken: vi.fn(),
  deleteAgentWorker: vi.fn(),
}))

const mockApi = vi.mocked(api)
const mockDeleteWorkspace = vi.mocked(deleteWorkspace)
const mockHydration = vi.mocked(useSettingStoreHydration)

function setSnapshot(partial: Partial<WorkspaceSettingsSnapshot>) {
  mockQueryData.settingsSnapshot.current = {
    workspaceName: '',
    workspaceDescription: '',
    settings: {
      entityType: 'question',
      intakeModes: [],
      labelOverrides: {},
      workflowKey: '',
    },
    executionConfiguration: {
      node_limits: [],
      migration_warnings: [],
      agent_capacity: null,
    },
    agentRoutes: [],
    ...partial,
  }
}

function setWorkflowDefinition(definition: WorkflowDefinitionRecord) {
  mockQueryData.workflowDefinition.current = definition
}

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
  },
  originalWorkspaceName: '测试空间',
  originalWorkspaceDescription: '测试描述',
  originalSettings: null,
  isDirty: false,
  isSaving: false,
  saveError: null,
  executionConfiguration: {
    node_limits: [],
    migration_warnings: [],
    agent_capacity: null,
  },
  originalExecutionConfiguration: null,
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
  setNodeLimit: vi.fn(),
  setAgentCapacity: vi.fn(),
  hydrateSettings: vi.fn(),
  saveAll: vi.fn().mockResolvedValue(undefined),
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
    mockQueryData.settingsSnapshot.current = null
    mockQueryData.workflowDefinition.current = null
    mockHydration.mockClear()
    mockApi.mockReset()
    mockApi.mockResolvedValue({})
    mockDeleteWorkspace.mockReset()
    mockDeleteWorkspace.mockResolvedValue(undefined)
  })

  it('renders all sections in order', async () => {
    setWorkflowDefinition({
      key: 'question_content',
      label: '题目内容生成',
      intake: {
        modes: [
          {
            key: 'direct_ids',
            label: '直接输入 ID',
            input_field: 'question_ids',
          },
        ],
      },
      edges: [],
      nodes: [
        {
          key: 'fetch_items',
          label: '获取题目',
          capability: 'fetch_items',
          after: [],
          inputs: [],
          outputs: [],
        },
      ],
    })
    setSnapshot({ agentRoutes: [] })
    renderPage()
    await act(async () => {})

    const headings = screen.getAllByRole('heading', { level: 2 })
    expect(headings.map((h) => h.textContent)).toEqual([
      '基本信息',
      '接入与资源',
      'Agent 与 Worker',
      'Agent 默认配置',
      '代码节点并发',
      '危险操作',
    ])
  })

  it('renders nav items matching the sections and marks the active one', async () => {
    renderPage()
    await act(async () => {})

    const nav = screen.getByRole('navigation')
    const navButtons = within(nav).getAllByRole('button')
    expect(navButtons.map((b) => b.textContent)).toEqual([
      '基础信息',
      '接入与资源',
      'Agent 与 Worker',
      'Agent 默认配置',
      '危险操作',
    ])
    expect(navButtons[0]).toHaveAttribute('aria-current', 'true')
    expect(navButtons[1]).not.toHaveAttribute('aria-current')
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

  it('hydrates the setting store through the settings query hook', async () => {
    renderPage()
    await waitFor(() => {
      expect(mockHydration).toHaveBeenCalledWith('ws1')
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

  it('renders checked checkbox for enabled intake modes', async () => {
    setWorkflowDefinition({
      key: 'demo_workflow',
      label: '题目审题信息生成',
      intake: {
        modes: [
          {
            key: 'batch_by_knowledge',
            label: '按知识点批量',
            input_field: 'knowledge_codes',
          },
          {
            key: 'batch_by_ids',
            label: '按题目ID批量',
            input_field: 'question_ids',
          },
        ],
      },
      edges: [],
      nodes: [],
    })
    useSettingStore.setState({
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
    setWorkflowDefinition({
      key: 'demo_workflow',
      label: '题目审题信息生成',
      intake: {
        modes: [
          {
            key: 'batch_by_ids',
            label: '按题目ID批量',
            input_field: 'question_ids',
          },
        ],
      },
      edges: [],
      nodes: [],
    })
    useSettingStore.setState({
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

  it('renders the code node concurrency section for code-pool nodes', async () => {
    // P-0.5：无 Agent 路由的节点一律进 code 池，节点并发区直接列出。
    setWorkflowDefinition({
      key: 'question_content',
      label: '题目内容生成',
      intake: { modes: [] },
      edges: [],
      nodes: [
        {
          key: 'fetch_items',
          label: '获取题目',
          capability: 'fetch_items',
          after: [],
          inputs: [],
          outputs: [],
        },
      ],
    })
    setSnapshot({ agentRoutes: [] })
    renderPage()
    await act(async () => {})

    const headings = screen.getAllByRole('heading')
    const labels = headings.map((h) => h.textContent)
    expect(labels).toContain('代码节点并发')
    expect(labels).not.toContain('执行器')
    expect(labels.indexOf('代码节点并发')).toBeGreaterThan(
      labels.indexOf('Agent 默认配置')
    )
  })

  it('saves node limits in one PUT request', async () => {
    const settings: WorkspaceSettings = {
      entityType: 'question',
      intakeModes: [],
      labelOverrides: {},
      workflowKey: 'sample_workflow',
    }
    setSnapshot({ agentRoutes: [] })
    setWorkflowDefinition({
      key: 'sample_workflow',
      label: '示例工作流',
      intake: { modes: [] },
      edges: [],
      nodes: [
        {
          key: 'fetch_items',
          label: '获取题目',
          capability: 'fetch_items',
          after: [],
          inputs: [],
          outputs: ['questions.json'],
        },
      ],
    })
    useSettingStore.setState({
      ...defaultState,
      workspaceName: 'Flow Workspace',
      originalWorkspaceName: 'Flow Workspace',
      workspaceDescription: '',
      originalWorkspaceDescription: '',
      settings,
      originalSettings: settings,
      executionConfiguration: {
        node_limits: [],
        migration_warnings: [],
        agent_capacity: null,
      },
      originalExecutionConfiguration: {
        node_limits: [],
        migration_warnings: [],
        agent_capacity: null,
      },
      setNodeLimit: originalActions.setNodeLimit,
      saveAll: originalActions.saveAll,
    })

    expectConsoleWarning(/out-of-range value/)

    mockApi.mockResolvedValueOnce({
      workspace: { name: 'Flow Workspace', description: '' },
      settings,
      execution_configuration: {
        node_limits: [
          {
            workflow_key: 'sample_workflow',
            node_key: 'fetch_items',
            concurrency_limit: 2,
          },
        ],
        migration_warnings: [],
      },
    })

    renderPage()
    await act(async () => {})

    const limitInput = (await screen.findByLabelText(
      '获取题目 并发上限'
    )) as HTMLInputElement
    await act(async () => {
      fireEvent.change(limitInput, { target: { value: '2' } })
    })

    await waitFor(() => {
      expect(
        useSettingStore.getState().executionConfiguration.node_limits
      ).toEqual([
        {
          workflow_key: 'sample_workflow',
          node_key: 'fetch_items',
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
      node_limits: [
        {
          workflow_key: 'sample_workflow',
          node_key: 'fetch_items',
          concurrency_limit: 2,
        },
      ],
    })
    expect('executor_allocations' in body).toBe(false)
    expect('node_bindings' in body).toBe(false)
    // Generous timeout: this full-page interaction chain exceeds vitest's
    // 5s default on loaded parallel-gate machines (local gate flake 2026-08-01).
  }, 30000)

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
      expect(mockDeleteWorkspace).toHaveBeenCalledWith('ws1')
    })
    await waitFor(() => {
      expect(screen.getByText('Dashboard')).toBeInTheDocument()
    })
  })

  it('shows error when delete workspace fails', async () => {
    mockDeleteWorkspace.mockRejectedValue(
      new Error('Cannot delete workspace with running jobs')
    )

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
