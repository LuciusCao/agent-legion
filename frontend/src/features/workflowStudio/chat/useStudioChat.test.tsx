import { createElement, type ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { useStudioChat } from './useStudioChat'
import * as chatApi from './studioChatApi'
import * as resumeApi from './studioChatResumeApi'
import type { StudioChatSessionRecord } from './studioChatApi'
import { EventSourceMock } from '../../../testing/eventSourceMock'
import { createTestQueryClient } from '../../../testing/testQueryClient'

vi.mock('./studioChatApi')
vi.mock('./studioChatResumeApi')

const mockApi = vi.mocked(chatApi)
const mockResume = vi.mocked(resumeApi)

// 该 jsdom 环境不提供 localStorage：用内存 stub 验证持久化读写（同
// StudioChatResume.test.tsx 的模式）。
function installLocalStorageStub() {
  const store = new Map<string, string>()
  const stub: Storage = {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: (key) => store.get(key) ?? null,
    key: (index) => [...store.keys()][index] ?? null,
    removeItem: (key) => void store.delete(key),
    setItem: (key, value) => void store.set(key, String(value)),
  }
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: stub,
  })
  return stub
}

function sessionRecord(
  overrides?: Partial<StudioChatSessionRecord>
): StudioChatSessionRecord {
  return {
    id: 's1',
    workspace_id: 'ws1',
    user_id: 'u1',
    agent_id: 'kimi',
    title: '',
    status: 'idle',
    acp_session_id: null,
    capability_snapshot: {},
    allow_all_permissions: false,
    mcp_status: 'unknown',
    selected_node_key: null,
    error_detail: '',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    closed_at: null,
    ...overrides,
  }
}

function textMessage(id: string, seq: number, text: string) {
  return {
    id,
    session_id: 's1',
    kind: 'text' as const,
    role: 'agent' as const,
    content: { text },
    seq,
    created_at: '2026-01-01T00:00:00Z',
  }
}

describe('useStudioChat', () => {
  const originalEventSource = globalThis.EventSource
  let testClient = createTestQueryClient()
  let storage: Storage
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: testClient }, children)

  beforeEach(() => {
    testClient = createTestQueryClient()
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
    storage = installLocalStorageStub()
    vi.clearAllMocks()
    mockApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi Code' },
    ])
    mockApi.fetchStudioChatSessions.mockResolvedValue([sessionRecord()])
    mockApi.fetchStudioChatMessages.mockResolvedValue([])
    mockApi.updateStudioChatContext.mockResolvedValue(sessionRecord())
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
  })

  async function renderChat() {
    const view = renderHook(() => useStudioChat('ws1'), { wrapper })
    await waitFor(() =>
      expect(mockApi.fetchStudioChatSessions).toHaveBeenCalled()
    )
    await act(async () => {
      await view.result.current.selectSession('s1')
    })
    await waitFor(() =>
      expect(mockApi.fetchStudioChatMessages).toHaveBeenCalledWith('ws1', 's1')
    )
    return view
  }

  function emit(payload: object) {
    const source =
      EventSourceMock.instances[EventSourceMock.instances.length - 1]
    expect(source).toBeDefined()
    act(() => source!.emitMessage(payload))
  }

  it('loads agents and sessions for the pickers', async () => {
    const { result } = await renderChat()
    await waitFor(() => expect(result.current.agents).toHaveLength(1))
    expect(result.current.sessions.map((row) => row.id)).toEqual(['s1'])
  })

  it('upserts SSE message events and merges streaming partials', async () => {
    const { result } = await renderChat()
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))
    emit({ type: 'message', message: textMessage('m1', 1, 'hel') })
    expect(result.current.messages[0]?.content.text).toBe('hel')

    // 流式残片：无 seq/created_at，只按 id 合并 content。
    emit({
      type: 'message',
      message: {
        id: 'm1',
        session_id: 's1',
        kind: 'text',
        role: 'agent',
        content: { text: 'hello world' },
      },
    })
    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0]?.content.text).toBe('hello world')
    expect(result.current.messages[0]?.seq).toBe(1)
  })

  it('refetches incrementally when a partial targets an unknown message', async () => {
    const { result } = await renderChat()
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))
    emit({ type: 'message', message: textMessage('m1', 7, 'done') })
    mockApi.fetchStudioChatMessages.mockResolvedValueOnce([
      textMessage('m2', 8, 'full text'),
    ])
    emit({
      type: 'message',
      message: {
        id: 'm2',
        session_id: 's1',
        kind: 'text',
        role: 'agent',
        content: { text: 'f' },
      },
    })
    await waitFor(() =>
      expect(mockApi.fetchStudioChatMessages).toHaveBeenCalledWith(
        'ws1',
        's1',
        7
      )
    )
    await waitFor(() =>
      expect(result.current.messages.map((m) => m.id)).toEqual(['m1', 'm2'])
    )
    expect(result.current.messages[1]?.content.text).toBe('full text')
  })

  it('refreshes the session snapshot when the SSE stream (re)opens', async () => {
    mockApi.fetchStudioChatSession.mockResolvedValue(
      sessionRecord({ status: 'awaiting_permission' })
    )
    const { result } = await renderChat()
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))

    // 断连前最后一次 SSE 推送让本地快照滞留在 running。
    emit({ type: 'session', session: sessionRecord({ status: 'running' }) })
    expect(result.current.session?.status).toBe('running')

    // 断连期间 agent 抛了权限请求（服务端置 awaiting_permission）；
    // 重连 open 必须重拉会话快照，否则 approve/deny 永远 disabled。
    const source = EventSourceMock.instances[0]
    act(() => source.onopen?.())

    await waitFor(() =>
      expect(mockApi.fetchStudioChatSession).toHaveBeenCalledWith('ws1', 's1')
    )
    await waitFor(() =>
      expect(result.current.session?.status).toBe('awaiting_permission')
    )
  })

  it('tracks run state from session events and supports cancel', async () => {
    mockApi.cancelStudioChatTurn.mockResolvedValue(sessionRecord())
    const { result } = await renderChat()
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))

    emit({ type: 'session', session: sessionRecord({ status: 'running' }) })
    expect(result.current.busy).toBe(true)
    expect(result.current.session?.status).toBe('running')

    await act(async () => {
      await result.current.cancel()
    })
    expect(mockApi.cancelStudioChatTurn).toHaveBeenCalledWith('ws1', 's1')

    emit({ type: 'session', session: sessionRecord({ status: 'idle' }) })
    expect(result.current.busy).toBe(false)
    expect(result.current.lastRunMs).not.toBeNull()
  })

  it('sends a message and upserts the returned record', async () => {
    mockApi.sendStudioChatMessage.mockResolvedValue(
      textMessage('u1', 3, '帮我加个节点')
    )
    const { result } = await renderChat()
    emit({ type: 'session', session: sessionRecord() })
    await act(async () => {
      await result.current.send(' 帮我加个节点 ')
    })
    expect(mockApi.sendStudioChatMessage).toHaveBeenCalledWith(
      'ws1',
      's1',
      '帮我加个节点'
    )
    expect(result.current.messages[0]?.id).toBe('u1')
  })

  it('returns false and surfaces actionError when send fails', async () => {
    mockApi.sendStudioChatMessage.mockRejectedValue(new Error('会话忙'))
    const { result } = await renderChat()
    emit({ type: 'session', session: sessionRecord() })
    let sent: boolean | undefined
    await act(async () => {
      sent = await result.current.send('你好')
    })
    // 排队 flush 依赖这个布尔决定队首去留。
    expect(sent).toBe(false)
    expect(result.current.actionError).toBe('会话忙')
  })

  it('answers permission requests and toggles allow-all', async () => {
    mockApi.answerStudioChatPermission.mockResolvedValue(undefined)
    mockApi.setStudioChatAllowAll.mockResolvedValue(
      sessionRecord({ allow_all_permissions: true })
    )
    const { result } = await renderChat()

    await act(async () => {
      await result.current.answerPermission('r1', { option_id: 'o1' })
    })
    expect(mockApi.answerStudioChatPermission).toHaveBeenCalledWith(
      'ws1',
      's1',
      'r1',
      { deny: false, option_id: 'o1' }
    )

    await act(async () => {
      await result.current.setAllowAll(true)
    })
    expect(mockApi.setStudioChatAllowAll).toHaveBeenCalledWith(
      'ws1',
      's1',
      true
    )
    expect(result.current.session?.allow_all_permissions).toBe(true)
  })

  it('creates a new session and activates it', async () => {
    const created = sessionRecord({ id: 's2' })
    mockApi.createStudioChatSession.mockResolvedValue(created)
    const { result } = await renderChat()
    await act(async () => {
      await result.current.startSession('kimi')
    })
    expect(mockApi.createStudioChatSession).toHaveBeenCalledWith('ws1', 'kimi')
    expect(result.current.activeSessionId).toBe('s2')
    expect(result.current.session?.id).toBe('s2')
  })

  it('derives workflow drafts from tool call messages', async () => {
    const { result } = await renderChat()
    emit({
      type: 'message',
      message: {
        ...textMessage('t1', 5, ''),
        kind: 'tool_call',
        content: {
          sessionUpdate: 'tool_call',
          toolCallId: 'call-1',
          title: 'validate_workflow',
          status: 'completed',
          rawInput: { workspace_id: 'ws1', definition_yaml: 'key: w\n' },
          rawOutput: {
            content: [{ type: 'text', text: '{"valid": true, "errors": []}' }],
          },
        },
      },
    })
    expect(result.current.workflowDraft?.yaml).toBe('key: w\n')
    expect(result.current.workflowDraft?.validated).toBe(true)
  })

  it('refetches the full timeline on turn_end to heal truncated streaming text', async () => {
    const { result } = await renderChat()
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))
    emit({ type: 'message', message: textMessage('m1', 1, 'trunc') })
    expect(result.current.messages[0]?.content.text).toBe('trunc')

    // 断连期间流式 text 的尾部只在服务端落库（原地更新 seq 不变）；turn_end
    // 后按 after_seq=0 全量回取，本地截断消息被完整版本覆盖。
    mockApi.fetchStudioChatMessages.mockResolvedValueOnce([
      textMessage('m1', 1, 'full ending'),
    ])
    emit({
      type: 'message',
      message: {
        id: 'st1',
        session_id: 's1',
        kind: 'status',
        role: 'system',
        content: { event: 'turn_end', stop_reason: 'end_turn' },
      },
    })

    await waitFor(() =>
      expect(mockApi.fetchStudioChatMessages).toHaveBeenCalledWith(
        'ws1',
        's1',
        0
      )
    )
    await waitFor(() =>
      expect(result.current.messages[0]?.content.text).toBe('full ending')
    )
  })

  it('invalidates studio canvas queries on turn_end so agent edits show up', async () => {
    await renderChat()
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))
    const invalidateSpy = vi.spyOn(testClient, 'invalidateQueries')

    // agent 一轮结束可能已保存 workflow/Agent 草稿：失效画布基线与 Agent
    // 目录查询，让 MCP 修改无需手动刷新即反映到 DAG。
    emit({
      type: 'message',
      message: {
        id: 'st1',
        session_id: 's1',
        kind: 'status',
        role: 'system',
        content: { event: 'turn_end', stop_reason: 'end_turn' },
      },
    })

    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ['workflowStudioData', 'ws1'],
      })
    )
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['studioAgentCatalog', 'ws1'],
    })
    // agent 可能已提交新的 skill 版本：技能预览查询按前缀整体失效。
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['studioSkillDetail'],
    })
  })

  it('does not merge an in-flight refill from a previous session', async () => {
    type MessagesResult = Awaited<
      ReturnType<typeof chatApi.fetchStudioChatMessages>
    >
    let resolveRefill: (messages: MessagesResult) => void = () => {}
    mockApi.fetchStudioChatMessages.mockImplementation(
      (_workspaceId, sessionId, afterSeq) => {
        if (sessionId === 's1' && afterSeq !== undefined) {
          return new Promise<MessagesResult>((resolve) => {
            resolveRefill = resolve
          })
        }
        return Promise.resolve([])
      }
    )
    mockApi.fetchStudioChatSession.mockResolvedValue(sessionRecord())
    const { result } = await renderChat()
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))

    // 重连触发对 s1 的增量补齐（在途），期间用户切到 s2。
    const source = EventSourceMock.instances[0]
    act(() => source.onopen?.())
    await waitFor(() =>
      expect(mockApi.fetchStudioChatMessages).toHaveBeenCalledWith(
        'ws1',
        's1',
        0
      )
    )
    await act(async () => {
      await result.current.selectSession('s2')
    })

    await act(async () => {
      resolveRefill([textMessage('stale', 1, 'from old session')])
    })
    expect(result.current.messages).toEqual([])
  })

  it('disables concurrent new-chat creation while one is in flight', async () => {
    let resolveCreate: (session: StudioChatSessionRecord) => void = () => {}
    mockApi.createStudioChatSession.mockImplementation(
      () =>
        new Promise<StudioChatSessionRecord>((resolve) => {
          resolveCreate = resolve
        })
    )
    const { result } = await renderChat()

    let first: Promise<void> | undefined
    act(() => {
      first = result.current.startSession('kimi')
    })
    await waitFor(() => expect(result.current.starting).toBe(true))

    await act(async () => {
      await result.current.startSession('kimi')
    })
    expect(mockApi.createStudioChatSession).toHaveBeenCalledTimes(1)

    await act(async () => {
      resolveCreate(sessionRecord({ id: 's2' }))
      await first
    })
    expect(result.current.starting).toBe(false)
    expect(result.current.activeSessionId).toBe('s2')
  })

  it('applies the resumed snapshot and invalidates the sessions list cache', async () => {
    mockResume.resumeStudioChatSession.mockResolvedValue(
      sessionRecord({ status: 'idle' })
    )
    const { result } = await renderChat()
    const invalidateSpy = vi.spyOn(testClient, 'invalidateQueries')

    await act(async () => {
      await result.current.resume()
    })

    expect(mockResume.resumeStudioChatSession).toHaveBeenCalledWith('ws1', 's1')
    // sessions 列表缓存失效：下拉里不再长期滞留「（已关闭）」。
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['studio-chat-sessions', 'ws1'],
    })
  })

  it('ignores a resume response that lands after switching sessions', async () => {
    type ResumeResult = Awaited<
      ReturnType<typeof resumeApi.resumeStudioChatSession>
    >
    let resolveResume: (session: ResumeResult) => void = () => {}
    mockResume.resumeStudioChatSession.mockImplementation(
      () =>
        new Promise<ResumeResult>((resolve) => {
          resolveResume = resolve
        })
    )
    const { result } = await renderChat()

    let resumeDone: Promise<void> | undefined
    act(() => {
      resumeDone = result.current.resume()
    })
    await act(async () => {
      await result.current.selectSession('s2')
    })
    await act(async () => {
      resolveResume(sessionRecord({ id: 's1', status: 'idle' }))
      await resumeDone
    })

    // 旧会话的 resume 响应不得覆盖当前选中会话的快照。
    expect(result.current.activeSessionId).toBe('s2')
    expect(result.current.session?.id).not.toBe('s1')
  })

  it('clears the active session when the workspace changes', async () => {
    mockApi.fetchStudioChatSessions.mockImplementation((workspaceId: string) =>
      Promise.resolve(workspaceId === 'ws1' ? [sessionRecord()] : [])
    )
    const view = renderHook(({ ws }: { ws: string }) => useStudioChat(ws), {
      wrapper,
      initialProps: { ws: 'ws1' },
    })
    await waitFor(() =>
      expect(mockApi.fetchStudioChatSessions).toHaveBeenCalled()
    )
    await act(async () => {
      await view.result.current.selectSession('s1')
    })
    expect(storage.getItem('studio-chat.active-session.ws1')).toBe('s1')

    view.rerender({ ws: 'ws2' })

    await waitFor(() => expect(view.result.current.activeSessionId).toBeNull())
    // 旧选中不得写进新 workspace 的记忆；旧 workspace 的记忆本身保留。
    expect(storage.getItem('studio-chat.active-session.ws2')).toBeNull()
    expect(storage.getItem('studio-chat.active-session.ws1')).toBe('s1')
  })
})
