import { api } from './core'
import type { components } from '../generated/api'
import type { NodeRun } from '../types/jobTypes'

// #410：节点检查器 latest 绑定的实际执行版本回显数据源——按 node_key 拿
// 最近 runs（列表本身 started_at 倒序，取第一条的 skill_version）。
// #410 review：类型从生成契约派生（WorkspaceRunsResponse.runs 现为
// NodeRunResponse[]，codex P1 on #427），不再手写 NodeRun[]。
type WorkspaceRunsResponse = components['schemas']['WorkspaceRunsResponse']

export async function fetchWorkspaceNodeRuns(
  workspaceId: string,
  options?: { nodeKey?: string; skill?: string; limit?: number }
): Promise<NodeRun[]> {
  const params = new URLSearchParams()
  if (options?.nodeKey) params.set('node_key', options.nodeKey)
  // #410 codex 四轮 P1：按绑定 key 过滤（schema v75 的 node_runs.skill 列）
  // ——节点从 skill-a 换绑 skill-b 后，查询不得把 a 的最近 run 当成 b 的
  // 「实际执行」回显。
  if (options?.skill) params.set('skill', options.skill)
  if (options?.limit) params.set('limit', String(options.limit))
  const query = params.toString()
  const { runs } = await api<WorkspaceRunsResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/node-runs${query ? `?${query}` : ''}`
  )
  return runs
}
