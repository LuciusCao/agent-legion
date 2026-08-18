import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useStudioContextSync } from './useStudioContextSync'
import * as chatApi from './studioChatApi'
import type { StudioChatSessionRecord } from './studioChatApi'

vi.mock('./studioChatApi')

const mockApi = vi.mocked(chatApi)

function sessionRecord(): StudioChatSessionRecord {
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
  }
}

describe('useStudioContextSync', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.updateStudioChatContext.mockResolvedValue(sessionRecord())
  })

  it('pushes the selection on change and dedupes repeat values', async () => {
    const { rerender } = renderHook(
      ({ key }: { key: string | null }) =>
        useStudioContextSync('ws1', 's1', key),
      { initialProps: { key: null as string | null } }
    )
    await waitFor(() =>
      expect(mockApi.updateStudioChatContext).toHaveBeenCalledWith(
        'ws1',
        's1',
        null
      )
    )
    rerender({ key: null })
    expect(mockApi.updateStudioChatContext).toHaveBeenCalledTimes(1)

    rerender({ key: 'node-a' })
    await waitFor(() =>
      expect(mockApi.updateStudioChatContext).toHaveBeenCalledWith(
        'ws1',
        's1',
        'node-a'
      )
    )
    expect(mockApi.updateStudioChatContext).toHaveBeenCalledTimes(2)
  })

  it('does nothing without a workspace or session', async () => {
    renderHook(() => useStudioContextSync(undefined, 's1', 'node-a'))
    renderHook(() => useStudioContextSync('ws1', null, 'node-a'))
    await new Promise((resolve) => setTimeout(resolve, 10))
    expect(mockApi.updateStudioChatContext).not.toHaveBeenCalled()
  })

  it('re-pushes a value whose earlier push failed', async () => {
    // node-a 推送失败后去重标记必须清除：否则 node-a → node-b → node-a 的
    // 往返里第二次 node-a 会被误判为「已推送」而跳过，服务端滞留 node-b。
    mockApi.updateStudioChatContext.mockRejectedValueOnce(new Error('boom'))
    const { rerender } = renderHook(
      ({ key }: { key: string | null }) =>
        useStudioContextSync('ws1', 's1', key),
      { initialProps: { key: 'node-a' as string | null } }
    )
    await waitFor(() =>
      expect(mockApi.updateStudioChatContext).toHaveBeenCalledTimes(1)
    )
    rerender({ key: 'node-b' })
    await waitFor(() =>
      expect(mockApi.updateStudioChatContext).toHaveBeenLastCalledWith(
        'ws1',
        's1',
        'node-b'
      )
    )
    rerender({ key: 'node-a' })
    await waitFor(() =>
      expect(mockApi.updateStudioChatContext).toHaveBeenLastCalledWith(
        'ws1',
        's1',
        'node-a'
      )
    )
  })
})
