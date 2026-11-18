import { describe, expect, it } from 'vitest'
import {
  backgroundTaskElapsedAt,
  backgroundTaskElapsedText,
  backgroundTaskStatusLabel,
  deriveStudioBackgroundTaskViews,
} from './studioBackgroundTaskModel'
import type { ChatMessage } from './studioChatMessages'

function message(
  id: string,
  content: Record<string, unknown>,
  kind: ChatMessage['kind'] = 'status'
): ChatMessage {
  return {
    id,
    session_id: 's1',
    kind,
    role: kind === 'status' ? 'system' : 'agent',
    content,
    seq: Number(id.replace(/\D/g, '')) || 1,
    created_at: '2026-01-01T00:00:10Z',
  }
}

function taskEvent(
  id: string,
  taskId: string,
  event: 'background_task_status' | 'background_task_finished',
  overrides: Record<string, unknown> = {}
): ChatMessage {
  return message(id, {
    event,
    task_id: taskId,
    kind: 'agent',
    status: event === 'background_task_finished' ? 'completed' : 'running',
    description: `描述 ${taskId}`,
    started_at: 100,
    elapsed_seconds: 10,
    detail: `详情 ${id}`,
    summary: event === 'background_task_finished' ? `摘要 ${id}` : '',
    ...overrides,
  })
}

describe('deriveStudioBackgroundTaskViews', () => {
  it('groups history by task, merges the latest state, and filters only intermediate timeline rows', () => {
    const ordinary = message('m0', { event: 'turn_end' })
    const created = taskEvent('m1', 'agent-1', 'background_task_status', {
      status: 'created',
      elapsed_seconds: 0,
    })
    const running = taskEvent('m2', 'agent-1', 'background_task_status', {
      description: '最新描述',
      detail: '中间进展',
      elapsed_seconds: 12,
    })
    const other = taskEvent('m3', 'bash-1', 'background_task_status', {
      kind: 'bash',
      status: 'quiet',
    })
    const finished = taskEvent('m4', 'agent-1', 'background_task_finished', {
      description: '最新描述',
      summary: '测试全部通过',
      elapsed_seconds: 42,
    })
    const trailing = message('m5', { event: 'session_resumed' })
    const messages = [ordinary, created, running, other, finished, trailing]

    const views = deriveStudioBackgroundTaskViews(messages)

    expect(views.tasks.map((task) => task.taskId)).toEqual([
      'agent-1',
      'bash-1',
    ])
    expect(views.tasks[0]).toMatchObject({
      kind: 'agent',
      description: '最新描述',
      status: 'completed',
      elapsedSeconds: 42,
      terminal: true,
      summary: '测试全部通过',
    })
    expect(views.tasks[0].history.map((item) => item.messageId)).toEqual([
      'm1',
      'm2',
      'm4',
    ])
    expect(views.tasks[1].kind).toBe('bash')
    expect(views.timelineMessages.map((item) => item.id)).toEqual([
      'm0',
      'm1',
      'm3',
      'm4',
      'm5',
    ])
    expect(views.timelineMessages[0]).toBe(ordinary)
    expect(views.timelineMessages[1]).toBe(created)
    expect(views.timelineMessages[2]).toBe(other)
    expect(views.timelineMessages[3]).toBe(finished)
  })

  it('locks the full terminal snapshot when a late non-terminal receipt arrives', () => {
    const running = taskEvent('m1', 'agent-1', 'background_task_status', {
      description: '运行描述',
      detail: '运行详情',
    })
    const finished = taskEvent('m2', 'agent-1', 'background_task_finished', {
      status: 'failed',
      description: '终态描述',
      detail: '终态详情',
      summary: '失败摘要',
      elapsed_seconds: 42,
    })
    const lateRunning = taskEvent('m3', 'agent-1', 'background_task_status', {
      kind: 'bash',
      status: 'running',
      description: '迟到描述',
      detail: '迟到详情',
      started_at: 200,
      elapsed_seconds: 30,
    })
    const views = deriveStudioBackgroundTaskViews([
      running,
      finished,
      lateRunning,
    ])
    const task = views.tasks[0]
    expect(task).toMatchObject({
      terminal: true,
      status: 'failed',
      summary: '失败摘要',
      elapsedSeconds: 42,
      kind: 'agent',
      description: '终态描述',
      detail: '终态详情',
      startedAtMs: 100_000,
    })
    expect(backgroundTaskElapsedAt(task, 1_000_000_000)).toBe(42)
    expect(task.history.map((item) => item.messageId)).toEqual([
      'm1',
      'm2',
      'm3',
    ])
    expect(task.history[2]).toMatchObject({
      status: 'running',
      detail: '迟到详情',
    })
    expect(views.timelineMessages.map((item) => item.id)).toEqual(['m1', 'm2'])
  })

  it('does not erase confirmed terminal evidence with incomplete repeated receipts', () => {
    const first = taskEvent('m1', 'agent-1', 'background_task_finished', {
      status: 'completed',
      description: '已确认描述',
      detail: '已确认详情',
      summary: '已确认摘要',
      elapsed_seconds: 42,
    })
    const repeated = taskEvent('m2', 'agent-1', 'background_task_finished', {
      kind: undefined,
      status: '',
      description: '',
      detail: '',
      started_at: undefined,
      elapsed_seconds: undefined,
      summary: '',
    })
    const views = deriveStudioBackgroundTaskViews([first, repeated])
    expect(views.tasks[0]).toMatchObject({
      terminal: true,
      status: 'completed',
      summary: '已确认摘要',
      elapsedSeconds: 42,
      kind: 'agent',
      description: '已确认描述',
      detail: '已确认详情',
      startedAtMs: 100_000,
    })
    expect(views.tasks[0].history.map((item) => item.messageId)).toEqual([
      'm1',
      'm2',
    ])
    expect(views.timelineMessages.map((item) => item.id)).toEqual(['m1', 'm2'])
  })

  it('degrades malformed and legacy messages without collecting unrelated task_id fields', () => {
    const blank = taskEvent('m1', ' ', 'background_task_status')
    const unrelated = message('m2', {
      event: 'unprompted_turn',
      task_id: 'agent-other',
    })
    const wrongKind = {
      ...taskEvent('m3', 'agent-text', 'background_task_status'),
      kind: 'text' as const,
    }
    const legacy = taskEvent('m4', 'legacy', 'background_task_status', {
      kind: { unexpected: true },
      status: '',
      description: '',
      elapsed_seconds: Number.NaN,
      started_at: '100',
      detail: null,
    })
    const views = deriveStudioBackgroundTaskViews([
      blank,
      unrelated,
      wrongKind,
      legacy,
    ])
    expect(views.tasks).toHaveLength(1)
    expect(views.tasks[0]).toMatchObject({
      taskId: 'legacy',
      kind: 'unknown',
      description: 'legacy',
      status: 'unknown',
      detail: '',
      elapsedSeconds: null,
      terminal: false,
    })
    expect(views.timelineMessages.map((item) => item.id)).toEqual([
      'm1',
      'm2',
      'm3',
      'm4',
    ])
  })

  it('uses the terminal event elapsed and summary instead of a later wall-clock estimate', () => {
    const terminal = taskEvent('m1', 'agent-1', 'background_task_finished', {
      status: 'failed',
      elapsed_seconds: 18,
      summary: '失败原因',
    })
    const [task] = deriveStudioBackgroundTaskViews([terminal]).tasks
    expect(backgroundTaskElapsedAt(task, 100_000_000)).toBe(18)
    expect(task.summary).toBe('失败原因')
  })

  it.each([
    ['created', '已创建'],
    ['starting', '正在启动'],
    ['running', '运行中'],
    ['awaiting_approval', '等待审批'],
    ['quiet', '长时间无输出'],
    ['stale', '心跳长时间未更新'],
    ['unavailable', '状态暂时无法读取'],
    ['completed', '已完成'],
    ['failed', '失败'],
    ['killed', '已终止'],
    ['lost', '执行进程丢失'],
    ['timed_out', '已超时'],
    ['future_state', 'future_state'],
    ['__proto__', '__proto__'],
    ['toString', 'toString'],
    ['', '未知状态'],
  ])('maps status %s safely', (status, label) => {
    expect(backgroundTaskStatusLabel(status)).toBe(label)
  })

  it.each(['completed', 'failed', 'killed', 'lost', 'timed_out'])(
    'treats finished status %s as terminal',
    (status) => {
      const event = taskEvent('m1', 'agent-1', 'background_task_finished', {
        status,
      })
      expect(deriveStudioBackgroundTaskViews([event]).tasks[0].terminal).toBe(
        true
      )
    }
  )
})

describe('background task elapsed', () => {
  it('advances active tasks from started_at and never moves backwards from the latest receipt', () => {
    const event = taskEvent('m1', 'agent-1', 'background_task_status', {
      started_at: 100,
      elapsed_seconds: 10,
    })
    const [task] = deriveStudioBackgroundTaskViews([event]).tasks
    expect(backgroundTaskElapsedAt(task, 130_000)).toBe(30)
    expect(backgroundTaskElapsedAt(task, 105_000)).toBe(10)
  })

  it('infers an active elapsed anchor from legacy created_at when started_at is absent', () => {
    const event = taskEvent('m1', 'agent-1', 'background_task_status', {
      started_at: undefined,
      elapsed_seconds: 5,
    })
    const [task] = deriveStudioBackgroundTaskViews([event]).tasks
    expect(task.startedAtMs).toBe(Date.parse('2026-01-01T00:00:05Z'))
    expect(
      backgroundTaskElapsedAt(task, Date.parse('2026-01-01T00:00:20Z'))
    ).toBe(15)
  })

  it('formats zero and longer durations', () => {
    expect(backgroundTaskElapsedText(null)).toBe('—')
    expect(backgroundTaskElapsedText(0)).toBe('0秒')
    expect(backgroundTaskElapsedText(65)).toBe('1分5秒')
  })
})
