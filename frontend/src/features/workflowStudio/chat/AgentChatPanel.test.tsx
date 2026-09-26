import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AgentChatPanel } from './AgentChatPanel'
import type { StudioChat } from './useStudioChat'
import type { StudioChatSessionRecord } from './studioChatApi'
import styles from './AgentChatPanel.module.css'

vi.mock('./studioChatConfigApi')

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

function fakeChat(overrides?: Record<string, unknown>): StudioChat {
  return {
    agents: [{ id: 'kimi', label: 'Kimi' }],
    agentsLoading: false,
    agentsError: false,
    sessions: [sessionRecord()],
    activeSessionId: 's1',
    session: sessionRecord(),
    messages: [],
    toolCalls: [],
    workflowDraft: null,
    agentDrafts: [],
    nodeDrafts: [],
    permissions: [],
    busy: false,
    closed: false,
    starting: false,
    actionError: null,
    lastRunMs: null,
    lastTerminalEvent: null,
    resume: vi.fn(),
    resuming: false,
    selectSession: vi.fn(),
    startSession: vi.fn(),
    send: vi.fn().mockResolvedValue(true),
    cancel: vi.fn(),
    setAllowAll: vi.fn(),
    answerPermission: vi.fn(),
    ...overrides,
  } as unknown as StudioChat
}

function renderPanel(
  chatOverrides?: Record<string, unknown>,
  props?: Partial<Parameters<typeof AgentChatPanel>[0]>
) {
  return render(
    <AgentChatPanel
      chat={fakeChat(chatOverrides)}
      workspaceId="ws1"
      emptyState="选择 Agent，点「＋ 新对话」开始"
      noSessionReason="先选择会话或新建对话"
      closedReason="会话已关闭或中断，点「继续对话」恢复"
      onApplyWorkflowDraft={vi.fn()}
      {...props}
    />
  )
}

describe('AgentChatPanel', () => {
  it('renders the header / actionArea slots', () => {
    renderPanel(undefined, {
      header: <div>头部插槽</div>,
      actionArea: <div>动作插槽</div>,
    })
    expect(screen.getByText('头部插槽')).toBeInTheDocument()
    expect(screen.getByText('动作插槽')).toBeInTheDocument()
  })

  it('renders composer config chips only with showAgentConfig (#695 R4)', () => {
    const configured = sessionRecord({
      capability_snapshot: { sessionModes: true },
      session_modes: {
        currentModeId: 'default',
        availableModes: [{ id: 'default', name: 'Default' }],
      },
    })
    const { unmount } = renderPanel(
      { session: configured },
      { showAgentConfig: true }
    )
    expect(
      screen.getByRole('button', { name: 'Agent 权限模式' })
    ).toBeInTheDocument()
    unmount()
    renderPanel({ session: configured })
    expect(
      screen.queryByRole('button', { name: 'Agent 权限模式' })
    ).not.toBeInTheDocument()
  })

  it('shows the empty state instead of the message list without a session', () => {
    renderPanel({ activeSessionId: null, session: null })
    expect(
      screen.getByText('选择 Agent，点「＋ 新对话」开始')
    ).toBeInTheDocument()
    expect(screen.getByLabelText('消息输入')).toBeDisabled()
  })

  it('renders actionError as a red statusError banner by default', () => {
    renderPanel({ actionError: '发送失败' })
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('发送失败')
    expect(alert.className).toBe(styles.statusError)
  })

  it('renders actionError as an amber statusWarning banner with warning tone', () => {
    renderPanel({ actionError: '操作失败' }, { actionErrorTone: 'warning' })
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('操作失败')
    expect(alert.className).toBe(styles.statusWarning)
  })

  it('shows the bootstrap error with a retry button and suppresses actionError', () => {
    const onRetry = vi.fn()
    renderPanel(
      { actionError: '会话创建失败：boom' },
      {
        bootstrapError: { message: '会话创建失败：boom', onRetry },
      }
    )
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('会话创建失败：boom')
    expect(alert.className).toBe(styles.statusWarning)
    // bootstrap 条展示期间压制 actionError（同一错误的两个呈现只留一个）。
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('disables the input while the session is compacting (#694)', () => {
    renderPanel({ session: sessionRecord({ compacting: true }) })
    const input = screen.getByLabelText('消息输入')
    expect(input).toBeDisabled()
    expect(input).toHaveAttribute(
      'placeholder',
      '正在压缩上下文，完成后即可发送'
    )
    // 压缩提示迁入 composer 工具行的上下文圆环（脉冲 + hover 精确文案），
    // 不再是 strip 里的独立文本。
    expect(screen.getByLabelText('上下文用量')).toBeInTheDocument()
  })

  it('shows the resume bar for a closed session', () => {
    renderPanel({
      session: sessionRecord({ status: 'closed' }),
      closed: true,
    })
    expect(screen.getByRole('button', { name: '继续对话' })).toBeInTheDocument()
    expect(screen.getByLabelText('消息输入')).toBeDisabled()
  })

  it('keeps the run status strip (with cancel) outside the composer card (#787)', () => {
    renderPanel({ busy: true, session: sessionRecord({ status: 'running' }) })
    const strip = screen.getByLabelText('会话状态条')
    const input = screen.getByLabelText('消息输入')
    const card = input.parentElement!
    // 取消是破坏性动作：状态行独立成行于输入卡片外，文档序在输入区之前。
    expect(card.contains(strip)).toBe(false)
    expect(
      strip.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument()
  })

  it('queues the message instead of sending directly while busy', async () => {
    const send = vi.fn().mockResolvedValue(true)
    renderPanel({
      busy: true,
      session: sessionRecord({ status: 'running' }),
      send,
    })
    const input = screen.getByLabelText('消息输入')
    fireEvent.change(input, { target: { value: '排队消息' } })
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' })
    })
    expect(send).not.toHaveBeenCalled()
    expect(screen.getAllByText('排队中 1')[0]).toBeInTheDocument()
    expect(screen.getByText('排队消息')).toBeInTheDocument()
  })
})
