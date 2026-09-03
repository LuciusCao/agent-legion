import { useCallback, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  cancelPublishRequest,
  confirmPublishRequest,
  fetchPendingPublishRequest,
  type StudioPublishRequestRecord,
} from '../../../api/studioPublishRequestApi'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { useUiStore } from '../../../stores/uiStore'

/** 轮询间隔：agent 发起请求到人点开 Studio 之间没有推送通道，5 秒轮询在
 * 「弹窗及时」与「请求量」之间取折中（与后端 10 分钟 TTL 相比足够密）。 */
const POLL_INTERVAL_MS = 5_000

export type AgentPublishRequestState = {
  /** 当前 pending 的 agent 发布请求；null 表示没有。 */
  pendingRequest: StudioPublishRequestRecord | null
  /** 用户已交互（确认/取消后的落地回执），用于在对话面板显示结果提示。 */
  resolvedNotice: string | null
  confirming: boolean
  /** 用户确认：走后端确认端点（与手动发布同门禁），成功后失效相关查询。 */
  confirm: () => Promise<void>
  /** 用户取消：请求落 rejected，agent 可继续修改草稿再发起。 */
  cancel: () => Promise<void>
  clearNotice: () => void
}

/** Agent 发起的 workflow 发布请求（#416）：轮询 workspace 的 pending 请求，
 * 有则由 AgentPublishRequestDialog 弹出发布确认对话框（复用 compare
 * summary 数据流）。确认/取消只由用户会话触发——agent 的 scoped token 在
 * 后端被 reject_studio_agent_scope 挡在门外。多个组件（对话框/对话栏）各自
 * 调用本 hook：相同 queryKey 的 useQuery 自动合并为一次轮询。 */
export function useAgentPublishRequest(
  workspaceId: string | undefined
): AgentPublishRequestState {
  const queryClient = useQueryClient()
  const showToast = useUiStore((s) => s.showToast)
  const [confirming, setConfirming] = useState(false)
  const [resolvedNotice, setResolvedNotice] = useState<string | null>(null)

  const { data: pendingRequest, refetch: refetchPending } = useQuery({
    queryKey: extraQueryKeys.studioPublishRequest(workspaceId ?? ''),
    queryFn: () => fetchPendingPublishRequest(workspaceId!),
    enabled: Boolean(workspaceId),
    refetchInterval: POLL_INTERVAL_MS,
  })
  const requestId = pendingRequest?.id ?? null

  const invalidate = useCallback(async () => {
    if (!workspaceId) return
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: extraQueryKeys.studioPublishRequest(workspaceId),
      }),
      queryClient.invalidateQueries({
        queryKey: extraQueryKeys.workflowStudioData(workspaceId),
      }),
    ])
  }, [queryClient, workspaceId])

  const confirm = useCallback(async () => {
    if (!workspaceId || !requestId || confirming) return
    setConfirming(true)
    try {
      const resolved = await confirmPublishRequest(workspaceId, requestId)
      setResolvedNotice(
        resolved.result_revision_id
          ? `已按 Agent 请求发布（revision ${resolved.result_revision_id}）`
          : '已按 Agent 请求保存节点运行配置'
      )
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
  }, [
    workspaceId,
    requestId,
    confirming,
    invalidate,
    showToast,
    refetchPending,
  ])

  const cancel = useCallback(async () => {
    if (!workspaceId || !requestId) return
    try {
      await cancelPublishRequest(workspaceId, requestId)
      setResolvedNotice('已取消 Agent 的发布请求，Agent 可继续修改草稿')
      await invalidate()
    } catch (error) {
      showToast(
        `取消失败：${(error instanceof Error && error.message) || '网络错误'}`,
        'error'
      )
      await refetchPending()
    }
  }, [workspaceId, requestId, invalidate, showToast, refetchPending])

  return {
    pendingRequest: pendingRequest ?? null,
    resolvedNotice,
    confirming,
    confirm,
    cancel,
    clearNotice: () => setResolvedNotice(null),
  }
}
