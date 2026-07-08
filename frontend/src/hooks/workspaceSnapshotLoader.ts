import type { MutableRefObject } from 'react'
import { useJobStore } from '../stores/jobStore'
import { loadWorkspaceJobsSnapshot } from './workspaceEventHandlers'

export function createLoadSnapshot(
  workspaceId: string,
  snapshotLoadingRef: MutableRefObject<boolean>,
  pendingEventsRef: MutableRefObject<MessageEvent[]>,
  processEvent: (event: MessageEvent) => void,
  isStale: () => boolean
): () => Promise<void> {
  return async () => {
    snapshotLoadingRef.current = true
    pendingEventsRef.current = []
    try {
      await loadWorkspaceJobsSnapshot(workspaceId, isStale)
      pendingEventsRef.current.forEach(processEvent)
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Failed to load workspace snapshot'
      useJobStore.getState().failJobFetch(workspaceId, message)
    } finally {
      snapshotLoadingRef.current = false
      pendingEventsRef.current = []
    }
  }
}

export function enqueuePendingEvent(
  pendingEventsRef: MutableRefObject<MessageEvent[]>,
  event: MessageEvent,
  maxPendingEvents: number
): void {
  if (pendingEventsRef.current.length >= maxPendingEvents) {
    pendingEventsRef.current.shift()
  }
  pendingEventsRef.current.push(event)
}
