import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AgentChatStatusStrip } from './AgentChatStatusStrip'
import type { StudioChat } from './useStudioChat'
import type { StudioChatSessionRecord } from './studioChatApi'

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
    session: sessionRecord(),
    busy: false,
    closed: false,
    lastRunMs: null,
    lastTerminalEvent: null,
    resuming: false,
    resume: vi.fn(),
    cancel: vi.fn(),
    ...overrides,
  } as unknown as StudioChat
}

function fakeQueue(count = 0) {
  return {
    queuedMessages: Array.from({ length: count }, (_, i) => ({
      id: `q${i}`,
      text: `消息 ${i}`,
    })),
    submit: vi.fn(),
    remove: vi.fn(),
  }
}

function renderStrip(chatOverrides?: Record<string, unknown>, queueCount = 0) {
  return render(
    <AgentChatStatusStrip
      chat={fakeChat(chatOverrides)}
      queue={fakeQueue(queueCount)}
    />
  )
}

describe('AgentChatStatusStrip', () => {
  it('renders nothing when there is no status to show', () => {
    const { container } = renderStrip()
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the busy state with a cancel button in one strip', () => {
    const cancel = vi.fn()
    renderStrip({
      busy: true,
      session: sessionRecord({ status: 'running' }),
      cancel,
    })
    const runState = screen.getByLabelText('运行状态')
    expect(runState).toHaveTextContent('运行中')
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(cancel).toHaveBeenCalledTimes(1)
  })

  it('labels awaiting_permission and starting distinctly', () => {
    const { unmount } = renderStrip({
      busy: true,
      session: sessionRecord({ status: 'awaiting_permission' }),
    })
    expect(screen.getByLabelText('运行状态')).toHaveTextContent('等待权限确认')
    unmount()
    renderStrip({
      busy: true,
      session: sessionRecord({ status: 'starting' }),
    })
    expect(screen.getByLabelText('运行状态')).toHaveTextContent(
      '正在启动 agent'
    )
  })

  it('shows 已完成 with the duration after a normal turn end', () => {
    renderStrip({ lastRunMs: 65_000, lastTerminalEvent: 'turn_end' })
    expect(screen.getByLabelText('运行状态')).toHaveTextContent(
      '已完成 · 用时 1m5s'
    )
  })

  it('shows 已超时终止 instead of 已完成 after a timeout (#693)', () => {
    renderStrip({ lastRunMs: 3_600_000, lastTerminalEvent: 'turn_timeout' })
    const runState = screen.getByLabelText('运行状态')
    expect(runState).toHaveTextContent('已超时终止 · 用时 60m0s')
    expect(runState).not.toHaveTextContent('已完成')
  })

  it('offers 继续对话 for a closed session and resumes on click', () => {
    const resume = vi.fn()
    renderStrip({
      closed: true,
      session: sessionRecord({ status: 'closed' }),
      resume,
    })
    const runState = screen.getByLabelText('运行状态')
    expect(runState).toHaveTextContent('会话已关闭，历史记录已保留')
    fireEvent.click(screen.getByRole('button', { name: '继续对话' }))
    expect(resume).toHaveBeenCalledTimes(1)
  })

  it('labels an error session as 会话已中断', () => {
    renderStrip({
      closed: true,
      session: sessionRecord({ status: 'error' }),
    })
    expect(screen.getByLabelText('运行状态')).toHaveTextContent('会话已中断')
  })

  it('shows the queue count summary when messages are queued', () => {
    renderStrip(undefined, 2)
    expect(screen.getByLabelText('会话状态条')).toHaveTextContent('排队中 2')
  })

  it('shows the context usage on the right side (#694)', () => {
    renderStrip({
      session: sessionRecord({ usage: { used: 12_345, size: 262_144 } }),
    })
    expect(screen.getByLabelText('上下文用量')).toHaveTextContent(
      '上下文 12.3k / 262.1k tokens（5%）'
    )
  })

  it('shows the compacting hint even without usage yet', () => {
    renderStrip({ session: sessionRecord({ compacting: true }) })
    expect(screen.getByLabelText('上下文用量')).toHaveTextContent(
      '正在压缩上下文…'
    )
  })

  it('combines run state, queue summary and usage in a single row', () => {
    renderStrip(
      {
        busy: true,
        session: sessionRecord({
          status: 'running',
          usage: { used: 1000, size: 2000 },
        }),
      },
      1
    )
    const strip = screen.getByLabelText('会话状态条')
    expect(strip).toHaveTextContent('运行中')
    expect(strip).toHaveTextContent('排队中 1')
    expect(strip).toHaveTextContent('上下文 1.0k / 2.0k tokens（50%）')
  })
})
