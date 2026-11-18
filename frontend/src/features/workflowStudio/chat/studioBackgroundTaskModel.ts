import { formatDuration } from '../../../lib/formatters'
import { asRecord, asText, type ChatMessage } from './studioChatMessages'

export type StudioBackgroundTaskKind = 'agent' | 'bash' | 'unknown'

export type StudioBackgroundTaskHistoryItem = {
  messageId: string
  event: string
  status: string
  detail: string
  createdAt: string
}

export type StudioBackgroundTask = {
  taskId: string
  kind: StudioBackgroundTaskKind
  description: string
  status: string
  detail: string
  elapsedSeconds: number | null
  startedAtMs: number | null
  terminal: boolean
  summary: string
  history: StudioBackgroundTaskHistoryItem[]
}

const STATUS_LABELS: Record<string, string> = {
  created: '已创建',
  starting: '正在启动',
  running: '运行中',
  awaiting_approval: '等待审批',
  quiet: '长时间无输出',
  stale: '心跳长时间未更新',
  unavailable: '状态暂时无法读取',
  completed: '已完成',
  failed: '失败',
  killed: '已终止',
  lost: '执行进程丢失',
  timed_out: '已超时',
  unknown: '未知状态',
}

export function backgroundTaskStatusLabel(status: string): string {
  const label = STATUS_LABELS[status]
  return typeof label === 'string' ? label : status || '未知状态'
}

function taskKind(value: unknown): StudioBackgroundTaskKind | null {
  return value === 'agent' || value === 'bash' ? value : null
}

function elapsedSeconds(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? Math.floor(value)
    : null
}

function startedAtMs(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? value * 1000
    : null
}

function messageTimeMs(message: ChatMessage): number | null {
  const parsed = Date.parse(message.created_at)
  return Number.isNaN(parsed) ? null : parsed
}

function backgroundEvent(message: ChatMessage) {
  if (message.kind !== 'status') return null
  const content = asRecord(message.content)
  const event = asText(content?.event)
  if (
    event !== 'background_task_status' &&
    event !== 'background_task_finished'
  )
    return null
  const taskId = asText(content?.task_id).trim()
  if (!content || !taskId) return null
  return {
    content,
    event,
    taskId,
    terminal: event === 'background_task_finished',
  }
}

export function deriveStudioBackgroundTaskViews(messages: ChatMessage[]): {
  tasks: StudioBackgroundTask[]
  timelineMessages: ChatMessage[]
} {
  const byId = new Map<string, StudioBackgroundTask>()
  const timelineMessages: ChatMessage[] = []
  for (const message of messages) {
    const current = backgroundEvent(message)
    if (!current) {
      timelineMessages.push(message)
      continue
    }
    const { content, event, taskId, terminal } = current
    const previous = byId.get(taskId)
    if (!previous || terminal) timelineMessages.push(message)
    const reportedElapsed = elapsedSeconds(content.elapsed_seconds)
    const explicitStart = startedAtMs(content.started_at)
    const observedAt = messageTimeMs(message)
    const inferredStart =
      observedAt !== null && reportedElapsed !== null
        ? observedAt - reportedElapsed * 1000
        : null
    const status = asText(content.status)
    const description = asText(content.description)
    const historyItem: StudioBackgroundTaskHistoryItem = {
      messageId: message.id,
      event,
      status: status || 'unknown',
      detail: asText(content.detail),
      createdAt: message.created_at,
    }
    if (previous?.terminal && !terminal) {
      byId.set(taskId, {
        ...previous,
        history: [...previous.history, historyItem],
      })
      continue
    }
    const task: StudioBackgroundTask = {
      taskId,
      kind: taskKind(content.kind) ?? previous?.kind ?? 'unknown',
      description: description || previous?.description || taskId,
      status:
        status ||
        (previous?.terminal
          ? previous.status
          : terminal
            ? 'unknown'
            : (previous?.status ?? 'unknown')),
      detail: asText(content.detail) || previous?.detail || '',
      elapsedSeconds:
        reportedElapsed ??
        (terminal && !previous?.terminal
          ? null
          : (previous?.elapsedSeconds ?? null)),
      startedAtMs: explicitStart ?? previous?.startedAtMs ?? inferredStart,
      terminal,
      summary: terminal
        ? asText(content.summary) ||
          (previous?.terminal ? previous.summary : '')
        : '',
      history: [...(previous?.history ?? []), historyItem],
    }
    byId.set(taskId, task)
  }
  return { tasks: [...byId.values()], timelineMessages }
}

export function backgroundTaskElapsedAt(
  task: StudioBackgroundTask,
  nowMs: number
): number | null {
  if (task.terminal || task.startedAtMs === null || !Number.isFinite(nowMs))
    return task.elapsedSeconds
  const liveElapsed = Math.max(0, Math.floor((nowMs - task.startedAtMs) / 1000))
  return Math.max(task.elapsedSeconds ?? 0, liveElapsed)
}

export function backgroundTaskElapsedText(seconds: number | null): string {
  if (seconds === null) return '—'
  return seconds === 0 ? '0秒' : formatDuration(seconds * 1000)
}
