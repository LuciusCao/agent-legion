import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createRealtimeChannel } from '../../../lib/realtime'
import { queryKeys } from '../../../lib/queryKeys'
import {
  answerStudioChatPermission,
  cancelStudioChatTurn,
  createStudioChatSession,
  fetchStudioChatAgents,
  fetchStudioChatMessages,
  fetchStudioChatSession,
  fetchStudioChatSessions,
  sendStudioChatMessage,
  setStudioChatAllowAll,
  type StudioChatMessageRecord,
  type StudioChatSessionRecord,
} from './studioChatApi'
import {
  handleSseMessageEvent,
  handleSseReconnect,
  type SsePayload,
} from './studioChatEvents'
import {
  deriveChatViews,
  maxSeq,
  upsertMessage,
  type ChatMessage,
} from './studioChatMessages'
import { mergeMessages } from './studioChatRefill'
import { useStudioChatResume } from './useStudioChatResume'
import {
  isStudioChatBusy,
  useStudioChatRunTiming,
} from './useStudioChatRunTiming'
import { useStudioChatSessionMemory } from './useStudioChatSessionMemory'

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
    queryFn: () => fetchStudioChatSessions(workspaceId!),
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
      let hasTerminal = false
      setMessages((current) => {
        // 跨会话竞态：拉取在途时切换了会话，旧会话的消息不得合入新列表；
        // 函数式更新以 current 为基线——并发的 refill(0) 与增量补齐各自
        // 合入，后落者不再回写旧基线覆盖前者（#563）。
        if (activeSessionIdRef.current !== sessionId) return current
        const merged = mergeMessages(current, fetched)
        hasTerminal = merged.hasTerminal
        return merged.messages
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
    setSession((previous) =>
      previous && previous.id === activeSessionId ? previous : null
    )
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

  async function startSession(agentId: string) {
    if (!workspaceId || starting) return
    setStarting(true)
    await runAction(async () => {
      const created = await createStudioChatSession(workspaceId, agentId)
      await queryClient.invalidateQueries({
        queryKey: queryKeys.studioChatSessions(workspaceId),
      })
      setSession(created)
      setActiveSessionId(created.id)
    })
    setStarting(false)
  }

  // 返回是否发送成功：busy 排队（useStudioChatQueue）flush 失败时要保留
  // 队首，失败原因已置 actionError。
  async function send(text: string) {
    if (!workspaceId || !activeSessionId || !text.trim()) return false
    const sent = await runAction(async () => {
      const message: StudioChatMessageRecord = await sendStudioChatMessage(
        workspaceId,
        activeSessionId,
        text.trim()
      )
      setMessages((current) => upsertMessage(current, message) ?? current)
    })
    return sent
  }

  async function cancel() {
    if (!workspaceId || !activeSessionId) return
    await runAction(async () => {
      setSession(await cancelStudioChatTurn(workspaceId, activeSessionId))
    })
  }

  async function setAllowAll(enabled: boolean) {
    if (!workspaceId || !activeSessionId) return
    await runAction(async () => {
      setSession(
        await setStudioChatAllowAll(workspaceId, activeSessionId, enabled)
      )
    })
  }

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

  const { toolCalls, workflowDraft, agentDrafts, nodeDrafts, permissions } =
    useMemo(() => deriveChatViews(messages), [messages])

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
  // eslint-disable-next-line react-hooks/set-state-in-effect -- 会话切换时重置选中（与上方消息重置同一模式）
  useEffect(() => setActiveSessionId(null), [workspaceId])
  // 按 workspace 记忆选中会话；未选择时恢复上次或回落最近会话。
  useStudioChatSessionMemory(
    workspaceId,
    sessionsQuery.data ?? [],
    activeSessionId,
    setActiveSessionId
  )

  const busy = session ? isStudioChatBusy(session.status) : false
  const closed = session?.status === 'closed' || session?.status === 'error'

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
    agentDrafts,
    nodeDrafts,
    permissions,
    busy,
    closed,
    starting,
    actionError,
    lastRunMs: runTiming.lastMs,
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
