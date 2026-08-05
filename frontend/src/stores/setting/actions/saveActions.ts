import { api } from '../../../api'
import type { WorkspaceConfigurationResponse } from '../../../types'
import { queryClient } from '../../../lib/queryClient'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
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
        const result = await api<WorkspaceConfigurationResponse>(
          `/api/workspaces/${encodeURIComponent(workspaceId)}/configuration`,
          {
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
              // null = unset：省略该字段，PUT 保留后端已存的 agent_capacity。
              ...(executorConfiguration.agent_capacity != null
                ? { agent_capacity: executorConfiguration.agent_capacity }
                : {}),
            }),
          }
        )
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
        // 失效 settings 缓存，后台 refetch 同步刚保存的服务端状态。
        void queryClient.invalidateQueries({
          queryKey: extraQueryKeys.workspaceSettings(workspaceId),
        })
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
