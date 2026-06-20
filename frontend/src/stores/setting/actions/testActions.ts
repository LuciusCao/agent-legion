import { api } from '../../../api'
import { type SettingState, type SettingStoreSet } from '../state'
import { useUiStore } from '../../uiStore'

export function testActions(set: SettingStoreSet, get: () => SettingState) {
  return {
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
  }
}
