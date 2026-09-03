import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAgentPublishRequest } from './useAgentPublishRequest'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { useUiStore } from '../../../stores/uiStore'
import { useAgentPublishNoticeStore } from './agentPublishNoticeStore'
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
    useAgentPublishNoticeStore.setState({ resolvedNotice: null })
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

  it('confirm of a runtime-only save lands the no-new-revision notice', async () => {
    mocks.fetchPendingPublishRequest.mockResolvedValue(requestRecord())
    mocks.confirmPublishRequest.mockResolvedValue(
      requestRecord({
        status: 'confirmed',
        result_revision_id: null,
        resolved_at: '2026-09-03T10:02:00Z',
      })
    )
    const { result } = renderHookWithProviders()
    await waitFor(() => expect(result.current.pendingRequest?.id).toBe('req-1'))

    await act(async () => {
      await result.current.confirm()
    })

    expect(result.current.resolvedNotice).toContain('未产生新版本')
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
    expect(result.current.resolvedNotice).toContain('已拒绝')
  })

  it('notice is shared across hook instances (dialog action visible in the aside)', async () => {
    // #429 P2-1 回归钉：回执是跨实例共享状态（zustand store），不是每实例
    // 一份的 useState——对话框实例 resolve 后，另一个实例（栏顶）同轮可读。
    mocks.fetchPendingPublishRequest.mockResolvedValue(requestRecord())
    mocks.cancelPublishRequest.mockResolvedValue(
      requestRecord({ status: 'rejected', resolved_at: '2026-09-03T10:02:00Z' })
    )
    const dialog = renderHookWithProviders()
    const aside = renderHookWithProviders()
    await waitFor(() =>
      expect(dialog.result.current.pendingRequest?.id).toBe('req-1')
    )

    await act(async () => {
      await dialog.result.current.cancel()
    })

    expect(aside.result.current.resolvedNotice).toContain('已拒绝')
    // 关闭（clearNotice）同样作用于共享层。
    await act(async () => {
      aside.result.current.clearNotice()
    })
    expect(aside.result.current.resolvedNotice).toBeNull()
    expect(dialog.result.current.resolvedNotice).toBeNull()
  })

  it('lands a superseded notice when the pending request vanishes externally', async () => {
    // #429 P2-3 前端：手动发布/新请求在后端顶替 pending → 轮询返回 null →
    // 弹窗无声关闭是不可接受的：这里必须补一轮回执。
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      mocks.fetchPendingPublishRequest.mockResolvedValueOnce(requestRecord())
      const { result } = renderHookWithProviders()
      await waitFor(() =>
        expect(result.current.pendingRequest?.id).toBe('req-1')
      )

      // 下一轮轮询：请求没了（被手动发布顶替）。
      mocks.fetchPendingPublishRequest.mockResolvedValue(null)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_100)
      })

      expect(result.current.pendingRequest).toBeNull()
      expect(result.current.resolvedNotice).toContain('已消解')
    } finally {
      vi.useRealTimers()
    }
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
