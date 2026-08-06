import { formatDuration } from '../lib/formatters'
import type { JobNode } from '../types/jobTypes'

export const EXECUTOR_KIND_LABELS: Record<string, string> = {
  code: '代码',
  pi: 'Pi Agent',
  openclaw: 'OpenClaw Agent',
}

export const EXECUTOR_KIND_ICONS: Record<string, string> = {
  code: 'code',
  pi: 'smart_toy',
  openclaw: 'smart_toy',
}

export function computeWaitTime(
  node: JobNode,
  nodes: JobNode[]
): string | undefined {
  if (!node.started_at) return undefined
  const started = new Date(node.started_at).getTime()
  if (Number.isNaN(started)) return undefined

  let readySince: number | undefined = node.created_at
    ? new Date(node.created_at).getTime()
    : undefined
  if (readySince !== undefined && Number.isNaN(readySince)) {
    readySince = undefined
  }

  for (const depKey of node.after ?? []) {
    const depNode = nodes.find((n) => n.node_key === depKey)
    if (!depNode?.finished_at) continue
    const depFinished = new Date(depNode.finished_at).getTime()
    if (Number.isNaN(depFinished)) continue
    readySince =
      readySince === undefined ? depFinished : Math.max(readySince, depFinished)
  }

  if (readySince === undefined) return undefined
  const seconds = Math.max(0, Math.floor((started - readySince) / 1000))
  if (seconds === 0) return '0秒'
  return formatDuration(seconds * 1000)
}
