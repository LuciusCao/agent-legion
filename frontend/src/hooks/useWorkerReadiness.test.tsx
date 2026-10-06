import type { ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createTestQueryClient } from '../testing/testQueryClient'
import { useUiStore } from '../stores/uiStore'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { useWorkerReadiness } from './useWorkerReadiness'

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  workers: vi.fn(),
  console: vi.fn(),
}))
vi.mock('../api', () => ({ api: mocks.api, listAgentWorkers: mocks.workers }))
vi.mock('./useWorkerConsoleUrl', () => ({ useWorkerConsoleUrl: mocks.console }))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

function mount(initialProps = { workspaceId: 'ws1', enabled: true }) {
  const client = createTestQueryClient()
  const hook = renderHook(
    ({ workspaceId, enabled }) => useWorkerReadiness(workspaceId, enabled),
    {
      initialProps,
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    }
  )
  return { ...hook, client }
}

beforeEach(() => {
  vi.resetAllMocks()
  mocks.api.mockResolvedValue({ paused: false })
  mocks.workers.mockResolvedValue([])
  mocks.console.mockReturnValue('')
  useUiStore.setState({ toast: null })
})

describe('Worker readiness lifecycle', () => {
  it('only fetches while enabled and stops polling when hidden', async () => {
    const { result, rerender, client } = mount({
      workspaceId: 'ws1',
      enabled: false,
    })
    expect(mocks.api).not.toHaveBeenCalled()
    expect(mocks.workers).not.toHaveBeenCalled()
    expect(mocks.console).toHaveBeenLastCalledWith(false)
    rerender({ workspaceId: 'ws1', enabled: true })
    await waitFor(() => expect(result.current.paused).toBe(false))
    expect(mocks.workers).toHaveBeenCalledTimes(1)
    rerender({ workspaceId: 'ws1', enabled: false })
    await act(async () => {
      await client.invalidateQueries()
    })
    vi.useFakeTimers()
    try {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(6000)
      })
    } finally {
      vi.useRealTimers()
    }
    expect(mocks.workers).toHaveBeenCalledTimes(1)
    expect(mocks.api).toHaveBeenCalledTimes(1)
    expect(result.current.paused).toBeUndefined()
  })

  it('keeps successful data during refresh but makes a failed refresh unknown', async () => {
    const { result, client } = mount()
    await waitFor(() => expect(result.current.workers).toEqual([]))
    const next = deferred<never[]>()
    mocks.workers.mockReturnValue(next.promise)
    act(() => {
      void client.invalidateQueries({
        queryKey: extraQueryKeys.workspaceWorkers('ws1'),
      })
    })
    await waitFor(() => expect(mocks.workers).toHaveBeenCalledTimes(2))
    expect(result.current.workers).toEqual([])
    await act(async () => {
      next.reject(new Error('offline'))
      await next.promise.catch(() => {})
    })
    await waitFor(() => expect(result.current.workers).toBeUndefined())
    mocks.workers.mockResolvedValue([])
    await act(async () => {
      await client.invalidateQueries({
        queryKey: extraQueryKeys.workspaceWorkers('ws1'),
      })
    })
    await waitFor(() => expect(result.current.workers).toEqual([]))
  })

  it('applies the same refresh policy to pause status', async () => {
    const { result, client } = mount()
    await waitFor(() => expect(result.current.paused).toBe(false))
    const next = deferred<{ paused: boolean }>()
    mocks.api.mockReturnValue(next.promise)
    act(() => {
      void client.invalidateQueries({
        queryKey: ['workerReadinessStatus', 'ws1'],
      })
    })
    await waitFor(() => expect(mocks.api).toHaveBeenCalledTimes(2))
    expect(result.current.paused).toBe(false)
    await act(async () => {
      next.reject(new Error('offline'))
      await next.promise.catch(() => {})
    })
    await waitFor(() => expect(result.current.paused).toBeUndefined())
  })

  it('does not use delayed responses for the previous workspace after switching', async () => {
    const oldStatus = deferred<{ paused: boolean }>()
    const oldWorkers = deferred<unknown[]>()
    mocks.api.mockImplementation((url: string) =>
      url.endsWith('ws1')
        ? oldStatus.promise
        : Promise.resolve({ paused: false })
    )
    mocks.workers.mockImplementation((id: string) =>
      id === 'ws1' ? oldWorkers.promise : Promise.resolve([])
    )
    const { result, rerender, client } = mount()
    rerender({ workspaceId: 'ws2', enabled: true })
    await waitFor(() => expect(result.current.paused).toBe(false))
    await act(async () => {
      oldStatus.resolve({ paused: true })
      oldWorkers.resolve([{ worker_id: 'old-workspace-worker' }])
    })
    expect(result.current.paused).toBe(false)
    expect(result.current.workers).toEqual([])
    // #961：暂停位唯一来源是按 workspace 分 key 的 RQ 缓存。
    expect(client.getQueryData(['workerReadinessStatus', 'ws1'])).toBe(true)
    expect(client.getQueryData(['workerReadinessStatus', 'ws2'])).toBe(false)
  })

  it('shows failure feedback when resume fails and preserves the pause state', async () => {
    mocks.api.mockResolvedValue({ paused: true })
    const { result } = mount()
    await waitFor(() => expect(result.current.paused).toBe(true))
    mocks.api.mockRejectedValue(new Error('forbidden'))
    await act(async () => {
      result.current.resumeScheduling()
    })
    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe('更新失败，请重试')
    )
    expect(result.current.paused).toBe(true)
  })
})
