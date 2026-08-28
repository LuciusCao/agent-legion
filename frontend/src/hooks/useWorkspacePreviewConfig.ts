/**
 * workspace 级产物预览勾选配置（issue #11 第 3 层）。
 *
 * 读：共享 extraQueryKeys.workspaceSettings 缓存（设置页/其他消费方同源），
 * previewHidden 从 settings payload 读取。
 * 写：乐观 setQueryData + PATCH settings/preview，失败回滚并 toast。
 * 勾选语义 = !hidden.includes(name)：默认空列表 = 全部显示，
 * 工作流升级产生的新产物自动可见。
 */
import { useCallback, useMemo } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { updateWorkspacePreviewHidden } from '../api/workspacePreviewApi'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { useUiStore } from '../stores/uiStore'
import { useWorkspaceSettingsQuery } from './useWorkspaceSettingsQuery'
import type { WorkspaceSettingsSnapshot } from './useWorkspaceSettingsQuery'

export function useWorkspacePreviewConfig(workspaceId: string | null | undefined) {
  const queryClient = useQueryClient()
  const { data: snapshot } = useWorkspaceSettingsQuery(workspaceId)
  const showToast = useUiStore((s) => s.showToast)

  const previewHidden = useMemo(
    () => snapshot?.settings?.previewHidden ?? [],
    [snapshot?.settings?.previewHidden]
  )

  /** 切换某个产物的显示/隐藏（勾上 = 显示 = 从 hidden 移除）。 */
  const toggleArtifact = useCallback(
    async (artifactName: string, visible: boolean) => {
      if (!workspaceId) return
      const queryKey = extraQueryKeys.workspaceSettings(workspaceId)
      const snapshotBefore = queryClient.getQueryData<WorkspaceSettingsSnapshot>(queryKey)
      const hiddenBefore =
        snapshotBefore?.settings?.previewHidden ?? []
      const nextHidden = visible
        ? hiddenBefore.filter((name) => name !== artifactName)
        : Array.from(new Set([...hiddenBefore, artifactName])).sort()

      // 乐观更新：失败回滚 + toast，避免勾选状态闪烁。
      if (snapshotBefore) {
        queryClient.setQueryData<WorkspaceSettingsSnapshot>(queryKey, {
          ...snapshotBefore,
          settings: { ...snapshotBefore.settings, previewHidden: nextHidden },
        })
      }
      try {
        await updateWorkspacePreviewHidden(workspaceId, nextHidden)
      } catch (err) {
        if (snapshotBefore) {
          queryClient.setQueryData<WorkspaceSettingsSnapshot>(queryKey, snapshotBefore)
        }
        showToast(
          `预览配置保存失败：${err instanceof Error ? err.message : String(err)}`,
          'error'
        )
      }
    },
    [workspaceId, queryClient, showToast]
  )

  return { previewHidden, toggleArtifact }
}
