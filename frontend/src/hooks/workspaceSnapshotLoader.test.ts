import { describe, it, expect, vi } from 'vitest'
import {
  createLoadSnapshot,
  enqueuePendingEvent,
} from './workspaceSnapshotLoader'

const { failJobFetch, loadWorkspaceJobsSnapshot } = vi.hoisted(() => ({
  failJobFetch: vi.fn(),
  loadWorkspaceJobsSnapshot: vi.fn(),
}))

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
})

describe('enqueuePendingEvent', () => {
  it('drops oldest events when queue exceeds max size', () => {
    const pendingEventsRef = { current: [] as MessageEvent[] }

    for (let i = 0; i < 5; i += 1) {
      enqueuePendingEvent(
        pendingEventsRef,
        { data: String(i) } as MessageEvent,
        3
      )
    }

    expect(pendingEventsRef.current).toHaveLength(3)
    expect(pendingEventsRef.current.map((e) => e.data)).toEqual(['2', '3', '4'])
  })
})
