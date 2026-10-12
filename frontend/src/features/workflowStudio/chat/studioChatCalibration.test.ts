import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  calibrateWithRowRefetch,
  handleMissedMessageFrame,
  inFlightInPlaceRowIds,
  type RowRefetchDeps,
} from './studioChatCalibration'
import { api } from '../../../api/core'
import type { ChatMessage } from './studioChatMessages'
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

function toolCallMessage(id: string, seq: number, status: string): ChatMessage {
  return {
    id,
    session_id: 's1',
    kind: 'tool_call',
    role: 'agent',
    content: { tool_call_id: id, status },
    seq,
    created_at: '2026-01-01T00:00:00Z',
  }
}

function recordOf(message: ChatMessage): StudioChatMessageRecord {
  return { ...message }
}

function makeDeps(current: ChatMessage[]) {
  const state = { messages: current }
  const deps: RowRefetchDeps & { messagesRef: { current: ChatMessage[] } } = {
    workspaceId: 'ws1',
    sessionId: 's1',
    sessionGenerationRef: { current: 1 },
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
