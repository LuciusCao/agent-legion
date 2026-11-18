import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { StudioChatPanel } from './StudioChatPanel'
import { StudioChatBackgroundTasks } from './StudioChatBackgroundTasks'
import * as chatApi from './studioChatApi'
import type { StudioChatSessionRecord } from './studioChatApi'
import type { ChatMessage } from './studioChatMessages'
import { deriveStudioBackgroundTaskViews } from './studioBackgroundTaskModel'
import { EventSourceMock } from '../../../testing/eventSourceMock'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useSettingStore } from '../../../stores/settingStore'

vi.mock('./studioChatApi')
vi.mock('../../../api/workflowDraftCompare', () => ({
  compareWorkflowDraft: vi.fn(),
}))

const mockApi = vi.mocked(chatApi)

function sessionRecord(
  id = 's1',
  overrides?: Partial<StudioChatSessionRecord>
): StudioChatSessionRecord {
  return {
    id,
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

function message(
  id: string,
  seq: number,
  kind: ChatMessage['kind'],
  content: Record<string, unknown>
): ChatMessage {
  return {
    id,
    session_id: 's1',
    kind,
    role: kind === 'text' ? 'user' : 'system',
    content,
    seq,
    created_at: '2026-01-01T00:00:10Z',
  }
}

function taskMessage(
  id: string,
  seq: number,
  taskId: string,
  overrides: Record<string, unknown> = {}
): ChatMessage {
  return message(id, seq, 'status', {
    event: 'background_task_status',
    task_id: taskId,
    kind: 'agent',
    status: 'running',
    description: `任务 ${taskId}`,
    started_at: 100,
    elapsed_seconds: 10,
    detail: `进展 ${id}`,
    summary: '',
    ...overrides,
  })
}

function renderPanel() {
  return render(
    <TestQueryProvider>
      <StudioChatPanel onApplyWorkflowDraft={vi.fn()} />
    </TestQueryProvider>
  )
}

function tasksTab() {
  return screen.getByRole('tab', { name: '后台任务' })
}

describe('StudioChatBackgroundTasks', () => {
  const originalEventSource = globalThis.EventSource

  beforeEach(() => {
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
    vi.clearAllMocks()
    useSettingStore.setState({
      workspaceId: 'ws1',
      settings: { entityType: 'question' },
    })
    mockApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi Code' },
    ])
    mockApi.fetchStudioChatSessions.mockResolvedValue([sessionRecord()])
    mockApi.fetchStudioChatMessages.mockResolvedValue([])
    mockApi.updateStudioChatContext.mockResolvedValue(sessionRecord())
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
    vi.useRealTimers()
  })

  it('defaults to chat, retains only first and terminal task receipts, and leaves other statuses intact', async () => {
    mockApi.fetchStudioChatMessages.mockResolvedValue([
      message('m1', 1, 'text', { text: '开始检查' }),
      taskMessage('m2', 2, 'agent-1', {
        status: 'created',
        description: '复核执行配置',
        detail: '任务已创建',
        elapsed_seconds: 0,
      }),
      taskMessage('m3', 3, 'agent-1', { detail: '正在运行测试' }),
      taskMessage('m4', 4, 'bash-1', {
        kind: 'bash',
        status: 'quiet',
        description: '运行 quick gate',
        detail: '长时间没有输出',
      }),
      taskMessage('m5', 5, 'agent-1', {
        event: 'background_task_finished',
        status: 'completed',
        description: '复核执行配置',
        detail: '任务已完成',
        summary: '相关测试全部通过',
        elapsed_seconds: 42,
      }),
      message('m6', 6, 'status', {
        event: 'mcp_unverified',
        detail: '其他状态保持不变',
      }),
    ])
    renderPanel()

    expect(await screen.findByText('开始检查')).toBeInTheDocument()
    const chatPanel = screen.getByRole('tabpanel', { name: '对话' })
    expect(screen.getByRole('tab', { name: '对话' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(tasksTab()).toHaveAttribute('aria-selected', 'false')
    expect(within(tasksTab()).getByText('2')).toBeInTheDocument()
    expect(within(chatPanel).getByText('任务已创建')).toBeVisible()
    expect(
      within(chatPanel).queryByText('正在运行测试')
    ).not.toBeInTheDocument()
    expect(within(chatPanel).getByText('任务已完成')).toBeVisible()
    expect(within(chatPanel).getByText(/其他状态保持不变/)).toBeVisible()

    fireEvent.click(tasksTab())
    expect(tasksTab()).toHaveAttribute('aria-selected', 'true')
    const agentRow = screen.getByRole('button', { name: /复核执行配置/ })
    const bashRow = screen.getByRole('button', { name: /运行 quick gate/ })
    expect(agentRow).toBeVisible()
    expect(bashRow).toBeVisible()
    expect(within(bashRow).getByText('长时间无输出')).toBeVisible()
    expect(within(agentRow).getByText('相关测试全部通过')).toBeVisible()
    expect(screen.getByLabelText('消息输入')).not.toBeVisible()
  })

  it('filters task kinds and expands ordered event history with terminal summary', async () => {
    mockApi.fetchStudioChatMessages.mockResolvedValue([
      taskMessage('m1', 1, 'agent-1', {
        status: 'created',
        description: '复核执行配置',
        detail: '创建记录',
      }),
      taskMessage('m2', 2, 'agent-1', { detail: '运行记录' }),
      taskMessage('m3', 3, 'bash-1', {
        kind: 'bash',
        description: '运行测试',
      }),
      taskMessage('m4', 4, 'agent-1', {
        event: 'background_task_finished',
        status: 'completed',
        description: '复核执行配置',
        detail: '完成记录',
        summary: '全部通过',
      }),
    ])
    renderPanel()
    const input = await screen.findByLabelText('消息输入')
    await waitFor(() => expect(input).toBeEnabled())
    fireEvent.click(tasksTab())

    const trigger = screen.getByRole('button', { name: /复核执行配置/ })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    const detailId = trigger.getAttribute('aria-controls')!
    expect(document.getElementById(detailId)).not.toBeVisible()
    fireEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    const detail = document.getElementById(detailId)!
    expect(detail).toBeVisible()
    expect(within(detail).getByText('终态摘要')).toBeInTheDocument()
    expect(within(detail).getByText('全部通过')).toBeInTheDocument()
    const history = within(detail).getByRole('list')
    expect(within(history).getAllByRole('listitem')).toHaveLength(3)
    expect(history).toHaveTextContent(/创建记录[\s\S]*运行记录[\s\S]*完成记录/)

    const filter = screen.getByLabelText('后台任务类型')
    fireEvent.change(filter, { target: { value: 'bash' } })
    expect(screen.queryByText('复核执行配置')).not.toBeInTheDocument()
    expect(screen.getByText('运行测试')).toBeVisible()
    fireEvent.change(filter, { target: { value: 'agent' } })
    expect(screen.getByText('复核执行配置')).toBeVisible()
    expect(screen.queryByText('运行测试')).not.toBeInTheDocument()
  })

  it('provides an empty state and keyboard-operable tabs with linked panels', async () => {
    renderPanel()
    await screen.findByLabelText('消息输入')
    const chatTab = screen.getByRole('tab', { name: '对话' })
    expect(chatTab).toHaveAttribute('tabindex', '0')
    expect(tasksTab()).toHaveAttribute('tabindex', '-1')
    for (const tab of [chatTab, tasksTab()]) {
      const panelId = tab.getAttribute('aria-controls')!
      expect(document.getElementById(panelId)).toHaveAttribute(
        'aria-labelledby',
        tab.id
      )
    }

    fireEvent.keyDown(screen.getByRole('tablist', { name: 'Agent 助手视图' }), {
      key: 'ArrowRight',
    })
    expect(tasksTab()).toHaveAttribute('aria-selected', 'true')
    expect(tasksTab()).toHaveFocus()
    expect(screen.getByText('当前会话还没有后台任务')).toBeVisible()
    fireEvent.keyDown(screen.getByRole('tablist', { name: 'Agent 助手视图' }), {
      key: 'Home',
    })
    expect(chatTab).toHaveAttribute('aria-selected', 'true')
    expect(chatTab).toHaveFocus()
    fireEvent.keyDown(screen.getByRole('tablist', { name: 'Agent 助手视图' }), {
      key: 'End',
    })
    expect(tasksTab()).toHaveAttribute('aria-selected', 'true')
  })

  it('ticks active elapsed time without remounting the task row', () => {
    vi.useFakeTimers()
    vi.setSystemTime(110_000)
    const event = taskMessage('m1', 1, 'agent-1', {
      description: '持续运行',
      started_at: 100,
      elapsed_seconds: 10,
    })
    const [task] = deriveStudioBackgroundTaskViews([event]).tasks
    render(
      <StudioChatBackgroundTasks
        id="tasks"
        labelledBy="tasks-tab"
        tasks={[task]}
        hidden={false}
      />
    )
    expect(screen.getByText('耗时 10秒')).toBeVisible()
    const row = screen.getByRole('button', { name: /持续运行/ })
    act(() => vi.advanceTimersByTime(5000))
    expect(screen.getByText('耗时 15秒')).toBeVisible()
    expect(screen.getByRole('button', { name: /持续运行/ })).toBe(row)
  })

  it('stops the active clock after terminal and does not restart it for late running receipts', () => {
    vi.useFakeTimers()
    vi.setSystemTime(110_000)
    const running = taskMessage('m1', 1, 'agent-1', {
      description: '持续运行',
      started_at: 100,
      elapsed_seconds: 10,
    })
    const finished = taskMessage('m2', 2, 'agent-1', {
      event: 'background_task_finished',
      status: 'completed',
      description: '持续运行',
      summary: '已确认摘要',
      elapsed_seconds: 42,
    })
    const lateRunning = taskMessage('m3', 3, 'agent-1', {
      description: '持续运行',
      started_at: 200,
      elapsed_seconds: 30,
    })
    const panel = (events: ChatMessage[]) => (
      <StudioChatBackgroundTasks
        id="tasks"
        labelledBy="tasks-tab"
        tasks={deriveStudioBackgroundTaskViews(events).tasks}
        hidden={false}
      />
    )
    const { rerender } = render(panel([running]))
    expect(vi.getTimerCount()).toBe(1)

    rerender(panel([running, finished]))
    let row = screen.getByRole('button', { name: /持续运行/ })
    expect(vi.getTimerCount()).toBe(0)
    expect(within(row).getByText('已完成')).toBeVisible()
    expect(within(row).getByText('已确认摘要')).toBeVisible()
    expect(within(row).getByText('耗时 42秒')).toBeVisible()

    rerender(panel([running, finished, lateRunning]))
    row = screen.getByRole('button', { name: /持续运行/ })
    expect(vi.getTimerCount()).toBe(0)
    expect(within(row).getByText('已完成')).toBeVisible()
    expect(within(row).getByText('已确认摘要')).toBeVisible()
    expect(within(row).getByText('耗时 42秒')).toBeVisible()
    act(() => vi.advanceTimersByTime(5000))
    expect(within(row).getByText('耗时 42秒')).toBeVisible()
  })

  it('keeps the composer, queue, and SSE alive across tabs without auto-switching on new tasks', async () => {
    mockApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord('s1', { status: 'running' }),
    ])
    mockApi.sendStudioChatMessage.mockResolvedValue(
      message('u1', 2, 'text', { text: '排队消息' })
    )
    renderPanel()
    const input = await screen.findByLabelText('消息输入')
    await waitFor(() => expect(input).toBeEnabled())
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))
    const source = EventSourceMock.instances[0]

    fireEvent.change(input, { target: { value: '未发送草稿' } })
    fireEvent.click(tasksTab())
    expect(input).toHaveValue('未发送草稿')
    act(() => {
      source.emitMessage({
        type: 'message',
        message: taskMessage('bg1', 1, 'agent-live'),
      })
    })
    await waitFor(() =>
      expect(within(tasksTab()).getByText('1')).toBeInTheDocument()
    )
    expect(tasksTab()).toHaveAttribute('aria-selected', 'true')
    expect(EventSourceMock.instances).toHaveLength(1)
    expect(source.close).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('tab', { name: '对话' }))
    expect(input).toHaveValue('未发送草稿')
    fireEvent.change(input, { target: { value: '排队消息' } })
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' })
    })
    expect(mockApi.sendStudioChatMessage).not.toHaveBeenCalled()
    expect(screen.getAllByText('排队中 1')[0]).toBeInTheDocument()
    fireEvent.click(tasksTab())
    act(() => {
      source.emitMessage({
        type: 'session',
        session: sessionRecord('s1', { status: 'idle' }),
      })
    })
    await waitFor(() =>
      expect(mockApi.sendStudioChatMessage).toHaveBeenCalledWith(
        'ws1',
        's1',
        '排队消息'
      )
    )
    expect(tasksTab()).toHaveAttribute('aria-selected', 'true')
    expect(EventSourceMock.instances).toHaveLength(1)
  })

  it('replaces tasks on session changes while preserving the selected view and composer state', async () => {
    mockApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord('s1', { title: '第一个会话' }),
      sessionRecord('s2', { title: '第二个会话' }),
    ])
    mockApi.fetchStudioChatMessages.mockImplementation(
      (_workspace, sessionId) =>
        Promise.resolve(
          sessionId === 's1'
            ? [
                message('text1', 1, 'text', { text: '会话一' }),
                taskMessage('a1', 2, 'agent-old', {
                  description: '旧会话任务',
                }),
              ]
            : [
                taskMessage('b1', 1, 'bash-new', {
                  kind: 'bash',
                  description: '新会话 Bash',
                }),
                taskMessage('b2', 2, 'agent-new', {
                  description: '新会话 Agent',
                }),
              ]
        )
    )
    renderPanel()
    const input = await screen.findByLabelText('消息输入')
    await screen.findByText('会话一')
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))
    const oldSource = EventSourceMock.instances[0]
    fireEvent.change(input, { target: { value: '跨会话草稿' } })
    fireEvent.click(tasksTab())
    expect(screen.getByText('旧会话任务')).toBeVisible()

    fireEvent.click(screen.getByLabelText('选择会话'))
    fireEvent.click(await screen.findByRole('button', { name: '第二个会话' }))
    await screen.findByText('新会话 Agent')
    expect(screen.queryByText('旧会话任务')).not.toBeInTheDocument()
    expect(screen.getByText('新会话 Bash')).toBeVisible()
    expect(within(tasksTab()).getByText('2')).toBeInTheDocument()
    expect(tasksTab()).toHaveAttribute('aria-selected', 'true')
    expect(input).toHaveValue('跨会话草稿')
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(2))
    expect(oldSource.close).toHaveBeenCalledTimes(1)
  })
})
