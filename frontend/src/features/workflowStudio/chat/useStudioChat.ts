import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createRealtimeChannel } from '../../../lib/realtime'
import { queryKeys } from '../../../lib/queryKeys'
import { sessionsWithRetention } from './studioChatRetention'
import {
  answerStudioChatPermission,
  createStudioChatSession,
  fetchStudioChatAgents,
  fetchStudioChatMessages,
  fetchStudioChatSession,
  type StudioChatSessionRecord,
} from './studioChatApi'
import {
  handleSseMessageEvent,
  handleSseReconnect,
  isTerminalStatus,
  type SsePayload,
} from './studioChatEvents'
import {
  deriveChatViews,
  lastTerminalEvent,
  maxSeq,
  type ChatMessage,
} from './studioChatMessages'
import { lastRunCancelled } from './studioChatCancelVisibility'
import { mergeMessages } from './studioChatRefill'
import { useStudioChatResume } from './useStudioChatResume'
import {
  isStudioChatBusy,
  useStudioChatRunTiming,
} from './useStudioChatRunTiming'
import { useStudioChatSessionMemory } from './useStudioChatSessionMemory'
import { useStudioChatSessionActions } from './useStudioChatSessionActions'

/** Studio「Agent 助手」对话面板的状态与动作：会话/消息经 REST 拉取，
 * 实时更新走 SSE（message 按 id upsert，session 为状态快照）；SSE
 * 重连或遇到缺 seq 的流式残片时按 after_seq 增量补齐。 */
export function useStudioChat(workspaceId: string | undefined) {
  const queryClient = useQueryClient()
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [session, setSession] = useState<StudioChatSessionRecord | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const messagesRef = useRef<ChatMessage[]>([])
  const activeSessionIdRef = useRef<string | null>(null)
  // 当前 workspace 的 ref 快照（codex P2 复审轮 #796）：startSession 等异步
  // 回调落地前切换 workspace 时，旧 workspace 的迟到响应不得写入本 hook
  // 的 state（本 hook 实例被 react-router 复用，不被重挂）。ref 更新合入
  // 下方 workspaceId 重置 effect（react-hooks/refs 禁止 render 期写 ref）。
  const workspaceIdRef = useRef(workspaceId)
  useEffect(() => {
    messagesRef.current = messages
    activeSessionIdRef.current = activeSessionId
  }, [messages, activeSessionId])

  const agentsQuery = useQuery({
    queryKey: queryKeys.studioChatAgents(workspaceId ?? ''),
    queryFn: () => fetchStudioChatAgents(workspaceId!),
    enabled: Boolean(workspaceId),
  })
  const sessionsQuery = useQuery({
    queryKey: queryKeys.studioChatSessions(workspaceId ?? ''),
    // #1041：响应里的保留天数顺带写入保留天数缓存（会话菜单清理提示用）。
    queryFn: ({ client }) => sessionsWithRetention(client, workspaceId!),
    enabled: Boolean(workspaceId),
  })

  // run 计时：会话状态快照进出 busy 状态时记开始/用时，切换会话重置。
  const sessionStatus = session?.status ?? null
  const runTiming = useStudioChatRunTiming(sessionStatus, activeSessionId)

  const refillMessages = useCallback(
    async (fromSeq?: number) => {
      if (!workspaceId || !activeSessionId) return false
      const sessionId = activeSessionId
      const after = fromSeq ?? maxSeq(messagesRef.current)
      const fetched = await fetchStudioChatMessages(
        workspaceId,
        sessionId,
        after
      )
      // #563：terminal 判定在 updater 外计算——updater 可能被 React 推迟到
      // render 才执行（同 state 有排队更新时），在 updater 内赋值再返回会
      // 拿到恒 false，REST 补齐的自愈被静默跳过；updater 必须是纯函数。
      const hasTerminal =
        activeSessionIdRef.current === sessionId &&
        fetched.some(isTerminalStatus)
      setMessages((current) => {
        // 跨会话竞态：拉取在途时切换了会话，旧会话的消息不得合入新列表；
        // 函数式更新以 current 为基线——并发的 refill(0) 与增量补齐各自
        // 合入，后落者不再回写旧基线覆盖前者（#563）。
        if (activeSessionIdRef.current !== sessionId) return current
        return mergeMessages(current, fetched)
      })
      return hasTerminal
    },
    [workspaceId, activeSessionId]
  )

  // 进入/切换会话：全量拉一次消息与会话快照。
  useEffect(() => {
    if (!workspaceId || !activeSessionId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 会话切换时重置消息/快照（与 useWorkflowStudioDraft 同一模式）
      setMessages([])
      setSession(null)
      return
    }
    let stale = false
    setMessages([])
    // 新建会话后 hook 已持有 snapshot（id 相同），不要被清空闪断。
    setSession((p) => (p && p.id === activeSessionId ? p : null))
    setActionError(null)
    void fetchStudioChatMessages(workspaceId, activeSessionId).then(
      (fetched) => {
        if (!stale) setMessages(fetched)
      },
      () => {
        if (!stale) setActionError('消息加载失败，请稍后重试')
      }
    )
    return () => {
      stale = true
    }
  }, [workspaceId, activeSessionId])

  // 会话快照兜底：列表/事件不可达时以 sessions 查询里的行为准。
  useEffect(() => {
    if (!activeSessionId || session) return
    const fromList = (sessionsQuery.data ?? []).find(
      (row) => row.id === activeSessionId
    )
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 用已加载的会话列表回填快照
    if (fromList) setSession(fromList)
  }, [activeSessionId, session, sessionsQuery.data])

  useEffect(() => {
    if (!workspaceId || !activeSessionId || typeof EventSource === 'undefined')
      return
    // SSE 事件的合入与重连自愈逻辑在 studioChatEvents（#563 拆出，保体积
    // 预算）：message 事件 upsert + terminal 触发全量回取；重连时未终结
    // 流式行直接校准。
    const sseDeps = {
      workspaceId,
      messagesRef,
      queryClient,
      setMessages,
      setSession,
      refillMessages,
      fetchSession: fetchStudioChatSession,
      activeSessionId,
    }
    const channel = createRealtimeChannel({
      url: `/api/workspaces/${encodeURIComponent(workspaceId)}/studio-chat/sessions/${encodeURIComponent(activeSessionId)}/events`,
      protocol: 'sse',
      onEvent: (_type, data) => {
        let payload: SsePayload
        try {
          payload = JSON.parse(data) as SsePayload
        } catch {
          return
        }
        if (payload.type === 'message' && payload.message) {
          handleSseMessageEvent(payload.message, sseDeps)
        } else if (payload.type === 'session' && payload.session) {
          setSession(payload.session)
        }
      },
      onStatus: (status) => {
        if (status === 'open') handleSseReconnect(sseDeps)
      },
    })
    return () => channel.close()
  }, [workspaceId, activeSessionId, refillMessages, queryClient, setSession])

  async function runAction(action: () => Promise<void>) {
    setActionError(null)
    try {
      await action()
      return true
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '操作失败')
      return false
    }
  }

  // 返回本次创建的成败（#801 codex 轮 7 根因方案）：useJobDiagnosis 的
  // 失败闩锁只采信本次尝试的失败——无关的历史加载错误（恢复会话的消息
  // 拉取失败）不采。
  async function startSession(agentId: string): Promise<boolean | undefined> {
    if (!workspaceId || starting) return
    setStarting(true)
    try {
      const created = await createStudioChatSession(workspaceId, agentId)
      // 迟到响应归属守卫（applyResumedSession 的 activeSessionIdRef 同款
      // 模式）：落地前已切换 workspace 则丢弃——不得把 A 的会话写进 B 的
      // 状态（随后的消息拉取会以 B 的 workspace 请求 A 的 session）。
      // workspaceId 是调用帧闭包值，与当前 ref 比对即「发起时快照」语义。
      if (workspaceIdRef.current !== workspaceId) return
      await queryClient.invalidateQueries({
        queryKey: queryKeys.studioChatSessions(workspaceId),
      })
      // 第二个异步窗口（codex P2 第六轮）：invalidate 的 refetch 在途时
      // 也可能切了 workspace——写入前复检，上面的守卫只盖住第一个 await。
      if (workspaceIdRef.current !== workspaceId) return
      setSession(created)
      setActiveSessionId(created.id)
      return true
    } catch (error) {
      // 迟到失败同样不落：错误只对发起时的 workspace 可见。
      if (workspaceIdRef.current === workspaceId)
        setActionError(error instanceof Error ? error.message : '操作失败')
      return false
    } finally {
      if (workspaceIdRef.current === workspaceId) setStarting(false)
    }
  }

  // send / cancel / setAllowAll 带会话归属守卫（#962）：切换会话后旧会话
  // 的迟到结果不写进新会话的状态。
  const { send, cancel, setAllowAll } = useStudioChatSessionActions({
    workspaceId,
    activeSessionId,
    activeSessionIdRef,
    setActionError,
    setMessages,
    setSession,
  })

  async function answerPermission(
    requestId: string,
    answer: { option_id?: string; deny?: boolean }
  ) {
    if (!workspaceId || !activeSessionId) return
    await runAction(async () => {
      await answerStudioChatPermission(
        workspaceId,
        activeSessionId,
        requestId,
        {
          deny: answer.deny ?? false,
          option_id: answer.option_id ?? null,
        }
      )
    })
  }

  const views = useMemo(() => deriveChatViews(messages), [messages])
  const { toolCalls, workflowDraft, nodeDrafts, permissions } = views
  // #675：取消轮收尾视图在姊妹文件（studioChatCancelVisibility），与
  // deriveChatViews 的派生链分开 memo——它只被 RunBar 消费。
  const runCancelled = useMemo(() => lastRunCancelled(messages), [messages])

  // #693：最近一轮的终结类型——RunBar 据此区分「已完成」与「已超时终止」。
  const terminalEvent = useMemo(() => lastTerminalEvent(messages), [messages])

  // 「继续对话」：closed/error 会话重建 runtime（转录/session load 由后端决定）。
  // 响应归属守卫（refillMessages 的 activeSessionIdRef 同款模式）：resume 在途
  // 时切换了会话，旧会话的响应不得覆盖当前选中会话的快照；成功后失效 sessions
  // 列表缓存，否则下拉里该会话长期滞留「（已关闭）」。
  const applyResumedSession = useCallback(
    (resumed: StudioChatSessionRecord) => {
      if (activeSessionIdRef.current !== resumed.id) return
      setSession(resumed)
      void queryClient.invalidateQueries({
        queryKey: queryKeys.studioChatSessions(resumed.workspace_id),
      })
    },
    [queryClient]
  )
  const { resume, resuming } = useStudioChatResume(
    workspaceId,
    activeSessionId,
    runAction,
    applyResumedSession
  )
  // 切换 workspace（React Router 复用组件实例）时清空旧选中：残留 id 会让
  // 记忆恢复效应被 !== null 跳过、写效应把旧 id 写进新 workspace 的记忆。
  // codex P2 复审轮（#796）：一并重置 actionError/starting——A 的错误与
  // 「创建中」态不得泄漏进 B 的头部/按钮；ref 快照同步换到新 workspace
  // （迟到响应守卫的比对基准）。session/messages 由上方入口 effect 随
  // activeSessionId 置空联动复位，这里不重复清。
  useEffect(() => {
    workspaceIdRef.current = workspaceId
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 会话切换时重置选中与瞬态（与上方消息重置同一模式）
    setActiveSessionId(null)
    setActionError(null)
    setStarting(false)
  }, [workspaceId])
  // 按 workspace 记忆选中会话；未选择时恢复上次或回落最近会话。
  useStudioChatSessionMemory(
    workspaceId,
    sessionsQuery.data ?? [],
    activeSessionId,
    setActiveSessionId
  )

  const busy = session ? isStudioChatBusy(session.status) : false

  return {
    agents: agentsQuery.data ?? [],
    agentsLoading: agentsQuery.isLoading,
    agentsError: agentsQuery.isError,
    sessions: sessionsQuery.data ?? [],
    activeSessionId,
    session,
    messages,
    toolCalls,
    workflowDraft,
    nodeDrafts,
    permissions,
    busy,
    closed: session?.status === 'closed' || session?.status === 'error',
    starting,
    // clearActionError（#801 codex 轮 6 P2）：排查引导重试前清上一次创建
    // 失败残留——否则重试成功帧上旧错误会被 useJobDiagnosis 的失败闩锁误采。
    // 会话级清理口，不引入全局语义。
    actionError,
    clearActionError: () => setActionError(null),
    lastRunMs: runTiming.lastMs,
    lastTerminalEvent: terminalEvent,
    lastRunCancelled: runCancelled,
    resume,
    resuming,
    selectSession: setActiveSessionId,
    startSession,
    send,
    cancel,
    setAllowAll,
    answerPermission,
  }
}

export type StudioChat = ReturnType<typeof useStudioChat>
