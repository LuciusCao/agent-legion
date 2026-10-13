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
import { api } from '../../../api/core'
import type { StudioChatSessionRecord } from './studioChatApi'
import { EventSourceMock } from '../../../testing/eventSourceMock'
import { createTestQueryClient } from '../../../testing/testQueryClient'

vi.mock('./studioChatApi')
vi.mock('./studioChatResumeApi')
// 按 id 点读（studioChatCalibration.fetchStudioChatMessage）直连 api/core。
vi.mock('../../../api/core', () => ({ api: vi.fn() }))

const mockApi = vi.mocked(chatApi)
const mockCoreApi = vi.mocked(api)
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
    // 默认拒绝：missed 帧点读等直连 api/core 的路径走既有失败回退（#1289），
    // 不在本文件主题的调用不得静默成功。
    mockCoreApi.mockRejectedValue(new Error('not mocked'))
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

  it('A 的分页在途时切到 B：A 迟到的失败不挤掉 B 已挂好的待定重试（#1304）', async () => {
    const rejecters: ((reason: unknown) => void)[] = []
    mockApi.fetchStudioChatHistoryPage.mockImplementation(
      () =>
        new Promise<HistoryPage>((_resolve, reject) => {
          rejecters.push(reject)
        })
    )
    const { result } = await renderS1()

    // s1 的分页请求挂起（在途），期间切到 s2：切换复位锁，允许 s2 发起请求。
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
    await act(async () => {
      await result.current.selectSession('s2')
    })
    await waitFor(() =>
      expect(result.current.messages.map((m) => m.id)).toEqual(['n500'])
    )

    // fake timers 必须先于失败的 load 开启（同 #1291 用例的复验 finding 2）。
    vi.useFakeTimers()
    try {
      // s2 的补拉先失败：挂好 2s 重试（闭包游标 500）。
      act(() => {
        void result.current.loadOlderHistory()
      })
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(2)
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenLastCalledWith(
        'ws1',
        's2',
        500
      )
      await act(async () => {
        rejecters[1](new Error('s2 down'))
      })

      // s1 的旧请求随后才失败：无修复时其无条件 clearTimeout 取消 s2 的
      // 计时器，重挂的定时器捕获 s1 游标 101——点火守卫被 s2 当前游标 500
      // 挡掉，s2 在未溢出视口永久停在部分历史。
      await act(async () => {
        rejecters[0](new Error('s1 late down'))
      })
      expect(result.current.messages.map((m) => m.id)).toEqual(['n500'])

      // 到点：s2 的待定重试必须照常点火（信号：第三次调用以游标 500 发起；
      // 无修复时计时器已被取消/替换，本断言即红——mutation 自检过）。
      mockApi.fetchStudioChatHistoryPage.mockResolvedValue({
        messages: [textMessage('n400', 400, 's2 older')],
        hasMore: false,
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_000)
      })
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(3)
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenLastCalledWith(
        'ws1',
        's2',
        500
      )
      expect(result.current.messages[0]?.id).toBe('n400')
    } finally {
      vi.useRealTimers()
    }
  })

  it('收口点读不猜渲染时序（#1305 复验 codex r2 P1）：SSE 中间帧与较新 GET 响应在同一 act 内落地（其间无渲染冲刷），收口点读仍点火并落地最新库快照', async () => {
    // 首屏只有一条在途流式行（无 terminal，streamingTextId 命中）。
    mockApi.fetchStudioChatMessages.mockResolvedValue([
      textMessage('m101', 101, 'a'),
    ])
    mockApi.fetchStudioChatSession.mockResolvedValue(sessionRecord())
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
    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))
    const source = EventSourceMock.instances[0]!

    // SSE 重连自愈校准：m101 在途 → refill(0)（原样返回）后按 id 点读，挂起。
    let resolvePointRead: (value: unknown) => void = () => {}
    mockCoreApi.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolvePointRead = resolve
        }) as ReturnType<typeof api>
    )
    act(() => source.onopen?.())
    await waitFor(() => expect(mockCoreApi).toHaveBeenCalledTimes(1))
    expect(mockCoreApi).toHaveBeenLastCalledWith(
      '/api/workspaces/ws1/studio-chat/sessions/s1/messages/m101'
    )

    // 关键时序：同一 act 内先 emit 中间 SSE 帧（ab）、再落地较新的 GET 响应
    //（abc——服务端在 GET 在途期间持久化、对应 SSE 丢失），其间没有任何
    // 渲染冲刷。收口决定读权威 messagesRef（emit 的 handler 已同步写入 ab），
    // 与 updater 的丢弃决定同一事实 → 立即补一次收口点读；若 ref 回到被动
    // 效应同步（mutation），此刻 ref 仍是 a，与点火快照相等 → 漏挂收口
    // 点读，界面永停 ab——本用例即红。
    mockCoreApi.mockResolvedValueOnce({
      message: { ...textMessage('m101', 101, 'abc') },
    })
    await act(async () => {
      source.emitMessage({
        type: 'message',
        message: {
          id: 'm101',
          session_id: 's1',
          kind: 'text',
          role: 'agent',
          content: { text: 'ab' },
        },
      })
      resolvePointRead({ message: { ...textMessage('m101', 101, 'abc') } })
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })
    await waitFor(() => expect(mockCoreApi).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(view.result.current.messages[0]?.content.text).toBe('abc')
    )
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
