import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useAgentPublishRequest } from './useAgentPublishRequest'
import {
  createTestQueryClient,
  TestQueryProvider,
} from '../../../testing/testQueryClient'
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
    draft_hash: null,
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

/** 共享 QueryClient 渲染多个 hook 实例：复刻生产拓扑（对话框与栏顶各自
 * 挂载 useAgentPublishRequest，同一 QueryClient、同一 queryKey，invalidate
 * 后双方同时收到同一次重取结果）。#429 二轮复审的回归正发生在双实例
 * 设置下——独立 QueryClient + 永远 pending 的 mock 让 pending→null 跳变
 * 从未在双实例下发生。 */
function renderTwoHooksSharingOneQueryClient() {
  const client = createTestQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const dialog = renderHook(() => useAgentPublishRequest('ws1'), { wrapper })
  const aside = renderHook(() => useAgentPublishRequest('ws1'), { wrapper })
  return { dialog, aside }
}

describe('useAgentPublishRequest', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useUiStore.setState({ toast: null })
    useAgentPublishNoticeStore.setState({
      resolvedNotice: null,
      lastResolvedRequestId: null,
    })
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

  it('a second cancel while one is in flight is a no-op (no 404 fake-failure toast)', async () => {
    // #429 三轮复审 P3 回归钉：cancel 不设守卫时，双击「返回编辑」或
    // cancel 在途按 ESC 会二次调 cancel——第一击已 resolve 掉请求，第二
    // 击必 404，红色「取消失败」toast 与正确的「已拒绝」回执同现。现在
    // cancel 在途（canceling）期间重复调用必须早退：端点只打一次、无
    // 错误 toast、成功后 canceling 复位。
    mocks.fetchPendingPublishRequest.mockResolvedValue(requestRecord())
    let releaseCancel: ((record: StudioPublishRequestRecord) => void) | null =
      null
    mocks.cancelPublishRequest.mockImplementation(
      () =>
        new Promise<StudioPublishRequestRecord>((resolve) => {
          releaseCancel = resolve
        })
    )
    const { result } = renderHookWithProviders()
    await waitFor(() => expect(result.current.pendingRequest?.id).toBe('req-1'))

    let firstCancel: Promise<void> | null = null
    act(() => {
      firstCancel = result.current.cancel()
    })
    // 在途：canceling 已置位，第二击（双击/ESC 同源）是 no-op。
    expect(result.current.canceling).toBe(true)
    await act(async () => {
      await result.current.cancel()
    })
    expect(mocks.cancelPublishRequest).toHaveBeenCalledTimes(1)

    await act(async () => {
      releaseCancel?.(
        requestRecord({
          status: 'rejected',
          resolved_at: '2026-09-03T10:02:00Z',
        })
      )
      await firstCancel
    })

    expect(mocks.cancelPublishRequest).toHaveBeenCalledTimes(1)
    expect(result.current.canceling).toBe(false)
    expect(result.current.resolvedNotice).toContain('已拒绝')
    expect(useUiStore.getState().toast).toBeNull()
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

  it('a bystander instance does not overwrite the receipt after confirm (shared QueryClient, pending→null)', async () => {
    // #429 二轮复审 P2 回归钉（生产拓扑的最小复刻）：对话框与栏顶共享
    // QueryClient 和 queryKey。确认后 dialog 实例着陆正确回执并 invalidate；
    // 随后轮询重取返回 null，两个实例先后看到 pending→null。旧实现的
    // resolve 归属是 per-instance ref——旁观实例（selfResolvedId 恒 null）
    // 会拿「已消解」覆盖「已按 Agent 请求发布」。现在归属存共享 store，
    // 旁观者必须沉默。mock 前两轮 pending、之后 null：确认路径的
    // invalidate 消费第 2 轮，第 3 轮由轮询定时器触发——invalidate 与轮询
    // 两个通道都会推来 null，任一通道都必须被归属守卫拦下。
    let calls = 0
    mocks.fetchPendingPublishRequest.mockImplementation(async () => {
      calls += 1
      return calls <= 2 ? requestRecord() : null
    })
    mocks.confirmPublishRequest.mockResolvedValue(
      requestRecord({
        status: 'confirmed',
        result_revision_id: 'ws1:publish_flow_ws:v2',
        resolved_at: '2026-09-03T10:02:00Z',
      })
    )
    const { dialog, aside } = renderTwoHooksSharingOneQueryClient()
    await waitFor(() =>
      expect(dialog.result.current.pendingRequest?.id).toBe('req-1')
    )

    await act(async () => {
      await dialog.result.current.confirm()
    })

    // 第 3 轮轮询返回 null：双方都看到 pending 消失。
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5_300))
    })
    expect(dialog.result.current.pendingRequest).toBeNull()
    expect(aside.result.current.pendingRequest).toBeNull()
    // 正确回执未被旁观实例覆盖成「已消解」。
    expect(aside.result.current.resolvedNotice).toContain(
      '已按 Agent 请求发布（revision ws1:publish_flow_ws:v2）'
    )
    expect(dialog.result.current.resolvedNotice).toContain(
      '已按 Agent 请求发布（revision ws1:publish_flow_ws:v2）'
    )
  })

  it('a bystander instance does not overwrite the receipt after cancel (shared QueryClient, pending→null)', async () => {
    // 同上，但走取消路径：取消后的「已拒绝」回执同样不得被旁观者的
    // pending→null 观测覆盖。
    let calls = 0
    mocks.fetchPendingPublishRequest.mockImplementation(async () => {
      calls += 1
      return calls <= 2 ? requestRecord() : null
    })
    mocks.cancelPublishRequest.mockResolvedValue(
      requestRecord({ status: 'rejected', resolved_at: '2026-09-03T10:02:00Z' })
    )
    const { dialog, aside } = renderTwoHooksSharingOneQueryClient()
    await waitFor(() =>
      expect(dialog.result.current.pendingRequest?.id).toBe('req-1')
    )

    await act(async () => {
      await dialog.result.current.cancel()
    })

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5_300))
    })
    expect(dialog.result.current.pendingRequest).toBeNull()
    expect(aside.result.current.pendingRequest).toBeNull()
    expect(aside.result.current.resolvedNotice).toContain('已拒绝')
    expect(dialog.result.current.resolvedNotice).toContain('已拒绝')
  })

  it('still lands the resolved-away notice when nobody resolved the request (external supersede)', async () => {
    // 守卫的另一面：无人主动 resolve（agent 重发 / 手动发布顶替 / TTL 过
    // 期）时，pending→null 必须照旧着陆「已消解」——共享 store 里没有该
    // id 的 resolve 记录。双实例设置下验证，确保归属检查没有误伤。
    let calls = 0
    mocks.fetchPendingPublishRequest.mockImplementation(async () => {
      calls += 1
      return calls <= 1 ? requestRecord() : null
    })
    const { dialog, aside } = renderTwoHooksSharingOneQueryClient()
    await waitFor(() =>
      expect(dialog.result.current.pendingRequest?.id).toBe('req-1')
    )

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5_300))
    })
    await waitFor(() => {
      expect(dialog.result.current.pendingRequest).toBeNull()
      expect(aside.result.current.pendingRequest).toBeNull()
    })
    expect(aside.result.current.resolvedNotice).toContain('已消解')
  })

  it('lands a superseded notice when the pending request vanishes externally', async () => {
    // #429 P2-3 前端：手动发布/新请求在后端顶替 pending → 轮询返回 null →
    // 弹窗无声关闭是不可接受的：这里必须补一轮回执（单实例，无人 resolve）。
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
