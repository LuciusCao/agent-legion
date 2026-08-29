/**
 * workspace 级产物预览勾选配置（issue #11 第 3 层）。
 * 读共享 workspaceSettings 缓存；写 = 乐观 PATCH settings/preview（失败
 * 回滚 + toast）。勾选语义 = !hidden.includes(name)：默认空列表 = 全部
 * 显示，工作流升级产生的新产物自动可见。
 * 并发勾选串行化执行（codex P2）：全量列表 PATCH 乱序完成会以旧基线
 * 覆盖新变更；队列内前序失败只回滚自己那一步的基线。
 */
import { useCallback, useMemo, useRef } from 'react'
import type { JobDetail } from '../types/jobTypes'
import { useQueryClient } from '@tanstack/react-query'
import { runPreviewToggle } from '../lib/workspacePreviewMutation'
import { useUiStore } from '../stores/uiStore'
import { useWorkspaceSettingsQuery } from './useWorkspaceSettingsQuery'

export function useWorkspacePreviewConfig(
  workspaceId: string | null | undefined
) {
  const queryClient = useQueryClient()
  const { data: snapshot } = useWorkspaceSettingsQuery(workspaceId)
  const showToast = useUiStore((s) => s.showToast)
  const pendingPatch = useRef<Promise<void>>(Promise.resolve())

  const previewHidden = useMemo(
    () => snapshot?.settings?.previewHidden ?? [],
    [snapshot?.settings?.previewHidden]
  )

  /** 切换某个产物的显示/隐藏（勾上 = 显示 = 从 hidden 移除）。 */
  const toggleArtifact = useCallback(
    (artifactName: string, visible: boolean) => {
      if (!workspaceId) return
      pendingPatch.current = pendingPatch.current
        .catch(() => {})
        .then(() =>
          runPreviewToggle(
            workspaceId,
            artifactName,
            visible,
            queryClient,
            showToast
          )
        )
    },
    [workspaceId, queryClient, showToast]
  )

  /** 过滤出可见产物（hidden 语义归口本 hook）。 */
  const visibleArtifacts = useCallback(
    (artifacts: JobDetail['artifacts']) =>
      artifacts.filter((name) => !previewHidden.includes(name)),
    [previewHidden]
  )

  return { previewHidden, toggleArtifact, visibleArtifacts }
}
