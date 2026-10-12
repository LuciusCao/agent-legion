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
    // 内容断言等合入完成的信号（非中间态）：m96/m100 由翻页先行到位，
    // id 列表相等不代表 once-mock 的 q full 已合入。
    await waitFor(() =>
      expect(
        result.current.messages.find((m) => m.id === 'm100')?.content.text
      ).toBe('q full')
    )
  })

  it('补拉失败短延迟自动重试一次（#1270）；重试自身失败不续期', async () => {
    mockApi.fetchStudioChatHistoryPage.mockRejectedValue(
      new Error('network down')
    )
    const { result } = await renderChat()
    vi.useFakeTimers()
    try {
      let failed: boolean | undefined
      await act(async () => {
        failed = await result.current.loadOlderHistory()
      })
      expect(failed).toBe(false)
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(1)

      // 未溢出态没有滚动事件可再触发——2s 后自动重试一次（信号驱动，非等待
      // 真实时长）。
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_000)
      })
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(2)

      // 重试自身失败不续期：再推进窗口不出现第三次。
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000)
      })
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('前台自然加载成功后待定重试不再重复拉取（#1291 游标守卫）', async () => {
    const { result } = await renderChat()
    // fake timers 必须先于失败的 load 开启（同上一用例的复验 finding 2）。
    vi.useFakeTimers()
    try {
      mockApi.fetchStudioChatHistoryPage.mockRejectedValueOnce(
        new Error('network down')
      )
      let failed: boolean | undefined
      await act(async () => {
        failed = await result.current.loadOlderHistory()
      })
      expect(failed).toBe(false)
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(1)

      // 2s 重试窗口内滚动/RO/新消息自然触发并成功一页：游标推进，前台加载
      // 承担了重试职责——定时器到点时不得再拉（无游标守卫则重复拉取，红）。
      let ok: boolean | undefined
      await act(async () => {
        ok = await result.current.loadOlderHistory()
      })
      expect(ok).toBe(true)
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(2)
      expect(result.current.messages[0]?.id).toBe('m96')

      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000)
      })
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('前台加载同样失败（游标未推进）：待定重试照常点火（#1291 守卫不误伤）', async () => {
    mockApi.fetchStudioChatHistoryPage.mockRejectedValue(
      new Error('network down')
    )
    const { result } = await renderChat()
    vi.useFakeTimers()
    try {
      await act(async () => {
        await result.current.loadOlderHistory()
      })
      // 第二次失败：??= 不叠加待定重试（#1270）。
      await act(async () => {
        await result.current.loadOlderHistory()
      })
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(2)
      // 游标未推进，重试照常点火；重试自身失败不续期。
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_000)
      })
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(3)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000)
      })
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('窗口内 fail→success→fail：第二次失败重新武装一次性重试（#1296）', async () => {
    const { result } = await renderChat()
    // fake timers 必须先于失败的 load 开启（同 #1291 用例的复验 finding 2）。
    vi.useFakeTimers()
    try {
      // 第一次失败（游标 101）：挂出 2s 重试。
      mockApi.fetchStudioChatHistoryPage.mockRejectedValueOnce(
        new Error('network down')
      )
      await act(async () => {
        await result.current.loadOlderHistory()
      })
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(1)

      // 窗口内前台自然加载成功：游标推进 101 → 96（#1291：前台承担了第一次
      // 失败的重试职责，首次失败的待定定时器到点应空转）。
      await act(async () => {
        await result.current.loadOlderHistory()
      })
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(2)
      expect(result.current.messages[0]?.id).toBe('m96')

      // 同一窗口内第二次失败（游标 96）：??= 占位仍在，但重试必须按最新失败
      // 重新武装——否则点火时比对的是首次失败的游标 101，第二次失败的一次性
      // 重试被游标守卫吞掉，会话静默停在部分历史。
      mockApi.fetchStudioChatHistoryPage.mockRejectedValueOnce(
        new Error('network down again')
      )
      await act(async () => {
        await result.current.loadOlderHistory()
      })
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(3)

      // 到点：比对最新失败游标（96 仍相等）重试点火；无修复时守卫比对首次
      // 失败的 101（游标已是 96）拦下点火——本断言即红。
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_000)
      })
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(4)
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenLastCalledWith(
        'ws1',
        's1',
        96
      )
    } finally {
      vi.useRealTimers()
    }
  })

  it('切换会话清理待定重试：旧会话的失败重试不跨会话触发（#1270）', async () => {
    mockApi.fetchStudioChatMessages.mockImplementation(
      (_workspaceId, sessionId) =>
        Promise.resolve(
          sessionId === 's1'
            ? [textMessage('m101', 101, 'a')]
            : [textMessage('n500', 500, 's2 newest')]
        )
    )
    mockApi.fetchStudioChatHistoryPage.mockRejectedValue(
      new Error('network down')
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

    let failed: boolean | undefined
    // fake timers 必须先于失败的 load 开启（复验 finding 2）：否则重试是
    // 真定时器，cleanup 的 clearTimeout 打在 fake 注册表上是空操作——
    // 有无 cleanup 断言都过（空心）且泄漏真定时器。
    vi.useFakeTimers()
    try {
      await act(async () => {
        failed = await view.result.current.loadOlderHistory()
      })
      expect(failed).toBe(false)
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(1)

      // 切走再推进重试窗口：待定重试已被会话切换的清理取消（无 cleanup 时
      // fake 定时器会在推进时点火、多拉一次——本断言即红）。
      await act(async () => {
        await view.result.current.selectSession('s2')
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000)
      })
      expect(mockApi.fetchStudioChatHistoryPage).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })
})
