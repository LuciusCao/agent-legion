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
      // 重入守卫：并发 PUT 乱序会让先发的旧响应回写覆盖新快照；调用方靠
      // isDirty 仍为 true 感知未保存状态。返回值告知保存是否成功（绑定编辑
      // 器据此回滚草稿）。
      if (!workspaceId || get().isSaving) return false
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
        return true
      } catch (err) {
        const message = err instanceof Error ? err.message : '保存失败'
        set({ saveError: message })
        useUiStore.getState().showToast(message, 'error')
        return false
      } finally {
        set({ isSaving: false })
      }
    },
  }
}
