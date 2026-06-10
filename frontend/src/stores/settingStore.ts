import { create } from 'zustand'
import type {
  WorkspaceSettings,
  GlobalServiceStatus,
  ResourceProviderDefinition,
  PipelineDefinitionRecord,
  WorkspaceAgentAssignment,
} from '../types'
import { api, assignAgent, unassignAgent } from '../api'
import { useUiStore } from './uiStore'

type TestStatus = {
  state: 'idle' | 'testing' | 'success' | 'failed'
  message?: string
}

type SettingState = {
  workspaceId: string | null
  workspaceName: string
  workspaceDescription: string
  settings: WorkspaceSettings
  agentAssignments: WorkspaceAgentAssignment[] | null
  originalWorkspaceName: string
  originalWorkspaceDescription: string
  originalSettings: WorkspaceSettings | null
  originalAgentAssignments: WorkspaceAgentAssignment[] | null
  isDirty: boolean
  isSaving: boolean
  saveError: string | null
  globalServices: GlobalServiceStatus | null
  resourceProviders: ResourceProviderDefinition[]
  pipelineDefinition: PipelineDefinitionRecord | null
  testStatus: TestStatus
  setWorkspaceId: (id: string) => void
  setWorkspaceName: (name: string) => void
  setWorkspaceDescription: (description: string) => void
  setSettings: (s: Partial<WorkspaceSettings>) => void
  setAgentAssignments: (assignments: WorkspaceAgentAssignment[] | null) => void
  fetchSettings: (workspaceId: string) => Promise<void>
  fetchAgentAssignments: (workspaceId: string) => Promise<void>
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
  agentIds: [],
  concurrencyLimit: 1,
  resources: {},
}

function computeDirty(state: Omit<SettingState, 'isDirty'>): boolean {
  if (state.originalSettings === null) return false
  if (state.workspaceName !== state.originalWorkspaceName) return true
  if (state.workspaceDescription !== state.originalWorkspaceDescription)
    return true
  if (JSON.stringify(state.settings) !== JSON.stringify(state.originalSettings))
    return true
  if (
    JSON.stringify(state.agentAssignments) !==
    JSON.stringify(state.originalAgentAssignments)
  )
    return true
  return false
}

export const useSettingStore = create<SettingState>((set, get) => ({
  workspaceId: null,
  workspaceName: '',
  workspaceDescription: '',
  settings: defaultSettings,
  agentAssignments: null,
  originalWorkspaceName: '',
  originalWorkspaceDescription: '',
  originalSettings: null,
  originalAgentAssignments: null,
  isDirty: false,
  globalServices: null,
  resourceProviders: [],
  pipelineDefinition: null,
  testStatus: { state: 'idle' },
  isSaving: false,
  saveError: null,

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
      const nextState = { ...state, settings: nextSettings }
      return { ...nextState, isDirty: computeDirty(nextState) }
    })
  },

  setAgentAssignments(assignments) {
    set((state) => {
      const nextState = { ...state, agentAssignments: assignments }
      return { ...nextState, isDirty: computeDirty(nextState) }
    })
  },

  async fetchSettings(workspaceId) {
    try {
      const [workspaceResult, settingsResult] = await Promise.all([
        api<{ workspace: { name: string; description?: string } }>(
          `/api/workspaces/${encodeURIComponent(workspaceId)}`
        ),
        api<
          Partial<WorkspaceSettings> | { settings: Partial<WorkspaceSettings> }
        >(`/api/workspaces/${encodeURIComponent(workspaceId)}/settings`),
      ])
      const workspaceData = workspaceResult?.workspace
      const data =
        settingsResult &&
        typeof settingsResult === 'object' &&
        'settings' in settingsResult
          ? (settingsResult.settings as Partial<WorkspaceSettings>)
          : (settingsResult as Partial<WorkspaceSettings>)
      set((state) => {
        const nextSettings = { ...defaultSettings, ...state.settings, ...data }
        const nextState = {
          ...state,
          workspaceName: workspaceData?.name || '',
          workspaceDescription: workspaceData?.description || '',
          originalWorkspaceName: workspaceData?.name || '',
          originalWorkspaceDescription: workspaceData?.description || '',
          settings: nextSettings,
          originalSettings: nextSettings,
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

  async fetchAgentAssignments(workspaceId) {
    try {
      const result = await api<{ agents: WorkspaceAgentAssignment[] }>(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/agents`
      )
      const assignments = result?.agents || null
      set((state) => {
        const nextState = {
          ...state,
          agentAssignments: assignments,
          originalAgentAssignments: assignments,
        }
        return { ...nextState, isDirty: computeDirty(nextState) }
      })
    } catch {
      // Silently fail
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
      if (result && typeof result === 'object' && 'pipeline' in result) {
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
      agentAssignments,
      originalAgentAssignments,
    } = get()
    if (!workspaceId) return
    set({ isSaving: true, saveError: null })
    try {
      // 1. PATCH name/description
      await api(`/api/workspaces/${encodeURIComponent(workspaceId)}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: workspaceName,
          description: workspaceDescription,
        }),
      })
      // 2. PATCH resource_config/intake_config/default_entity
      await api(`/api/workspaces/${encodeURIComponent(workspaceId)}`, {
        method: 'PATCH',
        body: JSON.stringify({
          resource_config: { resources: settings.resources },
          intake_config: {
            enabled_modes: settings.intakeModes,
            label_overrides: settings.labelOverrides,
          },
          default_entity: settings.entityType,
        }),
      })
      // 3. PATCH pipeline settings
      await api(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/settings/pipeline`,
        {
          method: 'PATCH',
          body: JSON.stringify({
            pipelineKey: settings.pipelineKey,
            localConcurrency: settings.localConcurrency,
            agentConcurrency: settings.agentConcurrency,
          }),
        }
      )
      // 4. POST agent assignments one by one
      if (agentAssignments) {
        for (const assignment of agentAssignments) {
          await assignAgent(
            workspaceId,
            assignment.agent_id,
            assignment.concurrency_limit
          )
        }
      }
      // Unassign removed agents
      if (originalAgentAssignments) {
        const currentIds = new Set(
          agentAssignments?.map((a) => a.agent_id) || []
        )
        for (const original of originalAgentAssignments) {
          if (!currentIds.has(original.agent_id)) {
            await unassignAgent(workspaceId, original.agent_id)
          }
        }
      }
      await get().fetchAgentAssignments(workspaceId)
      useUiStore.getState().showToast('设置已保存', 'success')
      // Refresh originals
      await get().fetchSettings(workspaceId)
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
