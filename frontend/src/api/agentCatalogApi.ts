import { api } from './core'
import type {
  AgentCatalogResponse,
  SkillDetail,
  WorkspaceExecutionConfiguration,
} from '../types/agentCatalogTypes'

export const getAgentCatalog = (workspaceId: string) =>
  api<AgentCatalogResponse>(
    `/api/agent-catalog?workspace_id=${encodeURIComponent(workspaceId)}`
  )

// ref：后端预览端点的版本参数（GET /api/agent-catalog/skills/{key}?workspace_id=<ws>&ref=<tag>，
// 非法 ref 404）；workspace_id 是鉴权 scope（成员校验 + scoped 绑定）。
// 契约见 generated/api.ts 的 SkillDetailResponse。
export const getSkillDetail = (
  skillKey: string,
  workspaceId: string,
  ref?: string
) => {
  const params = new URLSearchParams({ workspace_id: workspaceId })
  if (ref) params.set('ref', ref)
  return api<SkillDetail>(
    `/api/agent-catalog/skills/${skillKey
      .split('/')
      .map(encodeURIComponent)
      .join('/')}?${params.toString()}`
  )
}

export const getWorkspaceExecutionConfiguration = (workspaceId: string) =>
  api<WorkspaceExecutionConfiguration>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/execution-configuration`
  )
