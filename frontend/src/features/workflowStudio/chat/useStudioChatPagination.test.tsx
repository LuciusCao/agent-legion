import { createElement, type ReactNode } from 'react'
import {
  act,
  render,
  renderHook,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { useStudioChat, type StudioChat } from './useStudioChat'
import { useChatScroll } from './useChatScroll'
import * as chatApi from './studioChatApi'
import * as resumeApi from './studioChatResumeApi'
import type { StudioChatSessionRecord } from './studioChatApi'
import { EventSourceMock } from '../../../testing/eventSourceMock'
import { createTestQueryClient } from '../../../testing/testQueryClient'

vi.mock('./studioChatApi')
vi.mock('./studioChatResumeApi')

const mockApi = vi.mocked(chatApi)
void vi.mocked(resumeApi)

// 与 useStudioChat.test.tsx 同款的 jsdom localStorage 内存 stub。
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
    compacting: false,
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

type HistoryPage = Awaited<
  ReturnType<typeof chatApi.fetchStudioChatHistoryPage>
>

describe('useStudioChat 历史向上翻页（#1120 PR-4）', () => {
  const originalEventSource = globalThis.EventSource
  let testClient = createTestQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: testClient }, children)

  beforeEach(() => {
    testClient = createTestQueryClient()
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
    installLocalStorageStub()
    vi.clearAllMocks()
    mockApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi Code' },
    ])
    mockApi.fetchStudioChatSessions.mockResolvedValue([sessionRecord()])
    // 首屏固定返回最新窗口 seq 101..105。
    mockApi.fetchStudioChatMessages.mockResolvedValue([
      textMessage('m101', 101, 'a'),
      textMessage('m102', 102, 'b'),
      textMessage('m103', 103, 'c'),
      textMessage('m104', 104, 'd'),
      textMessage('m105', 105, 'e'),
    ])
    mockApi.fetchStudioChatHistoryPage.mockResolvedValue({
      messages: [textMessage('m96', 96, 'p'), textMessage('m100', 100, 'q')],
      hasMore: true,
    })
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
      expect(view.result.current.messages.map((m) => m.id)).toEqual([
        'm101',
        'm102',
        'm103',
        'm104',
        'm105',
      ])
    )
    return view
  }

  it('以当前最老 seq 为游标拉取更早一页并 prepend', async () => {
    const { result } = await renderChat()
    let started: boolean | undefined
    await act(async () => {
      started = await result.current.loadOlderHistory()
    })
    expect(started).toBe(true)
    expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledWith(
      'ws1',
      's1',
      101
    )
    expect(result.current.messages.map((m) => m.id)).toEqual([
      'm96',
      'm100',
      'm101',
      'm102',
      'm103',
      'm104',
      'm105',
    ])
  })

  it('hasMore=false 闩锁到顶：不再发起拉取', async () => {
    mockApi.fetchStudioChatHistoryPage.mockResolvedValue({
      messages: [textMessage('m96', 96, 'p')],
      hasMore: false,
    })
    const { result } = await renderChat()
    await act(async () => {
      await result.current.loadOlderHistory()
    })
    expect(result.current.hasMoreHistory).toBe(false)

    let retried: boolean | undefined
    await act(async () => {
      retried = await result.current.loadOlderHistory()
    })
    expect(retried).toBe(false)
    expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(1)
  })

  it('加载中锁：并发触发只拉一次，完成后可继续翻页', async () => {
    let resolvePage: (page: HistoryPage) => void = () => {}
    mockApi.fetchStudioChatHistoryPage.mockImplementation(
      () =>
        new Promise<HistoryPage>((resolve) => {
          resolvePage = resolve
        })
    )
    const { result } = await renderChat()

    let first: Promise<boolean> | undefined
    let second: Promise<boolean> | undefined
    act(() => {
      first = result.current.loadOlderHistory()
      second = result.current.loadOlderHistory()
    })
    expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(1)

    await act(async () => {
      resolvePage({
        messages: [textMessage('m96', 96, 'p')],
        hasMore: true,
      })
      expect(await second).toBe(false)
      expect(await first).toBe(true)
    })

    // 锁已释放：以新的最老 seq 继续向上翻。
    mockApi.fetchStudioChatHistoryPage.mockResolvedValue({
      messages: [textMessage('m90', 90, 'older page 2')],
      hasMore: false,
    })
    await act(async () => {
      await result.current.loadOlderHistory()
    })
    expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(2)
    expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenLastCalledWith(
      'ws1',
      's1',
      96
    )
  })

  it('拉取失败不闩锁：游标不推进，下次触发重试', async () => {
    mockApi.fetchStudioChatHistoryPage.mockRejectedValueOnce(
      new Error('network down')
    )
    const { result } = await renderChat()

    let failed: boolean | undefined
    await act(async () => {
      failed = await result.current.loadOlderHistory()
    })
    expect(failed).toBe(false)
    expect(result.current.hasMoreHistory).toBe(true)
    expect(result.current.messages[0]?.id).toBe('m101')

    await act(async () => {
      await result.current.loadOlderHistory()
    })
    expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(2)
    // 游标仍是最老的 101（失败未推进）。
    expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenLastCalledWith(
      'ws1',
      's1',
      101
    )
    expect(result.current.messages[0]?.id).toBe('m96')
  })

  it('空消息列表不发起拉取', async () => {
    mockApi.fetchStudioChatMessages.mockResolvedValue([])
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
    let started: boolean | undefined
    await act(async () => {
      started = await view.result.current.loadOlderHistory()
    })
    expect(started).toBe(false)
    expect(mockApi.fetchStudioChatHistoryPage).not.toHaveBeenCalled()
  })

  it('跨会话竞态：旧会话的迟到旧页不合入新会话，翻页态随会话复位', async () => {
    let resolvePage: (page: HistoryPage) => void = () => {}
    mockApi.fetchStudioChatHistoryPage.mockImplementation(
      () =>
        new Promise<HistoryPage>((resolve) => {
          resolvePage = resolve
        })
    )
    mockApi.fetchStudioChatMessages.mockImplementation(
      (_workspaceId, sessionId) =>
        Promise.resolve(
          sessionId === 's1'
            ? [textMessage('m101', 101, 'a')]
            : [textMessage('n500', 500, 's2 newest')]
        )
    )
    const view = renderHook(() => useStudioChat('ws1'), { wrapper })
    await waitFor(() =>
      expect(mockApi.fetchStudioChatSessions).toHaveBeenCalled()
    )
    await act(async () => {
      await view.result.current.selectSession('s1')
    })
    await waitFor(() =>
      expect(view.result.current.messages.map((m) => m.id)).toEqual(['m101'])
    )
    const { result } = view

    // s1 的旧页拉取挂起，期间切到 s2。
    let pending: Promise<boolean> | undefined
    act(() => {
      pending = result.current.loadOlderHistory()
    })
    await waitFor(() =>
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledWith(
        'ws1',
        's1',
        101
      )
    )
    await act(async () => {
      await result.current.selectSession('s2')
    })
    await waitFor(() =>
      expect(result.current.messages.map((m) => m.id)).toEqual(['n500'])
    )

    await act(async () => {
      resolvePage({
        messages: [textMessage('m90', 90, 'stale page')],
        hasMore: false,
      })
      expect(await pending).toBe(false)
    })
    // 旧页不得合入 s2；s2 的翻页闩锁也不被 s1 的响应污染。
    expect(result.current.messages.map((m) => m.id)).toEqual(['n500'])
    expect(result.current.hasMoreHistory).toBe(true)
  })

  it('与 refillMessages(0) 全量回取并存：重叠行 upsert 幂等、旧页保留', async () => {
    const { result } = await renderChat()
    await act(async () => {
      await result.current.loadOlderHistory()
    })
    expect(result.current.messages.map((m) => m.id)).toEqual([
      'm96',
      'm100',
      'm101',
      'm102',
      'm103',
      'm104',
      'm105',
    ])

    // turn_end 触发 after_seq=0 全量回取（#563 自愈路径）：窗口与已加载
    // 旧页重叠（m100 内容被服务端完整版覆盖），upsert 幂等不产生重复行。
    mockApi.fetchStudioChatMessages.mockResolvedValueOnce([
      textMessage('m100', 100, 'q full'),
      textMessage('m101', 101, 'a'),
      textMessage('m102', 102, 'b'),
      textMessage('m103', 103, 'c'),
      textMessage('m104', 104, 'd'),
      textMessage('m105', 105, 'e'),
    ])
    const source = EventSourceMock.instances[0]
    act(() =>
      source!.emitMessage({
        type: 'message',
        message: {
          id: 'st1',
          session_id: 's1',
          kind: 'status',
          role: 'system',
          content: { event: 'turn_end', stop_reason: 'end_turn' },
        },
      })
    )

    await waitFor(() =>
      expect(mockApi.fetchStudioChatMessages).toHaveBeenCalledWith(
        'ws1',
        's1',
        0
      )
    )
    await waitFor(() =>
      expect(result.current.messages.map((m) => m.id)).toEqual([
        'm96',
        'm100',
        'm101',
        'm102',
        'm103',
        'm104',
        'm105',
      ])
    )
    expect(
      result.current.messages.find((m) => m.id === 'm100')?.content.text
    ).toBe('q full')
  })
})

// #1237 codex P2 族：① 异步加载回调的陈旧响应拒绝必须到「会话选择代次」级——
// A→B→A 切换后 sessionId 守卫失效（重新选中的还是 A），早先代次挂起的响应
// 不得合入消息、不得闩锁翻页、不得覆盖会话快照；② 共享加载锁/瞬态标志的
// finally 释放必须绑定持有者（请求 token / 代次），旧请求的 finally 不得
// 清掉新请求的锁。
describe('会话选择代次与锁持有者守卫（#1237 codex P2 族）', () => {
  const originalEventSource = globalThis.EventSource
  let testClient = createTestQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: testClient }, children)

  beforeEach(() => {
    testClient = createTestQueryClient()
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
    installLocalStorageStub()
    vi.clearAllMocks()
    mockApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi Code' },
    ])
    mockApi.fetchStudioChatSessions.mockResolvedValue([sessionRecord()])
    mockApi.fetchStudioChatMessages.mockImplementation(
      (_workspaceId, sessionId) =>
        Promise.resolve(
          sessionId === 's1'
            ? [
                textMessage('m101', 101, 'a'),
                textMessage('m102', 102, 'b'),
                textMessage('m103', 103, 'c'),
                textMessage('m104', 104, 'd'),
                textMessage('m105', 105, 'e'),
              ]
            : [textMessage('n500', 500, 's2 newest')]
        )
    )
    mockApi.updateStudioChatContext.mockResolvedValue(sessionRecord())
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
  })

  async function renderS1() {
    const view = renderHook(() => useStudioChat('ws1'), { wrapper })
    await waitFor(() =>
      expect(mockApi.fetchStudioChatSessions).toHaveBeenCalled()
    )
    await act(async () => {
      await view.result.current.selectSession('s1')
    })
    await waitFor(() =>
      expect(view.result.current.messages.map((m) => m.id)).toEqual([
        'm101',
        'm102',
        'm103',
        'm104',
        'm105',
      ])
    )
    return view
  }

  async function roundTripViaS2(
    result: { current: ReturnType<typeof useStudioChat> },
    s1Messages: ReturnType<typeof textMessage>[]
  ) {
    await act(async () => {
      await result.current.selectSession('s2')
    })
    await waitFor(() =>
      expect(result.current.messages.map((m) => m.id)).toEqual(['n500'])
    )
    mockApi.fetchStudioChatMessages.mockImplementation(
      (_workspaceId, sessionId) =>
        Promise.resolve(
          sessionId === 's1' ? s1Messages : [textMessage('n500', 500, 's2')]
        )
    )
    await act(async () => {
      await result.current.selectSession('s1')
    })
    await waitFor(() =>
      expect(result.current.messages.map((m) => m.id)).toEqual(
        s1Messages.map((m) => m.id)
      )
    )
  }

  it('分页：A→B→A 后拒绝早先代次的旧页（不合入、不闩锁 hasMore）', async () => {
    let resolvePage: (page: HistoryPage) => void = () => {}
    mockApi.fetchStudioChatHistoryPage.mockImplementation(
      () =>
        new Promise<HistoryPage>((resolve) => {
          resolvePage = resolve
        })
    )
    const { result } = await renderS1()

    // gen1 的分页请求挂起，期间 A→B→A。
    act(() => {
      void result.current.loadOlderHistory()
    })
    await waitFor(() =>
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledWith(
        'ws1',
        's1',
        101
      )
    )
    await roundTripViaS2(result, [
      textMessage('m101', 101, 'a'),
      textMessage('m102', 102, 'b'),
      textMessage('m103', 103, 'c'),
      textMessage('m104', 104, 'd'),
      textMessage('m105', 105, 'e'),
    ])

    // gen1 的迟到响应落地：不得合入旧页、不得闩锁 hasMore（否则重新进入
    // A 的首屏窗口覆盖旧页后永远无法继续分页）。
    await act(async () => {
      resolvePage({
        messages: [textMessage('m96', 96, 'stale page')],
        hasMore: false,
      })
    })
    expect(result.current.messages.map((m) => m.id)).toEqual([
      'm101',
      'm102',
      'm103',
      'm104',
      'm105',
    ])
    expect(result.current.hasMoreHistory).toBe(true)

    // 当前代次分页不受影响。
    mockApi.fetchStudioChatHistoryPage.mockResolvedValue({
      messages: [textMessage('m90', 90, 'gen3 page')],
      hasMore: false,
    })
    await act(async () => {
      await result.current.loadOlderHistory()
    })
    expect(result.current.messages[0]?.id).toBe('m90')
    expect(result.current.hasMoreHistory).toBe(false)
  })

  it('refill：A→B→A 后拒绝早先代次的补齐（不覆盖更新内容）', async () => {
    const { result } = await renderS1()
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))

    // gen1：SSE 残片指向未知消息 → 增量补齐挂起。
    let resolveRefill: (
      messages: ReturnType<typeof textMessage>[]
    ) => void = () => {}
    mockApi.fetchStudioChatMessages.mockImplementation(
      (_workspaceId, sessionId, afterSeq) => {
        if (sessionId === 's1' && afterSeq !== undefined) {
          return new Promise((resolve) => {
            resolveRefill = resolve
          })
        }
        return Promise.resolve(
          sessionId === 's1' ? [] : [textMessage('n500', 500, 's2 newest')]
        )
      }
    )
    const source = EventSourceMock.instances[0]
    act(() =>
      source!.emitMessage({
        type: 'message',
        message: {
          id: 'm106',
          session_id: 's1',
          kind: 'text',
          role: 'agent',
          content: { text: 'partial' },
        },
      })
    )
    await waitFor(() =>
      expect(mockApi.fetchStudioChatMessages).toHaveBeenCalledWith(
        'ws1',
        's1',
        105
      )
    )

    // A→B→A：gen3 首屏窗口里 m105 已是服务端完整版。
    await roundTripViaS2(result, [
      textMessage('m101', 101, 'a'),
      textMessage('m102', 102, 'b'),
      textMessage('m103', 103, 'c'),
      textMessage('m104', 104, 'd'),
      textMessage('m105', 105, 'fresh e'),
    ])

    // gen1 的补齐携带 m105 的断连期旧快照：不得覆盖 gen3 的完整版。
    await act(async () => {
      resolveRefill([textMessage('m105', 105, 'stale e')])
    })
    expect(
      result.current.messages.find((m) => m.id === 'm105')?.content.text
    ).toBe('fresh e')
  })

  it('SSE 重连的会话快照：切换会话后丢弃迟到快照', async () => {
    const { result } = await renderS1()
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))

    let resolveFetch: (record: StudioChatSessionRecord) => void = () => {}
    mockApi.fetchStudioChatSession.mockImplementation(
      () =>
        new Promise<StudioChatSessionRecord>((resolve) => {
          resolveFetch = resolve
        })
    )
    const source = EventSourceMock.instances[0]
    act(() => source!.onopen?.())
    await waitFor(() =>
      expect(mockApi.fetchStudioChatSession).toHaveBeenCalledWith('ws1', 's1')
    )

    await act(async () => {
      await result.current.selectSession('s2')
    })
    await waitFor(() =>
      expect(result.current.messages.map((m) => m.id)).toEqual(['n500'])
    )

    // 旧会话的迟到快照不得写进当前选中（s2 不在会话列表里，快照应为空）。
    await act(async () => {
      resolveFetch(sessionRecord({ id: 's1', status: 'running' }))
    })
    expect(result.current.session).toBeNull()
  })

  it('加载中锁绑定请求 token：A 的迟到 finally 不释放 B 的在途锁（#1237 codex P2-2）', async () => {
    const resolvers: ((page: HistoryPage) => void)[] = []
    mockApi.fetchStudioChatHistoryPage.mockImplementation(
      () =>
        new Promise<HistoryPage>((resolve) => {
          resolvers.push(resolve)
        })
    )
    const { result } = await renderS1()

    // s1 的分页请求挂起（token1）。
    act(() => {
      void result.current.loadOlderHistory()
    })
    await waitFor(() =>
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(1)
    )

    // 切到 s2（切换 effect 复位锁）→ s2 的分页请求挂起（token2）。
    await act(async () => {
      await result.current.selectSession('s2')
    })
    await waitFor(() =>
      expect(result.current.messages.map((m) => m.id)).toEqual(['n500'])
    )
    act(() => {
      void result.current.loadOlderHistory()
    })
    await waitFor(() =>
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(2)
    )
    expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenLastCalledWith(
      'ws1',
      's2',
      500
    )

    // s1 的迟到响应落地：代次守卫拒绝合入；其 finally 不得释放 s2 的
    // 在途锁——否则下一次到顶会重复请求同一游标页。
    await act(async () => {
      resolvers[0]({ messages: [], hasMore: true })
    })
    let second: Promise<boolean> | undefined
    act(() => {
      second = result.current.loadOlderHistory()
    })
    // 先断言不重复发请求（旧实现这里会发起第三次的挂起请求），再收结果。
    expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(2)
    let secondResult: boolean | undefined
    await act(async () => {
      secondResult = await second
    })
    expect(secondResult).toBe(false)

    // s2 的响应落地释放自己的锁：hasMore=false 闩锁到顶。
    await act(async () => {
      resolvers[1]({
        messages: [textMessage('n400', 400, 's2 older')],
        hasMore: false,
      })
    })
    expect(result.current.messages[0]?.id).toBe('n400')
    expect(result.current.hasMoreHistory).toBe(false)
  })

  it('startSession：workspace 往返后早先创建的 finally 不释放新一轮在途创建的 starting（#1237 codex P2-2 族）', async () => {
    mockApi.fetchStudioChatSessions.mockImplementation((ws: string) =>
      Promise.resolve(ws === 'ws1' ? [sessionRecord()] : [])
    )
    const resolvers: ((record: StudioChatSessionRecord) => void)[] = []
    mockApi.createStudioChatSession.mockImplementation(
      () =>
        new Promise<StudioChatSessionRecord>((resolve) => {
          resolvers.push(resolve)
        })
    )
    const view = renderHook(({ ws }: { ws: string }) => useStudioChat(ws), {
      wrapper,
      initialProps: { ws: 'ws1' },
    })
    await waitFor(() =>
      expect(mockApi.fetchStudioChatSessions).toHaveBeenCalled()
    )

    // ws1 的创建 #1 挂起。
    act(() => {
      void view.result.current.startSession('kimi')
    })
    await waitFor(() => expect(view.result.current.starting).toBe(true))

    // ws1→ws2→ws1（复位瞬态）后发起创建 #2（仍在途）。
    view.rerender({ ws: 'ws2' })
    await waitFor(() => expect(view.result.current.starting).toBe(false))
    view.rerender({ ws: 'ws1' })
    act(() => {
      void view.result.current.startSession('kimi')
    })
    await waitFor(() => expect(view.result.current.starting).toBe(true))
    expect(mockApi.createStudioChatSession).toHaveBeenCalledTimes(2)

    // #1 的迟到响应：内容守卫丢弃，其 finally 不得清掉 #2 的 starting。
    await act(async () => {
      resolvers[0](sessionRecord({ id: 's-new-1' }))
    })
    expect(view.result.current.starting).toBe(true)

    // #2 落地：自己的 finally 正常释放。
    await act(async () => {
      resolvers[1](sessionRecord({ id: 's-new-2' }))
    })
    expect(view.result.current.starting).toBe(false)
    expect(view.result.current.activeSessionId).toBe('s-new-2')
  })
})

// #1237 codex 八轮 P2：自动补拉由子组件（useChatScroll，StudioChatMessageList）
// 的 passive effect 触发，而 messagesRef 由父组件（useStudioChat 所在组件）
// 的 passive effect 同步——React 子 effect 先跑，补拉若从 messagesRef 取游标
// 会读到上一轮的空数组直接返回，父 effect 更新 ref 又不再引发重渲染，
// 短会话死锁。本 describe 用父子组件集成还愿该时序（其余用例是 renderHook
// 单组件，结构上无法暴露）。
describe('补拉链游标取位（#1237 codex 八轮 P2）', () => {
  const originalEventSource = globalThis.EventSource
  let testClient = createTestQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: testClient }, children)

  function ChatList({ chat }: { chat: StudioChat }) {
    const { bottomRef, listRef, handleScroll } = useChatScroll(
      chat.messages,
      chat.loadOlderHistory
    )
    return (
      <div ref={listRef} data-testid="list" onScroll={handleScroll}>
        {chat.messages.map((m) => (
          <div key={m.id}>{m.id}</div>
        ))}
        <div ref={bottomRef} />
      </div>
    )
  }
  function Panel() {
    return <ChatList chat={useStudioChat('ws1')} />
  }

  beforeEach(() => {
    testClient = createTestQueryClient()
    EventSourceMock.reset()
    globalThis.EventSource = EventSourceMock as unknown as typeof EventSource
    installLocalStorageStub()
    vi.clearAllMocks()
    mockApi.fetchStudioChatAgents.mockResolvedValue([
      { id: 'kimi', label: 'Kimi Code' },
    ])
    mockApi.fetchStudioChatSessions.mockResolvedValue([sessionRecord()])
    mockApi.updateStudioChatContext.mockResolvedValue(sessionRecord())
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
  })

  it('首屏未溢出触发补拉：游标取当前渲染快照（子 effect 先于父 ref 同步）', async () => {
    // 首屏只有 3 条（seq 101..103），不足以撑满容器。
    mockApi.fetchStudioChatMessages.mockResolvedValue([
      textMessage('m101', 101, 'a'),
      textMessage('m102', 102, 'b'),
      textMessage('m103', 103, 'c'),
    ])
    mockApi.fetchStudioChatHistoryPage
      .mockResolvedValueOnce({
        messages: [textMessage('m96', 96, 'p1')],
        hasMore: true,
      })
      .mockResolvedValueOnce({
        messages: [textMessage('m90', 90, 'p2')],
        hasMore: false,
      })
    render(<Panel />, { wrapper })
    await waitFor(() =>
      expect(mockApi.fetchStudioChatSessions).toHaveBeenCalled()
    )
    // 列表容器 stub 为「未溢出」（300 < 400）；首屏消息落地后子组件的
    // 自动补拉 effect 即触发——此刻父组件同步 messagesRef 的 passive
    // 效应尚未运行。
    const list = screen.getByTestId('list')
    Object.defineProperty(list, 'scrollHeight', {
      configurable: true,
      get: () => 300,
    })
    Object.defineProperty(list, 'clientHeight', {
      configurable: true,
      get: () => 400,
    })

    // 记忆效应自动选中 s1 → 首屏消息落地 → 补拉必须以当前渲染的最老
    // seq=101 发起（滞后 messagesRef 此时仍为空数组，旧实现直接返回）。
    await waitFor(() =>
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledWith(
        'ws1',
        's1',
        101
      )
    )
    // 第一页落地后仍未溢出：以新最老 seq=96 续拉；hasMore=false 闩锁终止。
    await waitFor(() =>
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledWith(
        'ws1',
        's1',
        96
      )
    )
    await waitFor(() => expect(screen.getByText('m90')).toBeInTheDocument())
    expect(mockApi.fetchStudioChatHistoryPage.mock.calls.length).toBe(2)
  })
})
