import { api } from './core'
import type {
  ExecutorCatalogResponse,
  SkillDetail,
  WorkspaceExecutorConfiguration,
} from '../types/executorTypes'

export const getExecutorCatalog = () =>
  api<ExecutorCatalogResponse>('/api/executors')

export const getSkillDetail = (skillKey: string) =>
  api<SkillDetail>(
    `/api/executors/skills/${skillKey.split('/').map(encodeURIComponent).join('/')}`
  )

export const getWorkspaceExecutorConfiguration = (workspaceId: string) =>
  api<WorkspaceExecutorConfiguration>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/executor-configuration`
  )
