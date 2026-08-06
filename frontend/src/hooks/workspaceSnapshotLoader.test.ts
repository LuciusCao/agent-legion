import { describe, it, expect, vi } from 'vitest'
import type { QueryClient } from '@tanstack/react-query'
import {
  createLoadSnapshot,
  enqueuePendingEvent,
} from './workspaceSnapshotLoader'

const { failJobFetch, loadWorkspaceJobsSnapshot } = vi.hoisted(() => ({
  failJobFetch: vi.fn(),
  loadWorkspaceJobsSnapshot: vi.fn(),
}))

// loadWorkspaceJobsSnapshot 已被 mock，queryClient 只是透传参数。
const queryClient = {} as QueryClient

vi.mock('../stores/jobStore', () => ({
  useJobStore: {
    getState: () => ({
      failJobFetch,
    }),
  },
}))

vi.mock('./workspaceEventHandlers', () => ({
  loadWorkspaceJobsSnapshot,
}))

describe('createLoadSnapshot', () => {
  it('sets loading false and clears pending events when snapshot fails', async () => {
    loadWorkspaceJobsSnapshot.mockRejectedValue(new Error('network down'))

    const snapshotLoadingRef = { current: true }
    const pendingEventsRef = { current: [{ data: 'ev1' } as MessageEvent] }
    const loadSnapshot = createLoadSnapshot(
      queryClient,
      'ws1',
      snapshotLoadingRef,
      pendingEventsRef,
      () => {},
      () => false
    )

    await loadSnapshot()

    expect(snapshotLoadingRef.current).toBe(false)
    expect(pendingEventsRef.current).toEqual([])
    expect(loadWorkspaceJobsSnapshot).toHaveBeenCalledWith(
      queryClient,
      'ws1',
      expect.any(Function)
    )
    expect(failJobFetch).toHaveBeenCalledWith('ws1', 'network down')
  })

  it('clears pending queue and sets loading false on success', async () => {
    loadWorkspaceJobsSnapshot.mockResolvedValue(undefined)

    const snapshotLoadingRef = { current: true }
    const pendingEventsRef = { current: [{ data: 'ev' } as MessageEvent] }

    const loadSnapshot = createLoadSnapshot(
      queryClient,
      'ws1',
      snapshotLoadingRef,
      pendingEventsRef,
      () => {},
      () => false
    )

    await loadSnapshot()

    expect(pendingEventsRef.current).toEqual([])
    expect(snapshotLoadingRef.current).toBe(false)
  })

  it('a superseded load aborts its writes and leaves state to the newer load', async () => {
    failJobFetch.mockClear()
    let resolveFirst!: () => void
    let firstIsAborted!: () => boolean
    loadWorkspaceJobsSnapshot
      .mockImplementationOnce(
        (
          _queryClient: QueryClient,
          _workspaceId: string,
          isStale: () => boolean
        ) => {
          firstIsAborted = isStale
          return new Promise<void>((resolve) => {
            resolveFirst = resolve
          })
        }
      )
      .mockResolvedValueOnce(undefined)

    const snapshotLoadingRef = { current: false }
    const pendingEventsRef = { current: [] as MessageEvent[] }
    const processEvent = vi.fn()
    const loadSnapshot = createLoadSnapshot(
      queryClient,
      'ws1',
      snapshotLoadingRef,
      pendingEventsRef,
      processEvent,
      () => false
    )

    const first = loadSnapshot()
    const second = loadSnapshot()
    await second

    // Newer load completed and owns the state.
    expect(snapshotLoadingRef.current).toBe(false)
    // The superseded load's abort predicate now reports aborted, so a
    // paged snapshot still in flight stops writing to the store.
    expect(firstIsAborted()).toBe(true)

    // Events queued for the in-flight load must survive the superseded
    // load's completion (the next load replays them).
    pendingEventsRef.current.push({ data: 'late' } as MessageEvent)
    processEvent.mockClear()
    resolveFirst()
    await first

    expect(snapshotLoadingRef.current).toBe(false)
    expect(pendingEventsRef.current).toHaveLength(1)
    expect(processEvent).not.toHaveBeenCalled()
    expect(failJobFetch).not.toHaveBeenCalled()
  })
})

describe('enqueuePendingEvent', () => {
  it('enqueues events below the max size', () => {
    const pendingEventsRef = { current: [] as MessageEvent[] }

    for (let i = 0; i < 3; i += 1) {
      expect(
        enqueuePendingEvent(
          pendingEventsRef,
          { data: String(i) } as MessageEvent,
          3
        )
      ).toBe(true)
    }

    expect(pendingEventsRef.current.map((e) => e.data)).toEqual(['0', '1', '2'])
  })

  it('returns false and keeps the queue intact when full', () => {
    const pendingEventsRef = {
      current: [{ data: 'a' }, { data: 'b' }] as MessageEvent[],
    }

    expect(
      enqueuePendingEvent(pendingEventsRef, { data: 'c' } as MessageEvent, 2)
    ).toBe(false)
    expect(pendingEventsRef.current.map((e) => e.data)).toEqual(['a', 'b'])
  })
})
