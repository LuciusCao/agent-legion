import { create } from 'zustand'
import type {
  WorkspaceSettings,
  GlobalServiceStatus,
  ResourceProviderDefinition,
  ResourceBinding,
} from '../types'
import { api } from '../api'
import { useUiStore } from './uiStore'

type TestStatus = {
  state: 'idle' | 'testing' | 'success' | 'failed'
  message?: string
}

type SettingSection =
  | 'connection'
  | 'intake'
  | 'pipeline'
  | 'agents'
  | 'resources'

type SettingState = {
  workspaceId: string | null
  settings: WorkspaceSettings
  globalServices: GlobalServiceStatus | null
  resourceProviders: ResourceProviderDefinition[]
  testStatus: TestStatus
  isSaving: boolean
  saveError: string | null
  setWorkspaceId: (id: string) => void
  setSettings: (s: Partial<WorkspaceSettings>) => void
  fetchSettings: (workspaceId: string) => Promise<void>
  fetchGlobalServices: () => Promise<void>
  fetchResourceProviders: () => Promise<void>
  saveSection: (
    section: SettingSection,
    data:
      | Partial<WorkspaceSettings>
      | { resources: Record<string, ResourceBinding> }
  ) => Promise<void>
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

export const useSettingStore = create<SettingState>((set, get) => ({
  workspaceId: null,
  settings: defaultSettings,
  globalServices: null,
  resourceProviders: [],
  testStatus: { state: 'idle' },
  isSaving: false,
  saveError: null,

  setWorkspaceId(id) {
    set({ workspaceId: id })
  },

  setSettings(s) {
    set((state) => ({ settings: { ...state.settings, ...s } }))
  },

  async fetchSettings(workspaceId) {
    try {
      const result = await api<
        Partial<WorkspaceSettings> | { settings: Partial<WorkspaceSettings> }
      >(`/api/workspaces/${encodeURIComponent(workspaceId)}/settings`)
      const data =
        result && typeof result === 'object' && 'settings' in result
          ? (result.settings as Partial<WorkspaceSettings>)
          : (result as Partial<WorkspaceSettings>)
      set((state) => ({
        settings: { ...defaultSettings, ...state.settings, ...data },
      }))
    } catch (err) {
      const status =
        err && typeof err === 'object' && 'status' in err
          ? Number((err as { status?: unknown }).status)
          : undefined
      if (status === 404) {
        // Endpoint not implemented or no settings yet; keep defaults.
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
      const result = await api<{ providers: ResourceProviderDefinition[] }>(
        '/api/resource-providers'
      )
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

  async saveSection(section, data) {
    const { workspaceId } = get()
    if (!workspaceId) return
    set({ isSaving: true, saveError: null })
    try {
      await api(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/settings/${encodeURIComponent(section)}`,
        {
          method: 'PATCH',
          body: JSON.stringify(data),
        }
      )
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
