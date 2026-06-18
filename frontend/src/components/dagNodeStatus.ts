export type DagNodeStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'stale'

export const STATUS_ICON: Record<DagNodeStatus, string> = {
  completed: 'check_circle',
  running: 'hourglass_empty',
  failed: 'error',
  stale: 'warning',
  pending: 'radio_button_unchecked',
}

export const STATUS_LABEL: Record<DagNodeStatus, string> = {
  completed: '已完成',
  running: '运行中',
  failed: '失败',
  stale: '过期',
  pending: '等待中',
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
