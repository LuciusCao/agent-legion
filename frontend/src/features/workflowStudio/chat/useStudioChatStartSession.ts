import { useCallback, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import type { QueryClient } from '@tanstack/react-query'
import { queryKeys } from '../../../lib/queryKeys'
import {
  createStudioChatSession,
  type StudioChatSessionRecord,
} from './studioChatApi'

/** startSession 与其瞬态（starting / startTokenRef），拆自 useStudioChat
 * （文件预算）。
 * 归属守卫是双因子：workspaceIdRef 管 workspace 维度（#796 复审轮），
 * workspaceEpochRef 管「往返」（#1265）——A→B→A 后 workspaceIdRef 回到
 * 同值，单因子守卫被击败，旧创建的成功落地会短暂劫持会话选择
 * （setActiveSessionId('s-new-1')）；epoch 只在 workspace 重置效应里递增，
 * 往返必然推进。不用 sessionGenerationRef：它随任何会话选择变化递增
 * （含回到 ws1 时记忆恢复对 s1 的自动重选）——那会把往返后新发起的合法
 * 创建也挡掉（useStudioChatPaginationRace 的 #1237 P2-2 用例钉着这个
 * 行为）。与 finally 的 startTokenRef 语义不冲突：token 管 starting 锁的
 * 释放，epoch 管落地守卫。
 * 失败/释锁路径保持 workspace 单因子：A→B→A 的迟到失败落在 A 上仍是
 * 本 workspace 的真实失败，提示不丢（落地守卫只管成功写入）。 */
export function useStudioChatStartSession({
  workspaceId,
  workspaceIdRef,
  workspaceEpochRef,
  queryClient,
  setSession,
  setActiveSessionId,
  setActionError,
}: {
  workspaceId: string | undefined
  workspaceIdRef: MutableRefObject<string | undefined>
  workspaceEpochRef: MutableRefObject<number>
  queryClient: QueryClient
  setSession: (session: StudioChatSessionRecord) => void
  setActiveSessionId: (id: string) => void
  setActionError: (message: string | null) => void
}) {
  const [starting, setStarting] = useState(false)
  // 创建请求 token（#1237 codex P2-2 族）：starting 的 finally 释放只认持有者。
  const startTokenRef = useRef(0)

  // 返回本次创建的成败（#801 codex 轮 7 根因方案）：useJobDiagnosis 的
  // 失败闩锁只采信本次尝试的失败——无关的历史加载错误（恢复会话的消息
  // 拉取失败）不采。
  const startSession = useCallback(
    async (agentId: string): Promise<boolean | undefined> => {
      if (!workspaceId || starting) return
      const token = ++startTokenRef.current
      // #1265：发起时快照 workspace 往返代次，两个异步窗口落地前与
      // workspaceId 一起双检。
      const epoch = workspaceEpochRef.current
      setStarting(true)
      try {
        const created = await createStudioChatSession(workspaceId, agentId)
        // 迟到响应归属守卫（applyResumedSession 的 activeSessionIdRef 同款
        // 模式）：落地前已切换 workspace（含往返）则丢弃——不得把 A 的会话
        // 写进 B 的状态。workspaceId 是调用帧闭包值，与当前 ref 比对即
        // 「发起时快照」语义。
        if (
          workspaceIdRef.current !== workspaceId ||
          workspaceEpochRef.current !== epoch
        )
          return
        await queryClient.invalidateQueries({
          queryKey: queryKeys.studioChatSessions(workspaceId),
        })
        // 第二个异步窗口（codex P2 第六轮）：invalidate 的 refetch 在途时
        // 也可能切了 workspace——写入前复检，上面的守卫只盖住第一个 await。
        if (
          workspaceIdRef.current !== workspaceId ||
          workspaceEpochRef.current !== epoch
        )
          return
        setSession(created)
        setActiveSessionId(created.id)
        return true
      } catch (error) {
        // 迟到失败同样不落：错误只对发起时的 workspace 可见。
        if (workspaceIdRef.current === workspaceId)
          setActionError(error instanceof Error ? error.message : '操作失败')
        return false
      } finally {
        // 只许持有 token 的创建释放 starting（#1237 codex P2-2 族）：
        // workspace 往返后早先创建的 finally 不得清掉新一轮在途创建。
        const owns = startTokenRef.current === token
        if (owns && workspaceIdRef.current === workspaceId) setStarting(false)
      }
    },
    [
      workspaceId,
      starting,
      workspaceIdRef,
      workspaceEpochRef,
      queryClient,
      setSession,
      setActiveSessionId,
      setActionError,
    ]
  )

  // 切换 workspace 时复位「创建中」瞬态（useStudioChat 的 workspace 重置
  // 效应调用）：A 的 starting 不得泄漏进 B 的按钮。
  const resetStarting = useCallback(() => setStarting(false), [])

  return { startSession, starting, resetStarting }
}
