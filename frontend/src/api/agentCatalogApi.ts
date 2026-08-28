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

// ref：后端预览端点的版本参数（GET /api/agent-catalog/skills/{key}?ref=<tag>，
// 非法 ref 404）；契约见 generated/api.ts 的 SkillDetailResponse。
export const getSkillDetail = (skillKey: string, ref?: string) =>
  api<SkillDetail>(
    `/api/agent-catalog/skills/${skillKey
      .split('/')
      .map(encodeURIComponent)
      .join('/')}${ref ? `?ref=${encodeURIComponent(ref)}` : ''}`
  )

export const getWorkspaceExecutionConfiguration = (workspaceId: string) =>
  api<WorkspaceExecutionConfiguration>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/execution-configuration`
  )
