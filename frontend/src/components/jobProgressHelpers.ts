import { formatDuration } from '../lib/formatters'
import type { JobNode } from '../types/jobTypes'

// #731/#1180：pi/openclaw 词条已摘除——运行时投影只产生 code/None
// （agent 节点为 None），退役 runtime 不再出现在对外词汇里。
export const EXECUTOR_KIND_LABELS: Record<string, string> = {
  code: '代码',
}

export const EXECUTOR_KIND_ICONS: Record<string, string> = {
  code: 'code',
}

export function resolveExecutionType(node: JobNode): 'code' | 'agent' | null {
  const executionType =
    node.execution_type === undefined
      ? node.agent_id
        ? 'agent'
        : node.executor_kind === 'code'
          ? 'code'
          : null
      : node.execution_type
  return executionType
}

export function resolveVisibleExecutionType(
  node: JobNode
): 'code' | 'agent' | null {
  const executionType = resolveExecutionType(node)
  return executionType === 'code' && !node.capability ? null : executionType
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
