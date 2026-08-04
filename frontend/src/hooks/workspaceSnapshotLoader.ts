import type { MutableRefObject } from 'react'
import { useJobStore } from '../stores/jobStore'
import { loadWorkspaceJobsSnapshot } from './workspaceEventHandlers'

/**
 * Serialize concurrent snapshot loads with a generation counter: only the
 * latest load may write to the store, replay pending events, or flip
 * snapshotLoadingRef. A superseded load (e.g. a reconnect fired a fresh
 * load while an earlier paged fetch was still in flight) aborts its writes
 * and leaves the pending queue untouched for the newer load to replay.
 */
export function createLoadSnapshot(
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
      await loadWorkspaceJobsSnapshot(workspaceId, isAborted)
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
      if (isCurrent()) {
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
