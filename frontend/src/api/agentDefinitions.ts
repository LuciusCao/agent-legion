import { api } from './core'
import type { AgentListResponse } from '../types'

// Agent 定义目录是 workspace 作用域（schema v46）：所有端点都带
// workspace_id 查询参数，后端同时用它做成员校验。#1079（#440 P3b）：
// Studio 已无 Agent 定义编辑入口，前端只保留只读列表（设置页「历史
// Agent 定义」）；写端点 wrapper 随 AgentEditor / 草稿卡一并删除。
const base = '/api/agent-definitions'

function scoped(path: string, workspaceId: string): string {
  return `${path}?workspace_id=${encodeURIComponent(workspaceId)}`
}

function item(agentId: string): string {
  return `${base}/${encodeURIComponent(agentId)}`
}

export const fetchAgentDefinitions = (workspaceId: string) =>
  api<AgentListResponse>(scoped(base, workspaceId))

export const archiveAgent = (workspaceId: string, agentId: string) =>
  api<{ archived: number }>(scoped(item(agentId), workspaceId), {
    method: 'DELETE',
  })
