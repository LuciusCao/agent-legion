import { api } from './api'
import type {
  ExecutorCatalogResponse,
  WorkspaceExecutorConfiguration,
} from './executorTypes'

export const getExecutorCatalog = () =>
  api<ExecutorCatalogResponse>('/api/executors')

export const getWorkspaceExecutorConfiguration = (workspaceId: string) =>
  api<WorkspaceExecutorConfiguration>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/executor-configuration`
  )
