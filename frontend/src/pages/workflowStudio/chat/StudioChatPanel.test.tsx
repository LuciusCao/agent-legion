import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { StudioChatPanel } from './StudioChatPanel'
import * as chatApi from './studioChatApi'
import type { StudioChatSessionRecord } from './studioChatApi'
import { compareWorkflowDraft } from '../../../api/workflowDraftCompare'
import { EventSourceMock } from '../../../testing/eventSourceMock'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'
import type { WorkspaceSettings } from '../../../types'

vi.mock('./studioChatApi')
vi.mock('../../../api/workflowDraftCompare', () => ({
  compareWorkflowDraft: vi.fn(),
}))

const mockApi = vi.mocked(chatApi)
const mockCompare = vi.mocked(compareWorkflowDraft)

const baseSettings: WorkspaceSettings = {
  entityType: 'question',
  intakeModes: [],
  labelOverrides: {},
  workflowKey: '',
}

function sessionRecord(
  overrides?: Partial<StudioChatSessionRecord>
): StudioChatSessionRecord {
  return {
    id: 's1',
    workspace_id: 'ws1',
    user_id: 'u1',
    agent_id: 'kimi',
    title: '',
    status: 'idle',
    acp_session_id: null,
    capability_snapshot: {},
    allow_all_permissions: false,
    mcp_status: 'unknown',
    selected_node_key: null,
    error_detail: '',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    closed_at: null,
    ...overrides,
  }
}

function chatMessage(
  id: string,
  seq: number,
  kind: 'text' | 'tool_call' | 'plan' | 'permission' | 'status' | 'thought',
  role: 'user' | 'agent' | 'system',
  content: Record<string, unknown>
) {
  return {
    id,
    session_id: 's1',
    kind,
    role,
    content,
    seq,
    created_at: '2026-01-01T00:00:00Z',
  }
}

function renderPanel(
  overrides?: Partial<Parameters<typeof StudioChatPanel>[0]>
) {
  return render(
    <TestQueryProvider>
      <StudioChatPanel
        onApplyWorkflowDraft={vi.fn()}
        onSelectNode={vi.fn()}
        {...overrides}
      />
    </TestQueryProvider>
  )
}

describe('StudioChatPanel', () => {
  const originalEventSource = globalThis.EventSource

  beforeEach(() => {
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
    vi.clearAllMocks()
    useSettingStore.setState({ workspaceId: 'ws1', settings: baseSettings })
    mockApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi Code' },
    ])
    mockApi.fetchStudioChatSessions.mockResolvedValue([sessionRecord()])
    mockApi.fetchStudioChatMessages.mockResolvedValue([])
    mockApi.updateStudioChatContext.mockResolvedValue(sessionRecord())
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
  })

  it('shows the empty state when no ACP agent is available', async () => {
    mockApi.fetchStudioChatAgents.mockResolvedValue([])
    renderPanel()
    expect(
      await screen.findByText('未检测到可用的 ACP agent，请联系管理员配置')
    ).toBeInTheDocument()
  })

  it('renders pickers, scope note and input for an active session', async () => {
    renderPanel()
    expect(await screen.findByLabelText('选择 Agent')).toBeInTheDocument()
    expect(screen.getByLabelText('选择会话')).toBeInTheDocument()
    expect(screen.getByText(/发布永远由你确认/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('消息输入')).toBeEnabled())
    // 自动打开最近会话并建立 SSE。
    await waitFor(() =>
      expect(mockApi.fetchStudioChatMessages).toHaveBeenCalledWith('ws1', 's1')
    )
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))
  })

  it('renders every message kind', async () => {
    mockApi.fetchStudioChatMessages.mockResolvedValue([
      chatMessage('m1', 1, 'text', 'user', { text: '帮我加个难度评估节点' }),
      chatMessage('m2', 2, 'text', 'agent', { text: '好的，先看 active 版本' }),
      chatMessage('m3', 3, 'tool_call', 'agent', {
        sessionUpdate: 'tool_call',
        toolCallId: 'call-1',
        title: 'get_active_workflow',
        status: 'completed',
        rawInput: { workspace_id: 'ws1' },
        rawOutput: { content: [{ type: 'text', text: '{"version": 6}' }] },
      }),
      chatMessage('m4', 4, 'plan', 'agent', {
        sessionUpdate: 'plan',
        entries: [{ content: '起草新节点', status: 'in_progress' }],
      }),
      chatMessage('m5', 5, 'status', 'system', {
        event: 'mcp_unverified',
        detail: '本会话还没有任何 agent-legion 平台工具调用的迹象',
      }),
    ])
    renderPanel()

    expect(await screen.findByText('帮我加个难度评估节点')).toBeInTheDocument()
    expect(screen.getByText('好的，先看 active 版本')).toBeInTheDocument()
    expect(screen.getByText('get_active_workflow')).toBeInTheDocument()
    expect(screen.getByText('起草新节点')).toBeInTheDocument()
    // mcp_unverified 的文案以后端 detail 为准渲染。
    expect(
      screen.getByText(/本会话还没有任何 agent-legion 平台工具调用的迹象/)
    ).toBeInTheDocument()

    // 工具调用明细默认折叠（rawInput 不可见），点击展开。
    expect(screen.queryByText(/"workspace_id"/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('get_active_workflow'))
    expect(screen.getByText(/"workspace_id"/)).toBeInTheDocument()
    expect(screen.getAllByText('{"version": 6}')).not.toHaveLength(0)
  })

  it('renders agent thought as a collapsed foldable block', async () => {
    mockApi.fetchStudioChatMessages.mockResolvedValue([
      chatMessage('m1', 1, 'thought', 'agent', {
        text: '推理：先读 active 版本',
      }),
      chatMessage('m2', 2, 'text', 'agent', { text: '好的' }),
    ])
    renderPanel()

    const summary = await screen.findByText('思考过程')
    const details = summary.closest('details')
    expect(details).not.toBeNull()
    // 默认折叠，与正文气泡区分。
    expect(details).not.toHaveAttribute('open')
    fireEvent.click(summary)
    expect(details).toHaveAttribute('open')
    expect(screen.getByText('推理：先读 active 版本')).toBeInTheDocument()
  })

  it('marks a pending permission request as a prominent alert', async () => {
    mockApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord({ status: 'awaiting_permission' }),
    ])
    mockApi.fetchStudioChatMessages.mockResolvedValue([
      chatMessage('m1', 1, 'permission', 'agent', {
        request_id: 'r1',
        status: 'pending',
        tool_call: { title: 'Bash' },
        options: [{ optionId: 'o1', name: '允许一次', kind: 'allow_once' }],
      }),
    ])
    renderPanel()

    // pending 权限卡用 role=alert + 「需要你的确认」徽标，避免被忽略。
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('需要你的确认')
    expect(alert).toHaveTextContent('Bash')
  })

  it('answers a permission request inline', async () => {
    mockApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord({ status: 'awaiting_permission' }),
    ])
    mockApi.fetchStudioChatMessages.mockResolvedValue([
      chatMessage('m1', 1, 'permission', 'agent', {
        request_id: 'r1',
        status: 'pending',
        tool_call: { title: 'Bash' },
        options: [{ optionId: 'o1', name: '允许一次', kind: 'allow_once' }],
      }),
    ])
    mockApi.answerStudioChatPermission.mockResolvedValue(undefined)
    renderPanel()

    const allow = await screen.findByRole('button', { name: '允许一次' })
    await act(async () => {
      fireEvent.click(allow)
    })
    expect(mockApi.answerStudioChatPermission).toHaveBeenCalledWith(
      'ws1',
      's1',
      'r1',
      { deny: false, option_id: 'o1' }
    )

    mockApi.setStudioChatAllowAll.mockResolvedValue(
      sessionRecord({
        status: 'awaiting_permission',
        allow_all_permissions: true,
      })
    )
    await act(async () => {
      fireEvent.click(screen.getByRole('checkbox'))
    })
    expect(mockApi.setStudioChatAllowAll).toHaveBeenCalledWith(
      'ws1',
      's1',
      true
    )
  })

  it('applies a workflow draft to the editor and shows the diff dialog', async () => {
    const yaml = 'key: demo_video_workflow\nnodes: []\n'
    mockApi.fetchStudioChatMessages.mockResolvedValue([
      chatMessage('m1', 1, 'tool_call', 'agent', {
        sessionUpdate: 'tool_call',
        toolCallId: 'call-1',
        title: 'validate_workflow',
        status: 'completed',
        rawInput: { workspace_id: 'ws1', definition_yaml: yaml },
        rawOutput: {
          content: [{ type: 'text', text: '{"valid": true, "errors": []}' }],
        },
      }),
    ])
    mockCompare.mockResolvedValue({
      valid: true,
      creates_revision: true,
      base_revision: null,
      draft_workflow: null,
      errors: [],
      summary: {
        risk_level: 'none',
        node_changes: [],
        edge_changes: [],
        intake_changes: [],
        metadata_changes: [],
        risk_flags: [],
      },
    })
    const onApplyWorkflowDraft = vi.fn()
    renderPanel({ onApplyWorkflowDraft })

    const apply = await screen.findByRole('button', { name: '应用到编辑器' })
    expect(screen.getByText(/校验通过/)).toBeInTheDocument()
    fireEvent.click(apply)
    expect(onApplyWorkflowDraft).toHaveBeenCalledWith(yaml)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '查看 diff' }))
    })
    await waitFor(() =>
      expect(mockCompare).toHaveBeenCalledWith('ws1', {
        definition_yaml: yaml,
        allow_missing_baseline: true,
      })
    )
    expect(await screen.findByText('变更摘要')).toBeInTheDocument()
  })

  it('pushes the Studio node selection to the active session context', async () => {
    renderPanel({ selectedNodeKey: 'node-a' })
    // 自动打开最近会话 s1 后，选中节点同步到该会话上下文。
    await waitFor(() =>
      expect(mockApi.updateStudioChatContext).toHaveBeenCalledWith(
        'ws1',
        's1',
        { selectedNodeKey: 'node-a' }
      )
    )
  })

  it('shows cancel while running and disables the input', async () => {
    mockApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord({ status: 'running' }),
    ])
    mockApi.cancelStudioChatTurn.mockResolvedValue(sessionRecord())
    renderPanel()

    const cancel = await screen.findByRole('button', { name: '取消' })
    expect(screen.getByLabelText('消息输入')).toBeDisabled()
    await act(async () => {
      fireEvent.click(cancel)
    })
    expect(mockApi.cancelStudioChatTurn).toHaveBeenCalledWith('ws1', 's1')
  })

  it('sends on Enter and starts a new chat from the picker', async () => {
    mockApi.sendStudioChatMessage.mockResolvedValue(
      chatMessage('u1', 1, 'text', 'user', { text: '你好' })
    )
    renderPanel()

    const input = await screen.findByLabelText('消息输入')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.change(input, { target: { value: '你好' } })
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter', shiftKey: false })
    })
    await waitFor(() =>
      expect(mockApi.sendStudioChatMessage).toHaveBeenCalledWith(
        'ws1',
        's1',
        '你好'
      )
    )

    mockApi.createStudioChatSession.mockResolvedValue(
      sessionRecord({ id: 's2' })
    )
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '＋ 新对话' }))
    })
    await waitFor(() =>
      expect(mockApi.createStudioChatSession).toHaveBeenCalledWith(
        'ws1',
        'kimi'
      )
    )
  })

  it('disables the new-chat button while a session is being created', async () => {
    let resolveCreate: (session: StudioChatSessionRecord) => void = () => {}
    mockApi.createStudioChatSession.mockImplementation(
      () =>
        new Promise<StudioChatSessionRecord>((resolve) => {
          resolveCreate = resolve
        })
    )
    renderPanel()

    // 等 agent 列表到达（否则 selectedAgentId 为空，按钮天然禁用）。
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '＋ 新对话' })).toBeEnabled()
    )
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '＋ 新对话' }))
    })
    // 创建在途：按钮禁用，重复点击不会起第二个 agent 子进程。
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '＋ 新对话' })).toBeDisabled()
    )
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '＋ 新对话' }))
    })
    expect(mockApi.createStudioChatSession).toHaveBeenCalledTimes(1)

    await act(async () => {
      resolveCreate(sessionRecord({ id: 's2' }))
    })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '＋ 新对话' })).toBeEnabled()
    )
  })
})
