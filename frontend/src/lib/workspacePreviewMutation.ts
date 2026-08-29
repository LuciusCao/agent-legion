/**
 * 预览勾选的乐观 mutation（拆自 useWorkspacePreviewConfig 的架构预算）。
 * 串行化由 hook 侧的 promise 队列保证；本函数单步执行：
 * 读基线 → 乐观写缓存 → PATCH → 失败回滚 + toast。
 */
import type { QueryClient } from '@tanstack/react-query'
import { updateWorkspacePreviewHidden } from '../api/workspacePreviewApi'
import { extraQueryKeys } from './queryKeysExtra'
import type { WorkspaceSettingsSnapshot } from '../hooks/useWorkspaceSettingsQuery'

export async function runPreviewToggle(
  workspaceId: string,
  artifactName: string,
  visible: boolean,
  queryClient: QueryClient,
  showToast: (message: string, type: 'success' | 'error') => void
) {
  const queryKey = extraQueryKeys.workspaceSettings(workspaceId)
  const snapshotBefore =
    queryClient.getQueryData<WorkspaceSettingsSnapshot>(queryKey)
  const hiddenBefore = snapshotBefore?.settings?.previewHidden ?? []
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
    // 回滚到本步基线（串行化保证基线包含队列前序的成功变更）。
    if (snapshotBefore) {
      queryClient.setQueryData<WorkspaceSettingsSnapshot>(
        queryKey,
        snapshotBefore
      )
    }
    const reason = err instanceof Error ? err.message : String(err)
    showToast(`预览配置保存失败：${reason}`, 'error')
  }
}
