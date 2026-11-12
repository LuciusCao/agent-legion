import { useQuery } from '@tanstack/react-query'
import {
  fetchPendingPublishRequest,
  type StudioPublishRequestRecord,
} from '../../../api/studioPublishRequestApi'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { useAgentPublishNoticeStore } from './agentPublishNoticeStore'
import { useAgentPublishRequestActions } from './useAgentPublishRequestActions'
import { useAgentPublishRequestSync } from './useAgentPublishRequestSync'

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
  /** 用户确认：走后端确认端点（与手动发布同门禁），成功后失效相关查询。
   * #1122：onConfirmed 在端点成功后、invalidate 触发重取之前同步调用——
   * 对话框借此把发布的草稿原文登记进 justPublishedRef（复用手动发布管道
   * 的收尾），否则紧随的 canonical 新基线被 baseline sync 误判为外部
   * 变更，preserveDirtyDraft 永真、dirty chip 卡在「有未发布变更」。 */
  confirm: (onConfirmed?: () => void) => Promise<void>
  /** 用户取消：请求落 rejected，agent 可继续修改草稿再发起。404（输给
   * confirm 的 claim / 已被顶替 / 已过期）静默 refetch——不弹「取消失败」
   * （#429 收尾 P3：对「输给 confirm」是误导，行态自己说话）。 */
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
  const clearNotice = useAgentPublishNoticeStore((s) => s.clearNotice)
  const resolvedNotice = useAgentPublishNoticeStore((s) => s.resolvedNotice)

  const { data: pendingRequest, refetch: refetchPending } = useQuery({
    queryKey: extraQueryKeys.studioPublishRequest(workspaceId ?? ''),
    queryFn: ({ signal }) => fetchPendingPublishRequest(workspaceId!, signal),
    enabled: Boolean(workspaceId),
    refetchInterval: POLL_INTERVAL_MS,
  })
  const requestId = pendingRequest?.id ?? null
  const invalidate = useAgentPublishRequestSync(workspaceId, pendingRequest)
  const { confirming, canceling, confirm, cancel } =
    useAgentPublishRequestActions({
      workspaceId,
      requestId,
      invalidate,
      refetchPending,
    })

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
