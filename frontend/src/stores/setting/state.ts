import type { WorkspaceSettings } from '../../types'
import type { WorkspaceExecutorConfiguration } from '../../types/executorTypes'

/**
 * hydrateSettings 的输入（由 useWorkspaceSettingsQuery 拉取组装）。
 * 快照本体只存 react-query 缓存，store 只保留 draft 与 original* 基准。
 */
export interface HydrateSettingsInput {
  workspaceName: string
  workspaceDescription: string
  settings: WorkspaceSettings
  executorConfiguration: WorkspaceExecutorConfiguration
}

export type TestStatus = {
  state: 'idle' | 'testing' | 'success' | 'failed'
  message?: string
}

export type SettingState = {
  workspaceId: string | null
  workspaceName: string
  workspaceDescription: string
  settings: WorkspaceSettings
  originalWorkspaceName: string
  originalWorkspaceDescription: string
  originalSettings: WorkspaceSettings | null
  isDirty: boolean
  isSaving: boolean
  saveError: string | null
  testStatus: TestStatus
  executorConfiguration: WorkspaceExecutorConfiguration
  originalExecutorConfiguration: WorkspaceExecutorConfiguration | null
  pendingAllocationRemoval: string | null
  setWorkspaceId: (id: string) => void
  setWorkspaceName: (name: string) => void
  setWorkspaceDescription: (description: string) => void
  setSettings: (s: Partial<WorkspaceSettings>) => void
  setAgentCapacity: (capacity: number) => void
  setExecutorAllocation: (executorId: string, limit: number) => void
  requestExecutorRemoval: (executorId: string) => void
  confirmExecutorRemoval: () => void
  cancelExecutorRemoval: () => void
  setNodeBinding: (
    workflowKey: string,
    nodeKey: string,
    executorId: string | null
  ) => void
  setNodeLimit: (
    workflowKey: string,
    nodeKey: string,
    limit: number | null
  ) => void
  hydrateSettings: (workspaceId: string, snapshot: HydrateSettingsInput) => void
  // 返回是否真正保存成功（重入守卫拒绝或请求失败均为 false）。
  saveAll: () => Promise<boolean>
  testConnection: () => Promise<void>
  resetTestStatus: () => void
}

export type SettingStoreSet = (
  partial:
    | SettingState
    | Partial<SettingState>
    | ((state: SettingState) => SettingState | Partial<SettingState>),
  replace?: boolean
) => void

export const defaultSettings: WorkspaceSettings = {
  entityType: 'question',
  intakeModes: [],
  labelOverrides: {},
  workflowKey: '',
}

export const defaultExecutorConfiguration: WorkspaceExecutorConfiguration = {
  allocations: [],
  bindings: [],
  node_limits: [],
  migration_warnings: [],
  // Workspace-level agent concurrency cap; null = unset = unlimited.
  agent_capacity: null,
}

export function normalizeExecutorConfiguration(
  config: Partial<WorkspaceExecutorConfiguration> | undefined
): WorkspaceExecutorConfiguration {
  return {
    allocations: config?.allocations ?? [],
    bindings: config?.bindings ?? [],
    node_limits: config?.node_limits ?? [],
    migration_warnings: config?.migration_warnings ?? [],
    agent_capacity: config?.agent_capacity ?? null,
  }
}

export function computeDirty(state: Omit<SettingState, 'isDirty'>): boolean {
  if (state.originalSettings === null) return false
  if (state.workspaceName !== state.originalWorkspaceName) return true
  if (state.workspaceDescription !== state.originalWorkspaceDescription)
    return true
  if (JSON.stringify(state.settings) !== JSON.stringify(state.originalSettings))
    return true
  if (
    JSON.stringify(state.executorConfiguration) !==
    JSON.stringify(state.originalExecutorConfiguration)
  )
    return true
  return false
}
