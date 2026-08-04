import type {
  WorkspaceSettings,
  ResourceProviderDefinition,
  WorkflowDefinitionRecord,
} from '../../types'
import type {
  ExecutorDefinition,
  WorkspaceExecutorConfiguration,
} from '../../types/executorTypes'
import type { components } from '../../generated/api'

export type WorkspaceAgentRouteEntry =
  components['schemas']['WorkspaceAgentRouteEntry']

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
  resourceProviders: ResourceProviderDefinition[]
  workflowDefinition: WorkflowDefinitionRecord | null
  testStatus: TestStatus
  executorCatalog: ExecutorDefinition[]
  executorConfiguration: WorkspaceExecutorConfiguration
  originalExecutorConfiguration: WorkspaceExecutorConfiguration | null
  pendingAllocationRemoval: string | null
  agentRoutes: WorkspaceAgentRouteEntry[]
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
  fetchSettings: (workspaceId: string) => Promise<void>
  fetchResourceProviders: () => Promise<void>
  fetchWorkflowDefinition: () => Promise<void>
  saveAll: () => Promise<void>
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
  resources: {},
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
