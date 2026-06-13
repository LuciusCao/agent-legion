import { create } from 'zustand'
import type {
  WorkspaceSettings,
  GlobalServiceStatus,
  ResourceProviderDefinition,
  PipelineDefinitionRecord,
} from '../types'
import type {
  ExecutorDefinition,
  WorkspaceExecutorConfiguration,
} from '../executorTypes'
import { api } from '../api'
import {
  getExecutorCatalog,
  getWorkspaceExecutorConfiguration,
} from '../executorApi'
import { useUiStore } from './uiStore'

type TestStatus = {
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
  globalServices: GlobalServiceStatus | null
  resourceProviders: ResourceProviderDefinition[]
  pipelineDefinition: PipelineDefinitionRecord | null
  testStatus: TestStatus
  executorCatalog: ExecutorDefinition[]
  executorConfiguration: WorkspaceExecutorConfiguration
  originalExecutorConfiguration: WorkspaceExecutorConfiguration | null
  pendingAllocationRemoval: string | null
  setWorkspaceId: (id: string) => void
  setWorkspaceName: (name: string) => void
  setWorkspaceDescription: (description: string) => void
  setSettings: (s: Partial<WorkspaceSettings>) => void
  setExecutorAllocation: (executorId: string, limit: number) => void
  requestExecutorRemoval: (executorId: string) => void
  confirmExecutorRemoval: () => void
  cancelExecutorRemoval: () => void
  setNodeBinding: (
    pipelineKey: string,
    nodeKey: string,
    executorId: string | null
  ) => void
  setNodeLimit: (
    pipelineKey: string,
    nodeKey: string,
    limit: number | null
  ) => void
  fetchSettings: (workspaceId: string) => Promise<void>
  fetchGlobalServices: () => Promise<void>
  fetchResourceProviders: () => Promise<void>
  fetchPipelineDefinition: () => Promise<void>
  saveAll: () => Promise<void>
  testConnection: () => Promise<void>
  resetTestStatus: () => void
}

const defaultSettings: WorkspaceSettings = {
  entityType: 'question',
  intakeModes: [],
  labelOverrides: {},
  pipelineKey: '',
  resources: {},
}

const defaultExecutorConfiguration: WorkspaceExecutorConfiguration = {
  allocations: [],
  bindings: [],
  node_limits: [],
  migration_warnings: [],
}

function normalizeExecutorConfiguration(
  config: Partial<WorkspaceExecutorConfiguration> | undefined
): WorkspaceExecutorConfiguration {
  return {
    allocations: config?.allocations ?? [],
    bindings: config?.bindings ?? [],
    node_limits: config?.node_limits ?? [],
    migration_warnings: config?.migration_warnings ?? [],
  }
}

function computeDirty(state: Omit<SettingState, 'isDirty'>): boolean {
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

export const useSettingStore = create<SettingState>((set, get) => ({
  workspaceId: null,
  workspaceName: '',
  workspaceDescription: '',
  settings: defaultSettings,
  originalWorkspaceName: '',
  originalWorkspaceDescription: '',
  originalSettings: null,
  isDirty: false,
  globalServices: null,
  resourceProviders: [],
  pipelineDefinition: null,
  testStatus: { state: 'idle' },
  isSaving: false,
  saveError: null,
  executorCatalog: [],
  executorConfiguration: defaultExecutorConfiguration,
  originalExecutorConfiguration: null,
  pendingAllocationRemoval: null,

  setWorkspaceId(id) {
    set({ workspaceId: id })
  },

  setWorkspaceName(name) {
    set((state) => {
      const nextState = { ...state, workspaceName: name }
      return { ...nextState, isDirty: computeDirty(nextState) }
    })
  },

  setWorkspaceDescription(description) {
    set((state) => {
      const nextState = { ...state, workspaceDescription: description }
      return { ...nextState, isDirty: computeDirty(nextState) }
    })
  },

  setSettings(s) {
    set((state) => {
      const nextSettings = { ...state.settings, ...s }
      const pipelineChanged =
        s.pipelineKey !== undefined &&
        s.pipelineKey !== state.settings.pipelineKey
      const nextExecutorConfiguration = pipelineChanged
        ? {
            ...state.executorConfiguration,
            bindings: [],
            node_limits: [],
          }
        : state.executorConfiguration
      const nextState = {
        ...state,
        settings: nextSettings,
        pipelineDefinition: pipelineChanged ? null : state.pipelineDefinition,
        executorConfiguration: nextExecutorConfiguration,
      }
      return { ...nextState, isDirty: computeDirty(nextState) }
    })
  },

  setExecutorAllocation(executorId, limit) {
    set((state) => {
      const allocations = state.executorConfiguration.allocations.filter(
        (a) => a.executor_id !== executorId
      )
      allocations.push({
        executor_id: executorId,
        workspace_id: state.workspaceId ?? '',
        concurrency_limit: limit,
      })
      const nextConfiguration = {
        ...state.executorConfiguration,
        allocations,
      }
      const nextState = { ...state, executorConfiguration: nextConfiguration }
      return { ...nextState, isDirty: computeDirty(nextState) }
    })
  },

  requestExecutorRemoval(executorId) {
    set({ pendingAllocationRemoval: executorId })
  },

  confirmExecutorRemoval() {
    set((state) => {
      const executorId = state.pendingAllocationRemoval
      if (!executorId) return state
      const allocations = state.executorConfiguration.allocations.filter(
        (a) => a.executor_id !== executorId
      )
      const bindings = state.executorConfiguration.bindings.filter(
        (b) => b.executor_id !== executorId
      )
      const removedBindingKeys = new Set(
        state.executorConfiguration.bindings
          .filter((b) => b.executor_id === executorId)
          .map((b) => `${b.pipeline_key}:${b.node_key}`)
      )
      const node_limits = state.executorConfiguration.node_limits.filter(
        (l) => !removedBindingKeys.has(`${l.pipeline_key}:${l.node_key}`)
      )
      const nextConfiguration = {
        ...state.executorConfiguration,
        allocations,
        bindings,
        node_limits,
      }
      const nextState = {
        ...state,
        executorConfiguration: nextConfiguration,
        pendingAllocationRemoval: null,
      }
      return { ...nextState, isDirty: computeDirty(nextState) }
    })
  },

  cancelExecutorRemoval() {
    set({ pendingAllocationRemoval: null })
  },

  setNodeBinding(pipelineKey, nodeKey, executorId) {
    set((state) => {
      const bindings = state.executorConfiguration.bindings.filter(
        (b) => !(b.pipeline_key === pipelineKey && b.node_key === nodeKey)
      )
      let node_limits = state.executorConfiguration.node_limits
      if (executorId === null) {
        node_limits = node_limits.filter(
          (l) => !(l.pipeline_key === pipelineKey && l.node_key === nodeKey)
        )
      }
      if (executorId !== null) {
        bindings.push({
          pipeline_key: pipelineKey,
          node_key: nodeKey,
          executor_id: executorId,
        })
      }
      const nextConfiguration = {
        ...state.executorConfiguration,
        bindings,
        node_limits,
      }
      const nextState = { ...state, executorConfiguration: nextConfiguration }
      return { ...nextState, isDirty: computeDirty(nextState) }
    })
  },

  setNodeLimit(pipelineKey, nodeKey, limit) {
    set((state) => {
      const node_limits = state.executorConfiguration.node_limits.filter(
        (l) => !(l.pipeline_key === pipelineKey && l.node_key === nodeKey)
      )
      if (limit !== null) {
        node_limits.push({
          pipeline_key: pipelineKey,
          node_key: nodeKey,
          concurrency_limit: limit,
        })
      }
      const nextConfiguration = {
        ...state.executorConfiguration,
        node_limits,
      }
      const nextState = { ...state, executorConfiguration: nextConfiguration }
      return { ...nextState, isDirty: computeDirty(nextState) }
    })
  },

  async fetchSettings(workspaceId) {
    try {
      const [
        workspaceResult,
        settingsResult,
        catalogResult,
        executorConfigurationResult,
      ] = await Promise.all([
        api<{ workspace: { name: string; description?: string } }>(
          `/api/workspaces/${encodeURIComponent(workspaceId)}`
        ),
        api<
          Partial<WorkspaceSettings> | { settings: Partial<WorkspaceSettings> }
        >(`/api/workspaces/${encodeURIComponent(workspaceId)}/settings`),
        getExecutorCatalog(),
        getWorkspaceExecutorConfiguration(workspaceId),
      ])
      const workspaceData = workspaceResult?.workspace
      const data =
        settingsResult &&
        typeof settingsResult === 'object' &&
        'settings' in settingsResult
          ? (settingsResult.settings as Partial<WorkspaceSettings>)
          : (settingsResult as Partial<WorkspaceSettings>)
      const nextSettings = { ...defaultSettings, ...get().settings, ...data }
      const nextCatalog = catalogResult?.executors ?? []
      const nextExecutorConfiguration = normalizeExecutorConfiguration(
        executorConfigurationResult
      )
      set((state) => {
        const nextState = {
          ...state,
          workspaceName: workspaceData?.name || '',
          workspaceDescription: workspaceData?.description || '',
          originalWorkspaceName: workspaceData?.name || '',
          originalWorkspaceDescription: workspaceData?.description || '',
          settings: nextSettings,
          originalSettings: nextSettings,
          executorCatalog: nextCatalog,
          executorConfiguration: nextExecutorConfiguration,
          originalExecutorConfiguration: nextExecutorConfiguration,
        }
        return { ...nextState, isDirty: computeDirty(nextState) }
      })
    } catch (err) {
      const status =
        err && typeof err === 'object' && 'status' in err
          ? Number((err as { status?: unknown }).status)
          : undefined
      if (status === 404) {
        return
      }
      const message = err instanceof Error ? err.message : '加载设置失败'
      set({ saveError: message })
    }
  },

  async fetchGlobalServices() {
    try {
      const result = await api<{ cms: GlobalServiceStatus['cms'] }>(
        '/api/global-services'
      )
      if (result && typeof result === 'object' && 'cms' in result) {
        set({ globalServices: result as GlobalServiceStatus })
      }
    } catch {
      // Silently fail; global services are informational
    }
  },

  async fetchResourceProviders() {
    try {
      const result = await api<{
        providers: ResourceProviderDefinition[]
      }>('/api/resource-providers')
      if (result && typeof result === 'object' && 'providers' in result) {
        set({
          resourceProviders: (
            result as { providers: ResourceProviderDefinition[] }
          ).providers,
        })
      }
    } catch {
      // Silently fail
    }
  },

  async fetchPipelineDefinition() {
    const { settings } = get()
    const pipelineKey = settings.pipelineKey || 'question_content'
    try {
      const result = await api<{ pipeline: PipelineDefinitionRecord }>(
        `/api/pipelines/${encodeURIComponent(pipelineKey)}`
      )
      if (
        result &&
        typeof result === 'object' &&
        'pipeline' in result &&
        get().settings.pipelineKey === pipelineKey
      ) {
        set({ pipelineDefinition: result.pipeline })
      }
    } catch {
      // Silently fail
    }
  },

  async saveAll() {
    const {
      workspaceId,
      workspaceName,
      workspaceDescription,
      settings,
      executorConfiguration,
    } = get()
    if (!workspaceId) return
    set({ isSaving: true, saveError: null })
    try {
      const result = await api<{
        workspace: { name: string; description?: string }
        settings: WorkspaceSettings
        executor_configuration: WorkspaceExecutorConfiguration
      }>(`/api/workspaces/${encodeURIComponent(workspaceId)}/configuration`, {
        method: 'PUT',
        body: JSON.stringify({
          name: workspaceName,
          description: workspaceDescription,
          settings,
          executor_allocations: executorConfiguration.allocations.map(
            (allocation) => ({
              executor_id: allocation.executor_id,
              concurrency_limit: allocation.concurrency_limit,
            })
          ),
          node_bindings: executorConfiguration.bindings,
          node_limits: executorConfiguration.node_limits,
        }),
      })
      const savedExecutorConfiguration = normalizeExecutorConfiguration(
        result.executor_configuration
      )
      set({
        workspaceName: result.workspace.name,
        workspaceDescription: result.workspace.description || '',
        settings: result.settings,
        originalWorkspaceName: result.workspace.name,
        originalWorkspaceDescription: result.workspace.description || '',
        originalSettings: result.settings,
        executorConfiguration: savedExecutorConfiguration,
        originalExecutorConfiguration: savedExecutorConfiguration,
        isDirty: false,
      })
      useUiStore.getState().showToast('设置已保存', 'success')
    } catch (err) {
      const message = err instanceof Error ? err.message : '保存失败'
      set({ saveError: message })
      useUiStore.getState().showToast(message, 'error')
    } finally {
      set({ isSaving: false })
    }
  },

  async testConnection() {
    const { workspaceId } = get()
    if (!workspaceId) return
    set({ testStatus: { state: 'testing' } })
    try {
      const result = await api<{
        ok?: boolean
        message?: string
      }>(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/settings/test-connection`,
        {
          method: 'POST',
        }
      )
      set({
        testStatus: {
          state: 'success',
          message: result.message || '连接成功',
        },
      })
      useUiStore.getState().showToast('连接成功', 'success')
    } catch (err) {
      const message = err instanceof Error ? err.message : '连接测试失败'
      set({ testStatus: { state: 'failed', message } })
      useUiStore.getState().showToast('连接测试失败：' + message, 'error')
    }
  },

  resetTestStatus() {
    set({ testStatus: { state: 'idle' } })
  },
}))
