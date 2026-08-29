import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useStudioDraftSync } from './useStudioDraftSync'
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

function renderDraftSync(
  initial: { sessionId: string | null; yaml: string | null },
  workspaceId: string | undefined
) {
  return renderHook(
    ({ sessionId, yaml }: { sessionId: string | null; yaml: string | null }) =>
      useStudioDraftSync(workspaceId, sessionId, yaml),
    { initialProps: initial }
  )
}

describe('useStudioDraftSync', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    mockApi.updateStudioChatContext.mockResolvedValue(sessionRecord())
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('debounces rapid draft changes and pushes only the latest value', () => {
    const { rerender } = renderDraftSync({ sessionId: 's1', yaml: 'a' }, 'ws1')
    rerender({ sessionId: 's1', yaml: 'ab' })
    rerender({ sessionId: 's1', yaml: 'abc' })
    act(() => {
      vi.advanceTimersByTime(399)
    })
    expect(mockApi.updateStudioChatContext).not.toHaveBeenCalled()
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(mockApi.updateStudioChatContext).toHaveBeenCalledTimes(1)
    expect(mockApi.updateStudioChatContext).toHaveBeenCalledWith('ws1', 's1', {
      draftYaml: 'abc',
    })
  })

  it('dedupes a value already pushed to the same session', () => {
    const { rerender } = renderDraftSync({ sessionId: 's1', yaml: 'a' }, 'ws1')
    act(() => {
      vi.advanceTimersByTime(400)
    })
    expect(mockApi.updateStudioChatContext).toHaveBeenCalledTimes(1)

    // 会话切走再切回、draft 未变：lastSent 命中，不重复推送。
    rerender({ sessionId: null, yaml: 'a' })
    rerender({ sessionId: 's1', yaml: 'a' })
    act(() => {
      vi.advanceTimersByTime(400)
    })
    expect(mockApi.updateStudioChatContext).toHaveBeenCalledTimes(1)
  })

  it('clears the dedupe marker after a failed push so the next change retries', async () => {
    mockApi.updateStudioChatContext.mockRejectedValueOnce(new Error('boom'))
    const { rerender } = renderDraftSync({ sessionId: 's1', yaml: 'a' }, 'ws1')
    await act(async () => {
      vi.advanceTimersByTime(400)
    })
    expect(mockApi.updateStudioChatContext).toHaveBeenCalledTimes(1)

    rerender({ sessionId: 's1', yaml: 'ab' })
    await act(async () => {
      vi.advanceTimersByTime(400)
    })
    expect(mockApi.updateStudioChatContext).toHaveBeenCalledTimes(2)
    expect(mockApi.updateStudioChatContext).toHaveBeenLastCalledWith(
      'ws1',
      's1',
      { draftYaml: 'ab' }
    )
  })

  it('does not push without a workspace, session, or draft', () => {
    renderDraftSync({ sessionId: null, yaml: 'a' }, 'ws1')
    renderDraftSync({ sessionId: 's1', yaml: null }, 'ws1')
    renderDraftSync({ sessionId: 's1', yaml: 'a' }, undefined)
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(mockApi.updateStudioChatContext).not.toHaveBeenCalled()
  })
})
