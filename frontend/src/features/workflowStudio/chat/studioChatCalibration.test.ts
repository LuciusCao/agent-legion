import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import type { QueryClient } from '@tanstack/react-query'
import {
  calibrateWithRowRefetch,
  hasInFlightToolCall,
  inFlightInPlaceRowIds,
  type RowRefetchDeps,
} from './studioChatCalibration'
import { handleMissedMessageFrame } from './studioChatMissedRefetch'
import { handleSseMessageEvent } from './studioChatEvents'
import { api } from '../../../api/core'
import { upsertMessage, type ChatMessage } from './studioChatMessages'
import type { StudioChatMessageRecord } from './studioChatApi'

vi.mock('../../../api/core', () => ({ api: vi.fn() }))

const mockApi = vi.mocked(api)

function textMessage(id: string, seq: number, text: string): ChatMessage {
  return {
    id,
    session_id: 's1',
    kind: 'text',
    role: 'agent',
    content: { text },
    seq,
    created_at: '2026-01-01T00:00:00Z',
  }
}

function toolCallMessage(
  id: string,
  seq: number,
  status: string,
  extraContent?: Record<string, unknown>
): ChatMessage {
  return {
    id,
    session_id: 's1',
    kind: 'tool_call',
    role: 'agent',
    content: { tool_call_id: id, status, ...extraContent },
    seq,
    created_at: '2026-01-01T00:00:00Z',
  }
}

function recordOf(message: ChatMessage): StudioChatMessageRecord {
  return { ...message }
}

function statusMessage(id: string, seq: number, event: string): ChatMessage {
  return {
    id,
    session_id: 's1',
    kind: 'status',
    role: 'system',
    content: { event },
    seq,
    created_at: '2026-01-01T00:00:00Z',
  }
}

function makeDeps(current: ChatMessage[]) {
  const state = { messages: current }
  const deps: RowRefetchDeps = {
    workspaceId: 'ws1',
    sessionId: 's1',
    sessionGenerationRef: { current: 1 },
    missedRetryRef: { current: new Map<string, number>() },
    messagesRef: {
      get current() {
        return state.messages
      },
    } as {
      current: ChatMessage[]
    },
    setMessages: (updater) => {
      state.messages = updater(state.messages)
    },
    refillMessages: vi.fn(async () => false),
    onTerminal: vi.fn(),
  }
  return { deps, state }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('inFlightInPlaceRowIds（#1236）', () => {
  it('收集流式 text 与未终结 tool_call；终结行不收集', () => {
    const ids = inFlightInPlaceRowIds([
      toolCallMessage('tc1', 1, 'completed'),
      toolCallMessage('tc2', 2, 'in_progress'),
      textMessage('m3', 3, 'streaming…'),
    ])
    expect(ids).toEqual(['tc2', 'm3'])
  })

  it('tool_call status 缺失/畸形按在途处理（宁多勿漏）', () => {
    expect(inFlightInPlaceRowIds([toolCallMessage('tc1', 1, '')])).toEqual([
      'tc1',
    ])
  })

  it('terminal 状态行之后不再有在途流式 text', () => {
    const ids = inFlightInPlaceRowIds([
      textMessage('m1', 1, 'old stream'),
      {
        id: 'st1',
        session_id: 's1',
        kind: 'status',
        role: 'system',
        content: { event: 'turn_end' },
        seq: 2,
        created_at: '2026-01-01T00:00:00Z',
      },
    ])
    expect(ids).toEqual([])
  })
})

describe('handleMissedMessageFrame（#1236 missed 帧按 id 点读）', () => {
  it('点读成功：未知 id 的行合入列表，后续更新帧可正常 upsert', async () => {
    const { deps, state } = makeDeps([textMessage('m1', 1, 'a')])
    mockApi.mockResolvedValueOnce({
      message: recordOf(toolCallMessage('tc9', 9, 'in_progress')),
    })
    handleMissedMessageFrame(deps, 'tc9')
    await vi.waitFor(() => expect(state.messages).toHaveLength(2))
    expect(state.messages[1]).toMatchObject({ id: 'tc9', seq: 9 })
    expect(mockApi).toHaveBeenCalledWith(
      '/api/workspaces/ws1/studio-chat/sessions/s1/messages/tc9'
    )
    expect(deps.refillMessages).not.toHaveBeenCalled()
  })

  it('点读失败回退旧的增量补齐；补齐带 terminal 时连锁全量回取与查询失效', async () => {
    const { deps } = makeDeps([textMessage('m1', 1, 'a')])
    mockApi.mockRejectedValueOnce(new Error('404'))
    vi.mocked(deps.refillMessages)
      .mockResolvedValueOnce(true)
      .mockResolvedValueOnce(false)
    handleMissedMessageFrame(deps, 'tc9')
    await vi.waitFor(() => expect(deps.onTerminal).toHaveBeenCalled())
    expect(deps.refillMessages).toHaveBeenNthCalledWith(1)
    expect(deps.refillMessages).toHaveBeenNthCalledWith(2, 0)
  })

  it('落地前代次已推进（A→B→A）：迟到响应丢弃不合入', async () => {
    const { deps, state } = makeDeps([textMessage('m1', 1, 'a')])
    let resolveFetch!: (value: unknown) => void
    mockApi.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFetch = resolve
      }) as ReturnType<typeof api>
    )
    handleMissedMessageFrame(deps, 'tc9')
    deps.sessionGenerationRef.current += 1
    resolveFetch({ message: recordOf(toolCallMessage('tc9', 9, 'failed')) })
    await vi.waitFor(() => expect(mockApi).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(state.messages).toHaveLength(1)
  })
})

describe('calibrateWithRowRefetch（#1236 窗口外在途行补取）', () => {
  it('全量校准后对在途原地更新行按 id 逐个重取', async () => {
    const inFlight = toolCallMessage('tc2', 2, 'in_progress')
    const { deps } = makeDeps([textMessage('m1', 1, 'a'), inFlight])
    mockApi.mockResolvedValue({
      message: recordOf(toolCallMessage('tc2', 2, 'completed')),
    })
    const hasTerminal = await calibrateWithRowRefetch(deps)
    expect(hasTerminal).toBe(false)
    expect(deps.refillMessages).toHaveBeenCalledWith(0)
    await vi.waitFor(() =>
      expect(mockApi).toHaveBeenCalledWith(
        '/api/workspaces/ws1/studio-chat/sessions/s1/messages/tc2'
      )
    )
  })

  it('没有的在途行不重取；refill 的 terminal 判定原样返回', async () => {
    // role=user：agent text 行无终结即视为流式在途（#563 streamingTextId 语义）。
    const { deps } = makeDeps([{ ...textMessage('m1', 1, 'a'), role: 'user' }])
    vi.mocked(deps.refillMessages).mockResolvedValueOnce(true)
    const hasTerminal = await calibrateWithRowRefetch(deps)
    expect(hasTerminal).toBe(true)
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('refill 失败不重取（错误上抛给调用方既有 catch）', async () => {
    const { deps } = makeDeps([toolCallMessage('tc2', 2, 'in_progress')])
    vi.mocked(deps.refillMessages).mockRejectedValueOnce(new Error('down'))
    await expect(calibrateWithRowRefetch(deps)).rejects.toThrow('down')
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('入口与 refill 落地之间切换会话：点读不点火（代次钉在入口，复验 finding 1）', async () => {
    const { deps } = makeDeps([toolCallMessage('tc2', 2, 'in_progress')])
    let resolveRefill!: (value: boolean) => void
    vi.mocked(deps.refillMessages).mockReturnValueOnce(
      new Promise<boolean>((resolve) => {
        resolveRefill = resolve
      })
    )
    const pending = calibrateWithRowRefetch(deps)
    // refill 在途期间发生会话切换（A→B→A 代次同样推进）。
    deps.sessionGenerationRef.current += 1
    resolveRefill(false)
    await pending
    // 无修复时这里会用切换后的新代次点火点读——本断言即红（mutation 自检过）。
    expect(mockApi).not.toHaveBeenCalled()
  })
})

describe('inFlightInPlaceRowIds 最近 terminal 截断（#1290）', () => {
  it('最近 terminal 之前的死行（取消/超时遗留的 pending/in_progress）不重取', () => {
    const ids = inFlightInPlaceRowIds([
      toolCallMessage('tc1', 1, 'in_progress'),
      statusMessage('st1', 2, 'turn_end'),
      toolCallMessage('tc2', 3, 'in_progress'),
      textMessage('m4', 4, 'streaming…'),
    ])
    expect(ids).toEqual(['tc2', 'm4'])
  })

  it('整个已加载窗口没有 terminal：保持全窗口扫描（无法判定 turn 边界，宁多勿漏）', () => {
    expect(
      inFlightInPlaceRowIds([toolCallMessage('tc1', 1, 'in_progress')])
    ).toEqual(['tc1'])
  })

  it('hasInFlightToolCall 同样截断：历史死行不再触发全量校准', () => {
    expect(
      hasInFlightToolCall([
        toolCallMessage('tc1', 1, 'in_progress'),
        statusMessage('st1', 2, 'turn_end'),
      ])
    ).toBe(false)
    expect(
      hasInFlightToolCall([
        toolCallMessage('tc1', 1, 'in_progress'),
        statusMessage('st1', 2, 'turn_end'),
        toolCallMessage('tc2', 3, 'pending'),
      ])
    ).toBe(true)
  })

  it('校准时历史死行不按 id 点读（每次 turn 结束/重连不再重复点读）', async () => {
    const { deps } = makeDeps([
      toolCallMessage('tc1', 1, 'in_progress'),
      statusMessage('st1', 2, 'turn_end'),
      toolCallMessage('tc2', 3, 'in_progress'),
    ])
    mockApi.mockResolvedValue({
      message: recordOf(toolCallMessage('tc2', 3, 'completed')),
    })
    await calibrateWithRowRefetch(deps)
    await vi.waitFor(() => expect(mockApi).toHaveBeenCalledTimes(1))
    expect(mockApi).toHaveBeenCalledWith(
      '/api/workspaces/ws1/studio-chat/sessions/s1/messages/tc2'
    )
  })
})

describe('点读落地的新鲜度守卫（#1288）', () => {
  it('GET 在途期间本地行被更新的 SSE 快照超越：较旧点读响应丢弃（流式文本）', async () => {
    const { deps, state } = makeDeps([textMessage('m1', 1, 'abc')])
    let resolveFetch!: (value: unknown) => void
    mockApi.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFetch = resolve
      }) as ReturnType<typeof api>
    )
    await calibrateWithRowRefetch(deps)
    await vi.waitFor(() => expect(mockApi).toHaveBeenCalled())
    // GET 读库后、响应返回前：更新的流式快照已合入本地行。
    deps.setMessages(
      (current) =>
        upsertMessage(current, { id: 'm1', content: { text: 'abcdef' } }) ??
        current
    )
    resolveFetch({ message: recordOf(textMessage('m1', 1, 'abc')) })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(state.messages[0]).toMatchObject({ content: { text: 'abcdef' } })
  })

  it('GET 在途期间 tool call 已终结：较旧点读响应不得把它恢复 in_progress', async () => {
    const { deps, state } = makeDeps([toolCallMessage('tc1', 1, 'in_progress')])
    let resolveFetch!: (value: unknown) => void
    mockApi.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFetch = resolve
      }) as ReturnType<typeof api>
    )
    await calibrateWithRowRefetch(deps)
    await vi.waitFor(() => expect(mockApi).toHaveBeenCalled())
    deps.setMessages(
      (current) =>
        upsertMessage(current, {
          id: 'tc1',
          content: { status: 'completed' },
        }) ?? current
    )
    resolveFetch({
      message: recordOf(toolCallMessage('tc1', 1, 'in_progress')),
    })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(state.messages[0]).toMatchObject({
      content: { status: 'completed' },
    })
  })

  it('点读快照更新（本地未被超越）：照常合入', async () => {
    const { deps, state } = makeDeps([toolCallMessage('tc1', 1, 'in_progress')])
    mockApi.mockResolvedValueOnce({
      message: recordOf(toolCallMessage('tc1', 1, 'completed')),
    })
    await calibrateWithRowRefetch(deps)
    await vi.waitFor(() => expect(mockApi).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(state.messages[0]).toMatchObject({
      content: { status: 'completed' },
    })

    const { deps: deps2, state: state2 } = makeDeps([
      textMessage('m1', 1, 'abc'),
    ])
    mockApi.mockResolvedValueOnce({
      message: recordOf(textMessage('m1', 1, 'abcdef')),
    })
    await calibrateWithRowRefetch(deps2)
    await vi.waitFor(() => expect(mockApi).toHaveBeenCalledTimes(2))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(state2.messages[0]).toMatchObject({ content: { text: 'abcdef' } })
  })
})

describe('点读落地的点火快照守卫（#1299）', () => {
  it('GET 在途期间同 tool_call 收到非终态更新（status 均 in_progress）：旧点读快照不得覆盖较新本地字段', async () => {
    const { deps, state } = makeDeps([
      toolCallMessage('tc1', 1, 'in_progress', { rawOutput: 'out1' }),
    ])
    let resolveFetch!: (value: unknown) => void
    mockApi.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFetch = resolve
      }) as ReturnType<typeof api>
    )
    await calibrateWithRowRefetch(deps)
    await vi.waitFor(() => expect(mockApi).toHaveBeenCalled())
    // GET 读库后、响应返回前：rawOutput 刷新的非终态 SSE 帧合入本地行——
    // 终态比较判不出新旧（两侧 status 都是 in_progress），#1288 守卫放行。
    deps.setMessages(
      (current) =>
        upsertMessage(current, { id: 'tc1', content: { rawOutput: 'out2' } }) ??
        current
    )
    // 点读响应是 SSE 帧之前读库的旧快照（rawOutput 仍为 out1）。
    resolveFetch({
      message: recordOf(
        toolCallMessage('tc1', 1, 'in_progress', { rawOutput: 'out1' })
      ),
    })
    await new Promise((resolve) => setTimeout(resolve, 0))
    // 本地行已偏离点火快照 = 在途期间被更新的 SSE 帧改写，旧点读结果丢弃；
    // 无修复时 upsert 把 rawOutput 盖回 out1（若那是最后一帧界面永久回退）。
    expect(state.messages[0]).toMatchObject({
      content: { status: 'in_progress', rawOutput: 'out2' },
    })
  })

  it('本地行在途期间未变：点读快照照常合入（守卫不误伤）', async () => {
    const { deps, state } = makeDeps([
      toolCallMessage('tc1', 1, 'in_progress', { rawOutput: 'out1' }),
    ])
    mockApi.mockResolvedValueOnce({
      message: recordOf(
        toolCallMessage('tc1', 1, 'in_progress', { rawOutput: 'out2' })
      ),
    })
    await calibrateWithRowRefetch(deps)
    await vi.waitFor(() => expect(mockApi).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(state.messages[0]).toMatchObject({
      content: { status: 'in_progress', rawOutput: 'out2' },
    })
  })
})

describe('terminal 实时到达当轮的校准截断（#1300）', () => {
  it('calibrateWithRowRefetch scanBasis：截断按传入的合入快照计算', async () => {
    const { deps } = makeDeps([toolCallMessage('tc1', 1, 'in_progress')])
    const merged = upsertMessage(
      deps.messagesRef.current,
      statusMessage('st1', 2, 'turn_end')
    )
    await calibrateWithRowRefetch(deps, merged ?? undefined)
    // 快照里 terminal 已在 tc1 之后：tc1 是刚终结 turn 的死行，不点读。
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('handleSseMessageEvent：messagesRef 尚未合入刚到的 terminal 时，校准截断仍按合入后快照', async () => {
    const before = [toolCallMessage('tc1', 1, 'in_progress')]
    // React 被动效应才同步 messagesRef：事件当轮它仍是合入前的列表。
    const laggingRef = { current: before }
    let applied = before
    const deps = {
      workspaceId: 'ws1',
      messagesRef: laggingRef,
      queryClient: {
        invalidateQueries: vi.fn(),
      } as unknown as QueryClient,
      setMessages: (updater: (current: ChatMessage[]) => ChatMessage[]) => {
        applied = updater(applied)
      },
      setSession: vi.fn(),
      refillMessages: vi.fn(async () => false),
      fetchSession: vi.fn(),
      activeSessionId: 's1',
      sessionGenerationRef: { current: 1 },
      missedRetryRef: { current: new Map<string, number>() },
    }
    handleSseMessageEvent(statusMessage('st1', 2, 'turn_end'), deps)
    await vi.waitFor(() => expect(deps.refillMessages).toHaveBeenCalledWith(0))
    await new Promise((resolve) => setTimeout(resolve, 0))
    // terminal 的合入照常排队；刚终结 turn 的 tc1 是死行，不得点读——
    // 无修复时 ids 快照来自滞后 ref（无 terminal），tc1 被误判在途点读（红）。
    expect(applied.map((m) => m.id)).toEqual(['tc1', 'st1'])
    expect(mockApi).not.toHaveBeenCalled()
  })
})

describe('整行冲突丢弃后的收口点读（#1305）', () => {
  it('GET 在途期间收到中间 SSE、服务端随后持久化的更新快照（SSE 丢失）被整行守卫误丢：补一次收口点读落地最新库快照', async () => {
    const { deps, state } = makeDeps([
      toolCallMessage('tc1', 1, 'in_progress', { rawOutput: 'out1' }),
    ])
    let resolveFetch!: (value: unknown) => void
    mockApi.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFetch = resolve
        }) as ReturnType<typeof api>
    )
    await calibrateWithRowRefetch(deps)
    await vi.waitFor(() => expect(mockApi).toHaveBeenCalledTimes(1))
    // GET 在途期间：中间 SSE 快照合入本地行（rawOutput 刷新、status 不变——
    // 终态/长度比较判不出新旧，#1288 守卫放行的形态）。
    deps.setMessages(
      (current) =>
        upsertMessage(current, { id: 'tc1', content: { rawOutput: 'out2' } }) ??
        current
    )
    // 服务端随后持久化 out3 而对应 SSE 丢失：GET 的库读取发生在持久化之后，
    // 响应携带的其实是较新的 out3——整行守卫仍按偏离无条件丢弃（#1299）。
    mockApi.mockResolvedValueOnce({
      message: recordOf(
        toolCallMessage('tc1', 1, 'in_progress', { rawOutput: 'out3' })
      ),
    })
    resolveFetch({
      message: recordOf(
        toolCallMessage('tc1', 1, 'in_progress', { rawOutput: 'out3' })
      ),
    })
    // 丢弃后补一次收口点读：第二次 GET 读得 out3，本地未再偏离即落地。
    // 无修复时只有这一次 GET、界面永停中间态 out2——本断言即红
    //（mutation：摘掉收口点读即红，已自检）。
    await vi.waitFor(() => expect(mockApi).toHaveBeenCalledTimes(2))
    await vi.waitFor(() =>
      expect(state.messages[0]).toMatchObject({
        content: { status: 'in_progress', rawOutput: 'out3' },
      })
    )
    // 收口点读正常落地后不再续挂第三次（有界）。
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mockApi).toHaveBeenCalledTimes(2)
  })

  it('收口点读在途期间本地再被 SSE 改写：再次冲突丢弃但不续挂（有界），本地保留最新 SSE 内容', async () => {
    const { deps, state } = makeDeps([textMessage('m1', 1, 'a')])
    const resolvers: ((value: unknown) => void)[] = []
    mockApi.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvers.push(resolve)
        }) as ReturnType<typeof api>
    )
    await calibrateWithRowRefetch(deps)
    await vi.waitFor(() => expect(resolvers).toHaveLength(1))
    // 持续流式：第一次点读在途期间本地推进到 ab。
    deps.setMessages(
      (current) =>
        upsertMessage(current, { id: 'm1', content: { text: 'ab' } }) ?? current
    )
    // 旧快照 a 被整行守卫丢弃，挂出唯一一次收口点读。
    resolvers[0]({ message: recordOf(textMessage('m1', 1, 'a')) })
    await vi.waitFor(() => expect(resolvers).toHaveLength(2))
    // 收口点读在途期间又推进到 abc：再次偏离 → 丢弃；不再续挂第三次
    //（mutation：递归标记失效会点火第三次点读——本断言即红）。
    deps.setMessages(
      (current) =>
        upsertMessage(current, { id: 'm1', content: { text: 'abc' } }) ??
        current
    )
    resolvers[1]({ message: recordOf(textMessage('m1', 1, 'ab')) })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(resolvers).toHaveLength(2)
    expect(state.messages[0]).toMatchObject({ content: { text: 'abc' } })
  })
})

describe('missed 帧点读瞬时失败的有界重试（#1289）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('瞬时失败：对目标行短延迟重试，成功即合入；增量回退保持不变', async () => {
    const { deps, state } = makeDeps([textMessage('m1', 1, 'a')])
    mockApi
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce({
        message: recordOf(toolCallMessage('tc9', 9, 'completed')),
      })
    handleMissedMessageFrame(deps, 'tc9')
    await vi.advanceTimersByTimeAsync(0)
    // 既有增量回退（terminal 连锁）保留；点读失败后安排有界重试。
    expect(deps.refillMessages).toHaveBeenCalledTimes(1)
    expect(mockApi).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2_000)
    expect(mockApi).toHaveBeenCalledTimes(2)
    expect(state.messages.map((m) => m.id)).toEqual(['m1', 'tc9'])
  })

  it('持续失败：重试有界后停止，不无限空转', async () => {
    const { deps } = makeDeps([textMessage('m1', 1, 'a')])
    mockApi.mockRejectedValue(new Error('down'))
    handleMissedMessageFrame(deps, 'tc9')
    await vi.advanceTimersByTimeAsync(0)
    expect(mockApi).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2_000)
    expect(mockApi).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(2_000)
    expect(mockApi).toHaveBeenCalledTimes(3)
    // 界已用尽：再推进窗口不再点火，等下次校准/事件的自然触发。
    await vi.advanceTimersByTimeAsync(60_000)
    expect(mockApi).toHaveBeenCalledTimes(3)
  })

  it('同一 id 的重复 missed 帧不叠加重试链（按代次去重）', async () => {
    const { deps } = makeDeps([textMessage('m1', 1, 'a')])
    mockApi.mockRejectedValue(new Error('down'))
    handleMissedMessageFrame(deps, 'tc9')
    handleMissedMessageFrame(deps, 'tc9')
    await vi.advanceTimersByTimeAsync(0)
    // 两帧各点读一次（既有行为），失败只挂一条重试链。
    expect(mockApi).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(10_000)
    // 一条链 = 2 次重试；无去重时两条链共 4 次重试（总数 6）——本断言即红。
    expect(mockApi).toHaveBeenCalledTimes(4)
  })

  it('重试点火前会话代次已推进：旧会话的迟到重试不点火', async () => {
    const { deps } = makeDeps([textMessage('m1', 1, 'a')])
    mockApi.mockRejectedValue(new Error('down'))
    handleMissedMessageFrame(deps, 'tc9')
    await vi.advanceTimersByTimeAsync(0)
    deps.sessionGenerationRef.current += 1
    await vi.advanceTimersByTimeAsync(10_000)
    expect(mockApi).toHaveBeenCalledTimes(1)
  })

  it('首次点读在途期间切换会话后才失败：重试链沿用点火代次快照，不按新代次点火（#1297 r2 P1）', async () => {
    const { deps, state } = makeDeps([textMessage('m1', 1, 'a')])
    let rejectFetch!: (reason: unknown) => void
    mockApi.mockReturnValueOnce(
      new Promise((_, reject) => {
        rejectFetch = reject
      }) as ReturnType<typeof api>
    )
    handleMissedMessageFrame(deps, 'tc9')
    // 首次点读在途期间切换会话：共享 ref 已是新会话代次，deps.sessionId
    // 仍属旧会话——挂链时现读代次会穿守卫（mutation：改回现读即红）。
    deps.sessionGenerationRef.current += 1
    rejectFetch(new Error('down'))
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(30_000)
    expect(mockApi).toHaveBeenCalledTimes(1)
    expect(state.messages.map((m) => m.id)).toEqual(['m1'])
  })
})
