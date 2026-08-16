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
  buildPermissionViews,
  extractAgentDefinitionDrafts,
  extractNodeCodeDrafts,
  extractWorkflowDraft,
  groupToolCalls,
  maxSeq,
  upsertMessage,
  type ChatMessage,
} from './studioChatMessages'

type SsePayload = {
  type?: string
  message?: Partial<ChatMessage> & { id: string }
  session?: StudioChatSessionRecord
}

const BUSY_STATUSES = new Set(['starting', 'running', 'awaiting_permission'])

/** Studio「Agent 助手」对话面板的状态与动作：会话/消息经 REST 拉取，
 * 实时更新走 SSE（message 按 id upsert，session 为状态快照）；SSE
 * 重连或遇到缺 seq 的流式残片时按 after_seq 增量补齐。 */
export function useStudioChat(workspaceId: string | undefined) {
  const queryClient = useQueryClient()
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [session, setSession] = useState<StudioChatSessionRecord | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [starting, setStarting] = useState(false)
  const [runTiming, setRunTiming] = useState<{
    startedAt: number | null
    lastMs: number | null
  }>({ startedAt: null, lastMs: null })
  const messagesRef = useRef<ChatMessage[]>([])
  useEffect(() => {
    messagesRef.current = messages
  }, [messages])
  const activeSessionIdRef = useRef<string | null>(null)
  useEffect(() => {
    activeSessionIdRef.current = activeSessionId
  }, [activeSessionId])

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

  const applySession = useCallback((next: StudioChatSessionRecord) => {
    setSession(next)
  }, [])

  // run 计时：会话状态快照进出 busy 状态时记开始/用时。
  const sessionStatus = session?.status ?? null
  useEffect(() => {
    if (sessionStatus === null) return
    const busy = BUSY_STATUSES.has(sessionStatus)
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 计时器跟随 SSE 会话状态快照翻转
    setRunTiming((previous) => {
      if (busy) {
        return { startedAt: previous.startedAt ?? Date.now(), lastMs: null }
      }
      if (previous.startedAt === null) return previous
      return { startedAt: null, lastMs: Date.now() - previous.startedAt }
    })
  }, [sessionStatus])

  const refillMessages = useCallback(
    async (fromSeq?: number) => {
      if (!workspaceId || !activeSessionId) return
      const sessionId = activeSessionId
      const after = fromSeq ?? maxSeq(messagesRef.current)
      const fetched = await fetchStudioChatMessages(
        workspaceId,
        sessionId,
        after
      )
      setMessages((current) => {
        // 跨会话竞态：拉取在途时切换了会话，旧会话的消息不得合入新列表。
        if (activeSessionIdRef.current !== sessionId) return current
        let next = current
        for (const message of fetched) {
          next = upsertMessage(next, message) ?? next
        }
        return next
      })
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
    setRunTiming({ startedAt: null, lastMs: null })
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
    if (fromList) applySession(fromList)
  }, [activeSessionId, session, sessionsQuery.data, applySession])

  useEffect(() => {
    if (!workspaceId || !activeSessionId || typeof EventSource === 'undefined')
      return
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
          const incoming = payload.message
          // 缺 seq 的流式残片指向未知消息：在 updater 外判定（updater 在
          // StrictMode 下会被双调用，副作用放里面会重复 fetch），增量补齐
          // 而不是丢弃；补齐失败留待下次事件再试，不产生 unhandled rejection。
          const missed = upsertMessage(messagesRef.current, incoming) === null
          setMessages((current) => upsertMessage(current, incoming) ?? current)
          if (missed) {
            void refillMessages().catch(() => undefined)
          }
          // 断连期间的流式 text 尾部会永久截断（原地更新 seq 不变，after_seq
          // 增量补齐拿不到）；turn 结束时全量回取一次自愈。
          const content = incoming.content
          const statusEvent =
            incoming.kind === 'status' &&
            content !== null &&
            typeof content === 'object'
              ? (content as Record<string, unknown>).event
              : undefined
          if (statusEvent === 'turn_end') {
            void refillMessages(0).catch(() => undefined)
          }
        } else if (payload.type === 'session' && payload.session) {
          applySession(payload.session)
        }
      },
      onStatus: (status) => {
        if (status !== 'open') return
        void refillMessages().catch(() => undefined)
        // 断连期间的会话状态翻转（如 agent 抛权限请求置
        // awaiting_permission）不补发 SSE；重连必须重拉会话快照，否则本地
        // status 滞留 running，approve/deny 永远 disabled。刷新失败不阻断
        // 消息补齐：sessions 列表兜底与后续 SSE 会再校准。
        void fetchStudioChatSession(workspaceId, activeSessionId).then(
          applySession,
          () => undefined
        )
      },
    })
    return () => channel.close()
  }, [workspaceId, activeSessionId, applySession, refillMessages])

  async function runAction(action: () => Promise<void>) {
    setActionError(null)
    try {
      await action()
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '操作失败')
    }
  }

  async function selectSession(sessionId: string) {
    setActiveSessionId(sessionId)
  }

  async function startSession(agentId: string) {
    if (!workspaceId || starting) return
    setStarting(true)
    await runAction(async () => {
      const created = await createStudioChatSession(workspaceId, agentId)
      await queryClient.invalidateQueries({
        queryKey: queryKeys.studioChatSessions(workspaceId),
      })
      applySession(created)
      setActiveSessionId(created.id)
    })
    setStarting(false)
  }

  async function send(text: string) {
    if (!workspaceId || !activeSessionId || !text.trim()) return
    setSending(true)
    await runAction(async () => {
      const message: StudioChatMessageRecord = await sendStudioChatMessage(
        workspaceId,
        activeSessionId,
        text.trim()
      )
      setMessages((current) => upsertMessage(current, message) ?? current)
    })
    setSending(false)
  }

  async function cancel() {
    if (!workspaceId || !activeSessionId) return
    await runAction(async () => {
      applySession(await cancelStudioChatTurn(workspaceId, activeSessionId))
    })
  }

  async function setAllowAll(enabled: boolean) {
    if (!workspaceId || !activeSessionId) return
    await runAction(async () => {
      applySession(
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

  const toolCalls = useMemo(() => groupToolCalls(messages), [messages])
  const workflowDraft = useMemo(
    () => extractWorkflowDraft(toolCalls),
    [toolCalls]
  )
  const agentDrafts = useMemo(
    () => extractAgentDefinitionDrafts(toolCalls),
    [toolCalls]
  )
  const nodeDrafts = useMemo(
    () => extractNodeCodeDrafts(toolCalls),
    [toolCalls]
  )
  const permissions = useMemo(() => buildPermissionViews(messages), [messages])

  const busy = session ? BUSY_STATUSES.has(session.status) : false
  const closed = session
    ? session.status === 'closed' || session.status === 'error'
    : false

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
    sending,
    starting,
    actionError,
    lastRunMs: runTiming.lastMs,
    selectSession,
    startSession,
    send,
    cancel,
    setAllowAll,
    answerPermission,
  }
}

export type StudioChat = ReturnType<typeof useStudioChat>
