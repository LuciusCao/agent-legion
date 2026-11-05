import { useCallback, useState } from 'react'
import {
  cancelPublishRequest,
  confirmPublishRequest,
} from '../../../api/studioPublishRequestApi'
import { useUiStore } from '../../../stores/uiStore'
import {
  agentPublishStatusNotice,
  useAgentPublishNoticeStore,
} from './agentPublishNoticeStore'

export function useAgentPublishRequestActions({
  workspaceId,
  requestId,
  invalidate,
  refetchPending,
}: {
  workspaceId: string | undefined
  requestId: string | null
  invalidate: () => Promise<void>
  refetchPending: () => Promise<unknown>
}) {
  const showToast = useUiStore((s) => s.showToast)
  const landNotice = useAgentPublishNoticeStore((s) => s.landNotice)
  const markResolved = useAgentPublishNoticeStore((s) => s.markResolved)
  const [confirming, setConfirming] = useState(false)
  // 三轮复审 P3：cancel 的在途守卫，与 confirming 对称。
  const [canceling, setCanceling] = useState(false)

  const confirm = useCallback(
    async (onConfirmed?: () => void) => {
      if (!workspaceId || !requestId || confirming) return
      setConfirming(true)
      try {
        const resolved = await confirmPublishRequest(workspaceId, requestId)
        markResolved(requestId)
        // #1122：登记发布原文必须在 invalidate 的重取落地之前——基线变化
        // 紧随而来，baseline sync 靠 justPublishedRef 区分「自己刚发布」
        // 与「外部变更」。
        onConfirmed?.()
        const notice = agentPublishStatusNotice(resolved)
        if (notice) landNotice(notice)
        await invalidate()
      } catch (error) {
        showToast(
          `确认发布失败：${(error instanceof Error && error.message) || '网络错误'}`,
          'error'
        )
        // 失败（如草稿校验不过）后刷新 pending：请求可能仍在（可修复后重试）
        // 也可能已过期（后端返回 404 → 弹窗由轮询自然关闭）。
        await refetchPending()
      } finally {
        setConfirming(false)
      }
    },
    [
      workspaceId,
      requestId,
      confirming,
      invalidate,
      showToast,
      refetchPending,
      landNotice,
      markResolved,
    ]
  )

  const cancel = useCallback(async () => {
    if (!workspaceId || !requestId || canceling) return
    setCanceling(true)
    try {
      const resolved = await cancelPublishRequest(workspaceId, requestId)
      markResolved(requestId)
      const notice = agentPublishStatusNotice(resolved)
      if (notice) landNotice(notice)
      await invalidate()
    } catch (error) {
      // #429 收尾 P3：404 = 请求已不在（输了给 confirm 的 claim / 已被顶替
      // / 已过期）——后端契约故意用 404 回答，不是操作失败。「取消失败」
      // 的红 toast 对「输给 confirm」是误导（发布正在进行，取消本来就该
      // 不生效）；静默 refetchPending，行态自己说话（confirming/confirmed
      // 的回执或对话框状态会自然呈现）。其他错误（网络/5xx）照旧提示。
      if ((error as { status?: number }).status !== 404) {
        showToast(
          `取消失败：${(error instanceof Error && error.message) || '网络错误'}`,
          'error'
        )
      }
      await refetchPending()
    } finally {
      setCanceling(false)
    }
  }, [
    workspaceId,
    requestId,
    canceling,
    invalidate,
    showToast,
    refetchPending,
    landNotice,
    markResolved,
  ])

  return { confirming, canceling, confirm, cancel }
}
