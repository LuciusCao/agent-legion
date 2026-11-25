import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { StudioChatPanel } from './StudioChatPanel'
import * as chatApi from './studioChatApi'
import type { StudioChatSessionRecord } from './studioChatApi'
import { EventSourceMock } from '../../../testing/eventSourceMock'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'
import type { WorkspaceSettings } from '../../../types'

vi.mock('./studioChatApi')
vi.mock('../../../api/workflowDraftCompare', () => ({
  compareWorkflowDraft: vi.fn(),
}))

const mockApi = vi.mocked(chatApi)

const baseSettings: WorkspaceSettings = {
  entityType: 'question',
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
    compacting: false,
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
  kind:
    | 'text'
    | 'tool_call'
    | 'plan'
    | 'permission'
    | 'status'
    | 'thought'
    | 'draft',
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

  it('marks a pending permission request as a prominent alert', async () => {
    mockApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord({ status: 'awaiting_permission' }),
    ])
    mockApi.fetchStudioChatMessages.mockResolvedValue([
      chatMessage('m1', 1, 'permission', 'agent', {
        request_id: 'r1',
        status: 'pending',
        tool_call: { title: 'Bash', rawInput: { command: 'rm -rf build' } },
        options: [{ optionId: 'o1', name: '允许一次', kind: 'allow_once' }],
      }),
    ])
    renderPanel()

    // pending 权限卡用 role=alert + 「需要你的确认」徽标，避免被忽略。
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('需要你的确认')
    expect(alert).toHaveTextContent('Bash')
    // 授权绑定的完整命令随卡展示（#954）：人批准的就是 terminal 要跑的那条。
    expect(
      screen.getByLabelText('完整命令 · 仅在授权围栏开启时精确绑定')
    ).toHaveTextContent('rm -rf build')
    expect(alert).toHaveTextContent('命令已精确绑定')
    expect(alert).toHaveTextContent('你批准的就是上方完整命令')
  })

  it('distinguishes unbound, late-bound and non-terminal permission cards', async () => {
    mockApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord({ status: 'awaiting_permission' }),
    ])
    const request = (
      id: string,
      seq: number,
      toolCall: Record<string, unknown>
    ) =>
      chatMessage(id, seq, 'permission', 'agent', {
        request_id: id,
        status: 'pending',
        tool_call: toolCall,
        options: [{ optionId: 'allow', name: '允许一次', kind: 'allow_once' }],
      })
    const allowed = (id: string, seq: number) =>
      chatMessage(`${id}-resolved`, seq, 'permission', 'user', {
        request_id: id,
        status: 'resolved',
        decision: { option_id: 'allow' },
      })
    mockApi.fetchStudioChatMessages.mockResolvedValue([
      request('r-pending', 1, {
        toolCallId: 'main-pending',
        title: 'Bash',
        content: 'Requesting approval to Running: git diff…',
      }),
      request('r-late', 2, {
        toolCallId: 'main-late',
        title: 'Bash',
        content: 'Requesting approval to Running: git status…',
      }),
      allowed('r-late', 3),
      chatMessage('late-started', 4, 'tool_call', 'agent', {
        toolCallId: 'main-late',
        title: 'Bash',
        kind: 'execute',
        rawInput: { command: 'git status --short' },
      }),
      request('r-child', 5, {
        toolCallId: 'sub-call',
        title: 'Bash',
        content: 'Requesting approval to Running: npm test…',
      }),
      allowed('r-child', 6),
      request('r-read', 7, { title: 'Read', kind: 'read' }),
    ])
    renderPanel()

    const pendingSummary = await screen.findByLabelText(
      '权限请求携带的截断摘要 · 不是完整命令'
    )
    const pendingCard = pendingSummary.closest('[role="alert"]') as HTMLElement
    expect(pendingCard).toHaveTextContent('命令未绑定 · 仅显示截断摘要')
    expect(
      within(pendingCard).getByRole('button', { name: '允许一次（未绑定）' })
    ).toBeInTheDocument()
    expect(pendingSummary).toHaveTextContent(
      'Requesting approval to Running: git diff…'
    )

    const lateCommand = screen.getByLabelText(
      '批准后取得 · 仅在授权围栏开启时绑定的完整命令'
    )
    expect(lateCommand).toHaveTextContent('git status --short')
    const lateCard = lateCommand.closest('[role="group"]') as HTMLElement
    expect(lateCard).toHaveTextContent('已允许 · 批准后已晚绑定')
    expect(lateCard).toHaveTextContent(
      'Requesting approval to Running: git status…'
    )

    const childSummary = screen.getByText(
      /Requesting approval to Running: npm test…/
    )
    const childCard = childSummary.closest('[role="group"]') as HTMLElement
    expect(childCard).toHaveTextContent('已允许 · 命令仍未绑定')
    expect(childCard).toHaveTextContent('没有可展示的完整命令')
    expect(
      within(childCard).queryByLabelText(
        '批准后取得 · 仅在授权围栏开启时绑定的完整命令'
      )
    ).not.toBeInTheDocument()

    const readCard = screen
      .getByText('Read')
      .closest('[role="alert"]') as HTMLElement
    expect(readCard).not.toHaveTextContent(/命令未绑定|仅显示截断摘要/)
  })

  it('answers only after the session reaches awaiting_permission', async () => {
    mockApi.fetchStudioChatSessions.mockResolvedValue([sessionRecord()])
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

    const allow = await screen.findByRole('button', {
      name: '允许一次（未绑定）',
    })
    expect(allow).toBeDisabled()
    await act(async () => {
      fireEvent.click(allow)
    })
    expect(mockApi.answerStudioChatPermission).not.toHaveBeenCalled()

    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))
    act(() => {
      EventSourceMock.instances[0].emitMessage({
        type: 'session',
        session: sessionRecord({ status: 'awaiting_permission' }),
      })
    })
    await waitFor(() => expect(allow).toBeEnabled())
    await act(async () => {
      fireEvent.click(allow)
    })
    expect(mockApi.answerStudioChatPermission).toHaveBeenCalledWith(
      'ws1',
      's1',
      'r1',
      { deny: false, option_id: 'o1' }
    )
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

    const allow = await screen.findByRole('button', {
      name: '允许一次（未绑定）',
    })
    // 权限消息可能先于会话快照渲染；出现按钮不等于已经可回答。
    await waitFor(() => expect(allow).toBeEnabled())
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
})
