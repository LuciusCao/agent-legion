import { api } from './core'
import type { NodeRun } from '../types/jobTypes'

// #410：节点检查器 latest 绑定的实际执行版本回显数据源——按 node_key 拿
// 最近 runs（列表本身 started_at 倒序，取第一条的 skill_version）。
export async function fetchWorkspaceNodeRuns(
  workspaceId: string,
  options?: { nodeKey?: string; limit?: number }
): Promise<NodeRun[]> {
  const params = new URLSearchParams()
  if (options?.nodeKey) params.set('node_key', options.nodeKey)
  if (options?.limit) params.set('limit', String(options.limit))
  const query = params.toString()
  const { runs } = await api<{ runs: NodeRun[] }>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/node-runs${query ? `?${query}` : ''}`
  )
  return runs
}
