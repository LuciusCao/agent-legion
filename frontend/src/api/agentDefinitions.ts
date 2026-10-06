import { api } from './core'
import type { AgentListResponse } from '../types'
import type { components } from '../generated/api'

type WorkspaceAgentProvenanceResponse =
  components['schemas']['WorkspaceAgentProvenanceResponse']

// Agent 定义目录是 workspace 作用域（schema v46）：所有端点都带
// workspace_id 查询参数，后端同时用它做成员校验。#1079（#440 P3b）：
// Studio 已无 Agent 定义编辑入口，前端只保留只读列表（设置页「历史
// Agent 定义」）；写端点 wrapper（含归档）随 AgentEditor / 草稿卡与设置页
// 归档按钮一并删除。
const base = '/api/agent-definitions'

function scoped(path: string, workspaceId: string): string {
  return `${path}?workspace_id=${encodeURIComponent(workspaceId)}`
}

export const fetchAgentDefinitions = (workspaceId: string) =>
  api<AgentListResponse>(scoped(base, workspaceId))

// #1079（#440 D1）：「已内联到 N 个节点」——active revision 的
// agent_profile_provenance 只读投影（workspace 级端点，非 Agent 定义端点）。
export const fetchAgentProvenance = (workspaceId: string) =>
  api<WorkspaceAgentProvenanceResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/agent-provenance`
  )
