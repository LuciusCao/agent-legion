import { useCallback, useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { StudioPublishRequestRecord } from '../../../api/studioPublishRequestApi'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { useAgentPublishNoticeStore } from './agentPublishNoticeStore'

/* 发布请求相关查询失效，以及 pending→null 的外部消解观测。主动 confirm/
   cancel 的 resolve 归属由共享 notice store 判定，旁观 hook 实例不得覆盖
   操作实例刚着陆的回执。 */
export function useAgentPublishRequestSync(
  workspaceId: string | undefined,
  pendingRequest: StudioPublishRequestRecord | null | undefined
) {
  const queryClient = useQueryClient()
  const landNotice = useAgentPublishNoticeStore((s) => s.landNotice)
  /** 上一轮见过的 pending id：观测 pending→null 跳变用。 */
  const lastSeenPendingId = useRef<string | null>(null)
  const pendingRequestId = pendingRequest?.id ?? null

  useEffect(() => {
    if (!workspaceId || !pendingRequestId) return
    // #1198：Agent 保存并请求发布时，立即重取服务端草稿；现有 hydrate
    // 只在画布未触碰时应用，本地未保存编辑仍走 CAS 冲突保护。
    void queryClient.invalidateQueries({
      queryKey: extraQueryKeys.workflowStudioDraft(workspaceId),
    })
  }, [pendingRequestId, queryClient, workspaceId])

  const invalidate = useCallback(async () => {
    if (!workspaceId) return
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: extraQueryKeys.studioPublishRequest(workspaceId),
      }),
      queryClient.invalidateQueries({
        queryKey: extraQueryKeys.workflowStudioData(workspaceId),
      }),
      // #1122：补服务端草稿查询——后端哪天发布清草稿时不至于显示旧草稿。
      queryClient.invalidateQueries({
        queryKey: extraQueryKeys.workflowStudioDraft(workspaceId),
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
      // #1122：被手动发布顶替/过期时旁观路径也要刷新 revision 与状态
      // chip——否则另一 tab 的发布要等别的触发才反映到本 tab。
      if (workspaceId) {
        void queryClient.invalidateQueries({
          queryKey: extraQueryKeys.workflowStudioData(workspaceId),
        })
      }
    }
  }, [pendingRequest, landNotice, queryClient, workspaceId])

  return invalidate
}
