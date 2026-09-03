import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAgentPublishRequest } from './useAgentPublishRequest'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useUiStore } from '../../../stores/uiStore'
import type { StudioPublishRequestRecord } from '../../../api/studioPublishRequestApi'

const mocks = {
  fetchPendingPublishRequest: vi.fn(),
  confirmPublishRequest: vi.fn(),
  cancelPublishRequest: vi.fn(),
}

vi.mock('../../../api/studioPublishRequestApi', () => ({
  fetchPendingPublishRequest: (...args: unknown[]) =>
    mocks.fetchPendingPublishRequest(...args),
  confirmPublishRequest: (...args: unknown[]) =>
    mocks.confirmPublishRequest(...args),
  cancelPublishRequest: (...args: unknown[]) =>
    mocks.cancelPublishRequest(...args),
}))

function requestRecord(
  overrides: Partial<StudioPublishRequestRecord> = {}
): StudioPublishRequestRecord {
  return {
    id: 'req-1',
    workspace_id: 'ws1',
    chat_session_id: 's1',
    status: 'pending',
    created_by: 'studio-agent:u1',
    result_revision_id: null,
    created_at: '2026-09-03T10:00:00Z',
    expires_at: '2026-09-03T10:10:00Z',
    resolved_at: null,
    ...overrides,
  }
}

function renderHookWithProviders(workspaceId = 'ws1') {
  return renderHook(() => useAgentPublishRequest(workspaceId), {
    wrapper: TestQueryProvider,
  })
}

describe('useAgentPublishRequest', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useUiStore.setState({ toast: null })
    mocks.fetchPendingPublishRequest.mockResolvedValue(null)
  })

  it('surfaces a pending request from the poll', async () => {
    mocks.fetchPendingPublishRequest.mockResolvedValue(requestRecord())
    const { result } = renderHookWithProviders()

    await waitFor(() => expect(result.current.pendingRequest?.id).toBe('req-1'))
    expect(mocks.fetchPendingPublishRequest).toHaveBeenCalledWith('ws1')
  })

  it('keeps pendingRequest null when the workspace has no request', async () => {
    const { result } = renderHookWithProviders()

    await waitFor(() =>
      expect(mocks.fetchPendingPublishRequest).toHaveBeenCalled()
    )
    expect(result.current.pendingRequest).toBeNull()
  })

  it('confirm calls the confirm endpoint, reloads, and lands a notice', async () => {
    mocks.fetchPendingPublishRequest.mockResolvedValue(requestRecord())
    mocks.confirmPublishRequest.mockResolvedValue(
      requestRecord({
        status: 'confirmed',
        result_revision_id: 'ws1:publish_flow_ws:v2',
        resolved_at: '2026-09-03T10:02:00Z',
      })
    )
    const { result } = renderHookWithProviders()
    await waitFor(() => expect(result.current.pendingRequest?.id).toBe('req-1'))

    await act(async () => {
      await result.current.confirm()
    })

    expect(mocks.confirmPublishRequest).toHaveBeenCalledWith('ws1', 'req-1')
    expect(result.current.resolvedNotice).toContain('ws1:publish_flow_ws:v2')
  })

  it('cancel calls the cancel endpoint and lands a rejection notice', async () => {
    mocks.fetchPendingPublishRequest.mockResolvedValue(requestRecord())
    mocks.cancelPublishRequest.mockResolvedValue(
      requestRecord({ status: 'rejected', resolved_at: '2026-09-03T10:02:00Z' })
    )
    const { result } = renderHookWithProviders()
    await waitFor(() => expect(result.current.pendingRequest?.id).toBe('req-1'))

    await act(async () => {
      await result.current.cancel()
    })

    expect(mocks.cancelPublishRequest).toHaveBeenCalledWith('ws1', 'req-1')
    expect(result.current.resolvedNotice).toContain('已取消')
  })

  it('confirm failure shows an error toast and does not fake success', async () => {
    mocks.fetchPendingPublishRequest.mockResolvedValue(requestRecord())
    mocks.confirmPublishRequest.mockRejectedValue(
      new Error('Publish validation failed')
    )
    const { result } = renderHookWithProviders()
    await waitFor(() => expect(result.current.pendingRequest?.id).toBe('req-1'))

    await act(async () => {
      await result.current.confirm()
    })

    expect(useUiStore.getState().toast?.message).toContain(
      '确认发布失败：Publish validation failed'
    )
    expect(result.current.resolvedNotice).toBeNull()
  })
})
