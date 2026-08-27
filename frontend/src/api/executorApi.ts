import { api } from './core'
import type {
  ExecutorCatalogResponse,
  SkillDetail,
  WorkspaceExecutorConfiguration,
} from '../types/executorTypes'

export const getExecutorCatalog = (workspaceId: string) =>
  api<ExecutorCatalogResponse>(
    `/api/executors?workspace_id=${encodeURIComponent(workspaceId)}`
  )

// ref：后端预览端点的版本参数（GET /api/executors/skills/{key}?ref=<tag>，
// 非法 ref 404）；契约见 generated/api.ts 的 SkillDetailResponse。
export const getSkillDetail = (skillKey: string, ref?: string) =>
  api<SkillDetail>(
    `/api/executors/skills/${skillKey
      .split('/')
      .map(encodeURIComponent)
      .join('/')}${ref ? `?ref=${encodeURIComponent(ref)}` : ''}`
  )

export const getWorkspaceExecutorConfiguration = (workspaceId: string) =>
  api<WorkspaceExecutorConfiguration>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/executor-configuration`
  )
