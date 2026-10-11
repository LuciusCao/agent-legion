import type { MutableRefObject } from 'react'
import type { QueryClient } from '@tanstack/react-query'
import { useJobStore } from '../stores/jobStore'
import { loadWorkspaceJobsSnapshot } from './workspaceEventHandlers'

/**
 * Serialize concurrent snapshot loads with a generation counter: only the
 * latest load may write to the store, replay pending events, or flip
 * snapshotLoadingRef. A superseded load (e.g. a reconnect fired a fresh
 * load while an earlier paged fetch was still in flight) aborts its writes
 * and leaves the pending queue untouched for the newer load to replay.
 * The finally must also re-check isStale: generation is closure-private,
 * so a stale closure (e.g. the previous workspace's effect) still reports
 * isCurrent — without the staleness guard it would flip the shared
 * snapshotLoadingRef to false while the new workspace's snapshot is in
 * flight, and patches arriving in that window would no longer queue.
 * Two queueing layers compose here: while an SSE snapshot load is in
 * flight, events wait in pendingEventsRef; if a refreshFirstPage is in
 * flight when they replay, the store-level patch buffer
 * (jobStore.snapshotInFlight / pendingPatchBuffer) holds them again until
 * the refresh's snapshot lands — neither layer needs to know the other.
 */
export function createLoadSnapshot(
  queryClient: QueryClient,
  workspaceId: string,
  snapshotLoadingRef: MutableRefObject<boolean>,
  pendingEventsRef: MutableRefObject<MessageEvent[]>,
  processEvent: (event: MessageEvent) => void,
  isStale: () => boolean
): () => Promise<void> {
  let generation = 0
  return async () => {
    const myGeneration = ++generation
    const isCurrent = () => myGeneration === generation
    const isAborted = () => isStale() || !isCurrent()
    snapshotLoadingRef.current = true
    try {
      await loadWorkspaceJobsSnapshot(queryClient, workspaceId, isAborted)
      if (isAborted()) return
      pendingEventsRef.current.forEach(processEvent)
      pendingEventsRef.current = []
    } catch (err) {
      if (isAborted()) return
      const message =
        err instanceof Error ? err.message : 'Failed to load workspace snapshot'
      useJobStore.getState().failJobFetch(workspaceId, message)
      pendingEventsRef.current = []
    } finally {
      if (isCurrent() && !isStale()) {
        snapshotLoadingRef.current = false
      }
    }
  }
}

/**
 * Queue an event received while a snapshot load is in flight.
 * Returns false when the queue is full; the caller must then trigger a
 * resync (a fresh snapshot load) instead of silently dropping events.
 */
export function enqueuePendingEvent(
  pendingEventsRef: MutableRefObject<MessageEvent[]>,
  event: MessageEvent,
  maxPendingEvents: number
): boolean {
  if (pendingEventsRef.current.length >= maxPendingEvents) {
    return false
  }
  pendingEventsRef.current.push(event)
  return true
}
