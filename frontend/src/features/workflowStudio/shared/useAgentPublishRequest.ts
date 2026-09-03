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
  /** 当前 pending/confirming 的 agent 发布请求；null 表示没有。confirming
   * 行也返回（#429 四轮 P3-2）：confirm 在途时轮询不再返回 null——对话框
   * 保持打开显示「发布进行中」，而不是中途消失。 */
  pendingRequest: StudioPublishRequestRecord | null
  /** 用户已交互（确认/取消）或请求被顶替后的落地回执（跨实例共享：见
   * agentPublishNoticeStore——栏顶 StudioChatAside 与对话框读同一份）。 */
  resolvedNotice: string | null
  /** confirm 整体在途（#429 四轮 P2-1/P3-2）：覆盖 flush + 重读 + 端点
   * 调用整窗——期间轮询可能先送来 confirming 行，对话框据它显示「发布
   * 进行中」而非消失；替代旧 confirming（仅端点段）。 */
  publishInFlight: boolean
  /** cancel 在途（#429 三轮复审 P3）：在途期间重复 cancel 早退——二次
   * cancel 必 404，红 toast 与正确回执同现是假失败。 */
  canceling: boolean
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
 * 调用本 hook：相同 queryKey 的 useQuery 自动合并为一次轮询；落定回执与
 * resolve 归属（#429 复审）都走 agentPublishNoticeStore（zustand store，
 * 跨实例共享），invalidate 后所有实例同时看到 pending→null，旁观实例的
 * 观测 effect 靠 store 的 lastResolvedRequestId 识别「已被主动 resolve」，
 * 不再拿「已消解」覆盖操作实例着陆的正确回执。 */
export function useAgentPublishRequest(
  workspaceId: string | undefined
): AgentPublishRequestState {
  const queryClient = useQueryClient()
  const showToast = useUiStore((s) => s.showToast)
  const landNotice = useAgentPublishNoticeStore((s) => s.landNotice)
  const clearNotice = useAgentPublishNoticeStore((s) => s.clearNotice)
  const resolvedNotice = useAgentPublishNoticeStore((s) => s.resolvedNotice)
  const markResolved = useAgentPublishNoticeStore((s) => s.markResolved)
  const [confirming, setConfirming] = useState(false)
  // 三轮复审 P3：cancel 的在途守卫，与 confirming 对称（见类型注释）。
  const [canceling, setCanceling] = useState(false)
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

  // 观测 pending→null 的跳变：跳变的请求若已被本 app 内任一实例主动
  // resolve（store 的 lastResolvedRequestId 命中——#429 二轮复审：per-
  // instance ref 挡不住旁观者，对话框与栏顶共享 QueryClient，invalidate
  // 后双方同时收到跳变，旁观实例会拿「已消解」覆盖操作实例刚着陆的正确
  // 回执），则回执已由操作响应着陆，这里不动；否则是后端被顶替（agent
  // 重发新请求 / 手动发布取代）或 TTL 过期，弹窗无声关闭——补一轮回执。
  // confirming 行不算跳变（#429 四轮 P3-2）：confirm 在途时轮询会送来
  // status='confirming' 的同一请求——它是「发布进行中」，不是「已消解」，
  // 着陆回执的必须是真正终态的 null 跳变。
  useEffect(() => {
    if (pendingRequest) {
      if (pendingRequest.status === 'confirming') return
      lastSeenPendingId.current = pendingRequest.id
      return
    }
    const lastId = lastSeenPendingId.current
    if (!lastId) return
    lastSeenPendingId.current = null
    if (
      useAgentPublishNoticeStore.getState().lastResolvedRequestId !== lastId
    ) {
      landNotice('Agent 的发布请求已消解（被新请求或手动发布取代，或已过期）')
    }
  }, [pendingRequest, landNotice])

  const confirm = useCallback(async () => {
    if (!workspaceId || !requestId || confirming) return
    setConfirming(true)
    try {
      const resolved = await confirmPublishRequest(workspaceId, requestId)
      markResolved(requestId)
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
    markResolved,
  ])

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
      showToast(
        `取消失败：${(error instanceof Error && error.message) || '网络错误'}`,
        'error'
      )
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

  return {
    pendingRequest: pendingRequest ?? null,
    resolvedNotice: resolvedNotice?.message ?? null,
    publishInFlight: confirming,
    canceling,
    confirm,
    cancel,
    clearNotice,
  }
}
