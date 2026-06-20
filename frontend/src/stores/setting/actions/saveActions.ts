import { api } from '../../../api'
import type { WorkspaceSettings } from '../../../types'
import type { WorkspaceExecutorConfiguration } from '../../../executorTypes'
import {
  normalizeExecutorConfiguration,
  type SettingState,
  type SettingStoreSet,
} from '../state'
import { useUiStore } from '../../uiStore'

export function saveActions(set: SettingStoreSet, get: () => SettingState) {
  return {
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
  }
}
