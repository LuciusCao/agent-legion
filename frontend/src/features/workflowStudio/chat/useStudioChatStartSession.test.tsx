import { createElement, type ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

describe('useStudioChat startSession 代次守卫（#1265）', () => {
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
    // 会话列表按 workspace 分：ws2 为空（排除记忆恢复噪音）。
    mockApi.fetchStudioChatSessions.mockImplementation((ws: string) =>
      Promise.resolve(ws === 'ws1' ? [sessionRecord()] : [])
    )
    mockApi.fetchStudioChatMessages.mockResolvedValue([])
    mockApi.updateStudioChatContext.mockResolvedValue(sessionRecord())
  })

  afterEach(() => {
    globalThis.EventSource = originalEventSource
  })

  function renderWorkspaceChat() {
    return renderHook(({ ws }: { ws: string }) => useStudioChat(ws), {
      wrapper,
      initialProps: { ws: 'ws1' },
    })
  }

  it('A→B→A 往返后旧创建的成功落地不得劫持会话选择', async () => {
    let resolveCreate!: (record: StudioChatSessionRecord) => void
    mockApi.createStudioChatSession.mockReturnValue(
      new Promise((resolve) => {
        resolveCreate = resolve
      })
    )
    const view = renderWorkspaceChat()
    await waitFor(() =>
      expect(mockApi.fetchStudioChatSessions).toHaveBeenCalled()
    )
    await act(async () => {
      await view.result.current.selectSession('s1')
    })
    await act(async () => {
      void view.result.current.startSession('kimi')
    })
    expect(view.result.current.starting).toBe(true)

    // A→B→A 往返：workspaceIdRef 回到同值，单因子守卫被击败；代次已推进。
    view.rerender({ ws: 'ws2' })
    await waitFor(() => expect(view.result.current.activeSessionId).toBeNull())
    view.rerender({ ws: 'ws1' })
    // 记忆恢复重新选中 s1（代次再推进）。
    await waitFor(() => expect(view.result.current.activeSessionId).toBe('s1'))

    await act(async () => {
      resolveCreate(sessionRecord({ id: 's-new-1' }))
    })
    // 旧创建不得劫持选择（无修复时这里会是 s-new-1——revert 即红）。
    expect(view.result.current.activeSessionId).toBe('s1')
    expect(view.result.current.session?.id ?? null).not.toBe('s-new-1')
  })

  it('第二个异步窗口（invalidate 在途）上的 A→B→A 同样被代次挡住', async () => {
    mockApi.createStudioChatSession.mockResolvedValue(
      sessionRecord({ id: 's-new-1' })
    )
    let resolveInvalidate!: () => void
    vi.spyOn(testClient, 'invalidateQueries').mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          resolveInvalidate = resolve
        })
    )
    const view = renderWorkspaceChat()
    await waitFor(() =>
      expect(mockApi.fetchStudioChatSessions).toHaveBeenCalled()
    )
    await act(async () => {
      await view.result.current.selectSession('s1')
    })
    await act(async () => {
      void view.result.current.startSession('kimi')
    })
    await waitFor(() =>
      expect(mockApi.createStudioChatSession).toHaveBeenCalled()
    )

    view.rerender({ ws: 'ws2' })
    view.rerender({ ws: 'ws1' })
    await waitFor(() => expect(view.result.current.activeSessionId).toBe('s1'))
    await act(async () => {
      resolveInvalidate()
    })
    expect(view.result.current.activeSessionId).toBe('s1')
    expect(view.result.current.session?.id ?? null).not.toBe('s-new-1')
  })

  it('对照组：未发生切换时创建成功仍正常选中新会话', async () => {
    mockApi.createStudioChatSession.mockResolvedValue(
      sessionRecord({ id: 's-new-1' })
    )
    const view = renderWorkspaceChat()
    await waitFor(() =>
      expect(mockApi.fetchStudioChatSessions).toHaveBeenCalled()
    )
    let created: boolean | undefined
    await act(async () => {
      created = await view.result.current.startSession('kimi')
    })
    expect(created).toBe(true)
    expect(view.result.current.activeSessionId).toBe('s-new-1')
    expect(view.result.current.session?.id).toBe('s-new-1')
    expect(view.result.current.starting).toBe(false)
  })
})
