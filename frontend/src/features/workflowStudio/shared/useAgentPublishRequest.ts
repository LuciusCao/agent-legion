import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  cancelPublishRequest,
  confirmPublishRequest,
  fetchPendingPublishRequest,
  type StudioPublishRequestRecord,
} from '../../../api/studioPublishRequestApi'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { useUiStore } from '../../../stores/uiStore'
import {
  agentPublishStatusNotice,
  useAgentPublishNoticeStore,
} from './agentPublishNoticeStore'

/** 轮询间隔：agent 发起请求到人点开 Studio 之间没有推送通道，5 秒轮询在
 * 「弹窗及时」与「请求量」之间取折中（与后端 10 分钟 TTL 相比足够密）。 */
const POLL_INTERVAL_MS = 5_000

export type AgentPublishRequestState = {
  /** 当前 pending 的 agent 发布请求；null 表示没有。 */
  pendingRequest: StudioPublishRequestRecord | null
  /** 用户已交互（确认/取消）或请求被顶替后的落地回执（跨实例共享：见
   * agentPublishNoticeStore——栏顶 StudioChatAside 与对话框读同一份）。 */
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
 * 调用本 hook：相同 queryKey 的 useQuery 自动合并为一次轮询；落定回执走
 * agentPublishNoticeStore（zustand store，#429 复审修复：原 useState 每个实
 * 例一份，对话框写入的回执对话栏永远读不到）。 */
export function useAgentPublishRequest(
  workspaceId: string | undefined
): AgentPublishRequestState {
  const queryClient = useQueryClient()
  const showToast = useUiStore((s) => s.showToast)
  const landNotice = useAgentPublishNoticeStore((s) => s.landNotice)
  const clearNotice = useAgentPublishNoticeStore((s) => s.clearNotice)
  const resolvedNotice = useAgentPublishNoticeStore((s) => s.resolvedNotice)
  const [confirming, setConfirming] = useState(false)
  /** 本 hook 实例自己 resolve 掉的请求 id（confirm/cancel 成功路径）：
   * pending→null 的跳变若源于自己的操作，回执已由操作响应着陆，观测 effect
   * 不得覆盖；否则（agent 重发 / 手动发布在后端顶替）补一轮回执。 */
  const selfResolvedId = useRef<string | null>(null)
  /** 上一轮见过的 pending id：观测 pending→null 跳变用。 */
  const lastSeenPendingId = useRef<string | null>(null)

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

  // 观测 pending→null 的跳变：自己 resolve 的（selfResolvedId 命中）回执已
  // 着陆；否则是后端被顶替（agent 重发新请求 / 手动发布取代，后端 #429 落
  // 地 supersede）或 TTL 过期，弹窗无声关闭——这里补一轮回执而非静默。
  useEffect(() => {
    if (pendingRequest) {
      lastSeenPendingId.current = pendingRequest.id
      if (selfResolvedId.current === pendingRequest.id)
        selfResolvedId.current = null
      return
    }
    const lastId = lastSeenPendingId.current
    if (!lastId) return
    lastSeenPendingId.current = null
    if (selfResolvedId.current !== lastId) {
      landNotice('Agent 的发布请求已消解（被新请求或手动发布取代，或已过期）')
    } else {
      selfResolvedId.current = null
    }
  }, [pendingRequest, landNotice])

  const confirm = useCallback(async () => {
    if (!workspaceId || !requestId || confirming) return
    setConfirming(true)
    try {
      const resolved = await confirmPublishRequest(workspaceId, requestId)
      selfResolvedId.current = requestId
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
  }, [
    workspaceId,
    requestId,
    confirming,
    invalidate,
    showToast,
    refetchPending,
    landNotice,
  ])

  const cancel = useCallback(async () => {
    if (!workspaceId || !requestId) return
    try {
      const resolved = await cancelPublishRequest(workspaceId, requestId)
      selfResolvedId.current = requestId
      const notice = agentPublishStatusNotice(resolved)
      if (notice) landNotice(notice)
      await invalidate()
    } catch (error) {
      showToast(
        `取消失败：${(error instanceof Error && error.message) || '网络错误'}`,
        'error'
      )
      await refetchPending()
    }
  }, [
    workspaceId,
    requestId,
    invalidate,
    showToast,
    refetchPending,
    landNotice,
  ])

  return {
    pendingRequest: pendingRequest ?? null,
    resolvedNotice: resolvedNotice?.message ?? null,
    confirming,
    confirm,
    cancel,
    clearNotice,
  }
}
