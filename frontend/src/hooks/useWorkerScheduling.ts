import { useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useAgentsStore } from '../stores/agentsStore'
import { useUiStore } from '../stores/uiStore'

/** 顶栏、引导与排查入口共用恢复/暂停结果反馈，失败保持现有状态。 */
export function useWorkerScheduling(workspaceId: string | undefined) {
  const setWorkerPaused = useAgentsStore((s) => s.setWorkerPaused)
  const showToast = useUiStore((s) => s.showToast)
  const client = useQueryClient()
  return useCallback(
    async (paused: boolean) => {
      if (!workspaceId) return
      try {
        await setWorkerPaused(paused, workspaceId)
        showToast(paused ? '已暂停运行' : '已恢复运行', 'success')
        void client.invalidateQueries({
          queryKey: ['workerReadinessStatus', workspaceId],
        })
      } catch {
        showToast('更新失败，请重试', 'error')
      }
    },
    [setWorkerPaused, workspaceId, showToast, client]
  )
}
