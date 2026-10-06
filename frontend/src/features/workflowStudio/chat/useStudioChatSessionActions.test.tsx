import { createElement, type ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useStudioChat } from './useStudioChat'
import * as chatApi from './studioChatApi'
import type { StudioChatSessionRecord } from './studioChatApi'
import { EventSourceMock } from '../../../testing/eventSourceMock'
import { createTestQueryClient } from '../../../testing/testQueryClient'
import { expectConsoleWarning } from '../../../test-setup-console'

vi.mock('./studioChatApi')
vi.mock('./studioChatResumeApi')

const mockApi = vi.mocked(chatApi)

/* #962：send / cancel / setAllowAll 的会话归属守卫——动作发起后切换了
 * 会话，旧会话的迟到响应（消息、快照、错误）不得写进新会话的状态；
 * 显式携带的 expectedSessionId 与当前会话失配时直接丢弃、不发请求。 */

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

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

// 该 jsdom 环境不提供 localStorage：会话记忆读写用内存 stub。
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

describe('useStudioChat 会话归属守卫（#962）', () => {
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
    mockApi.fetchStudioChatSessions.mockResolvedValue([
      sessionRecord(),
      sessionRecord({ id: 's2' }),
    ])
    mockApi.fetchStudioChatMessages.mockResolvedValue([])
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
  })

  async function renderOnS1() {
    const view = renderHook(() => useStudioChat('ws1'), { wrapper })
    await waitFor(() =>
      expect(mockApi.fetchStudioChatSessions).toHaveBeenCalled()
    )
    await act(async () => {
      view.result.current.selectSession('s1')
    })
    await waitFor(() => expect(view.result.current.session?.id).toBe('s1'))
    return view
  }

  async function switchToS2(view: Awaited<ReturnType<typeof renderOnS1>>) {
    await act(async () => {
      view.result.current.selectSession('s2')
    })
    await waitFor(() => expect(view.result.current.session?.id).toBe('s2'))
  }

  it('drops a send response that lands after switching sessions', async () => {
    expectConsoleWarning(/send 会话归属失配/)
    const pending = deferred<chatApi.StudioChatMessageRecord>()
    mockApi.sendStudioChatMessage.mockReturnValue(pending.promise)
    const view = await renderOnS1()

    let sent: Promise<boolean> | undefined
    act(() => {
      sent = view.result.current.send('你好')
    })
    expect(mockApi.sendStudioChatMessage).toHaveBeenCalledWith(
      'ws1',
      's1',
      '你好'
    )
    await switchToS2(view)

    await act(async () => {
      pending.resolve({
        id: 'u1',
        session_id: 's1',
        kind: 'text',
        role: 'user',
        content: { text: '你好' },
        seq: 1,
        created_at: '2026-01-01T00:00:00Z',
      })
      await sent
    })
    await expect(sent!).resolves.toBe(false)
    expect(view.result.current.messages).toEqual([])
    expect(view.result.current.activeSessionId).toBe('s2')
  })

  it('does not surface a late send failure on the new session', async () => {
    expectConsoleWarning(/send 会话归属失配/)
    const pending = deferred<chatApi.StudioChatMessageRecord>()
    mockApi.sendStudioChatMessage.mockReturnValue(pending.promise)
    const view = await renderOnS1()

    let sent: Promise<boolean> | undefined
    act(() => {
      sent = view.result.current.send('你好')
    })
    await switchToS2(view)
    await act(async () => {
      pending.reject(new Error('会话忙'))
      await sent
    })
    expect(view.result.current.actionError).toBeNull()
  })

  it('drops a cancel snapshot from the previous session', async () => {
    expectConsoleWarning(/cancel 会话归属失配/)
    const pending = deferred<StudioChatSessionRecord>()
    mockApi.cancelStudioChatTurn.mockReturnValue(pending.promise)
    const view = await renderOnS1()

    let cancelled: Promise<void> | undefined
    act(() => {
      cancelled = view.result.current.cancel()
    })
    await switchToS2(view)
    await act(async () => {
      pending.resolve(sessionRecord({ status: 'running' }))
      await cancelled
    })
    expect(view.result.current.session?.id).toBe('s2')
    expect(view.result.current.session?.status).toBe('idle')
  })

  it('drops a setAllowAll snapshot from the previous session', async () => {
    expectConsoleWarning(/setAllowAll 会话归属失配/)
    const pending = deferred<StudioChatSessionRecord>()
    mockApi.setStudioChatAllowAll.mockReturnValue(pending.promise)
    const view = await renderOnS1()

    let toggled: Promise<void> | undefined
    act(() => {
      toggled = view.result.current.setAllowAll(true)
    })
    await switchToS2(view)
    await act(async () => {
      pending.resolve(sessionRecord({ allow_all_permissions: true }))
      await toggled
    })
    expect(view.result.current.session?.id).toBe('s2')
    expect(view.result.current.session?.allow_all_permissions).toBe(false)
  })

  it('rejects actions bound to a session that is no longer active', async () => {
    expectConsoleWarning(/会话归属失配/)
    const view = await renderOnS1()
    await switchToS2(view)

    // 旧渲染帧绑定的 s1 回调（如旧权限卡片的开关、旧取消按钮）落到 s2：
    // 不发请求。
    await act(async () => {
      await view.result.current.setAllowAll(true, 's1')
      await view.result.current.cancel('s1')
    })
    let sent: boolean | undefined
    await act(async () => {
      sent = await view.result.current.send('你好', 's1')
    })
    expect(sent).toBe(false)
    expect(mockApi.setStudioChatAllowAll).not.toHaveBeenCalled()
    expect(mockApi.cancelStudioChatTurn).not.toHaveBeenCalled()
    expect(mockApi.sendStudioChatMessage).not.toHaveBeenCalled()
  })

  it('still applies actions whose bound session matches', async () => {
    mockApi.setStudioChatAllowAll.mockResolvedValue(
      sessionRecord({ allow_all_permissions: true })
    )
    const view = await renderOnS1()
    await act(async () => {
      await view.result.current.setAllowAll(true, 's1')
    })
    expect(mockApi.setStudioChatAllowAll).toHaveBeenCalledWith(
      'ws1',
      's1',
      true
    )
    expect(view.result.current.session?.allow_all_permissions).toBe(true)
  })
})
