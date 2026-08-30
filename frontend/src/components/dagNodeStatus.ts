export type DagNodeStatus =
  | 'pending'
  | 'ready'
  | 'running'
  | 'completed'
  | 'failed'
  | 'stale'
  | 'not_applicable'
  | 'awaiting_approval'

export const STATUS_ICON: Record<DagNodeStatus, string> = {
  completed: 'check_circle',
  running: 'hourglass_empty',
  failed: 'error',
  stale: 'warning',
  pending: 'radio_button_unchecked',
  ready: 'play_circle',
  not_applicable: 'block',
  awaiting_approval: 'pending_actions',
}

export const STATUS_LABEL: Record<DagNodeStatus, string> = {
  pending: '等待中',
  ready: '就绪',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  stale: '需重跑',
  not_applicable: '不适用',
  awaiting_approval: '待审批',
}
export function formatDuration(
  status: DagNodeStatus,
  duration?: number
): string {
  if (status === 'running') {
    return `运行中 ${duration ?? 0}s`
  }
  if (typeof duration === 'number') {
    return `耗时 ${duration}s`
  }
  return ''
}
