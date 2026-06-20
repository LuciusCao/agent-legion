import { api } from '../../../api'
import {
  getExecutorCatalog,
  getWorkspaceExecutorConfiguration,
} from '../../../executorApi'
import type {
  WorkspaceSettings,
  GlobalServiceStatus,
  ResourceProviderDefinition,
  WorkflowDefinitionRecord,
} from '../../../types'
import {
  computeDirty,
  defaultSettings,
  normalizeExecutorConfiguration,
  type SettingState,
  type SettingStoreSet,
} from '../state'

export function loadActions(set: SettingStoreSet, get: () => SettingState) {
  return {
    async fetchSettings(workspaceId: string) {
      set({ saveError: null })
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
            | Partial<WorkspaceSettings>
            | { settings: Partial<WorkspaceSettings> }
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
        const nextSettings = { ...defaultSettings, ...data }
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

    async fetchWorkflowDefinition() {
      const { settings } = get()
      const workflowKey = settings.workflowKey || 'question_content'
      try {
        const result = await api<{ workflow: WorkflowDefinitionRecord }>(
          `/api/workflows/${encodeURIComponent(workflowKey)}`
        )
        if (
          result &&
          typeof result === 'object' &&
          'workflow' in result &&
          get().settings.workflowKey === workflowKey
        ) {
          set({ workflowDefinition: result.workflow })
        }
      } catch {
        // Silently fail
      }
    },
  }
}
