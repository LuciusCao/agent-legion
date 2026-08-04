import { useEffect, useRef } from 'react'
import { useJobStore } from '../stores/jobStore'

const LOAD_AHEAD_ROWS = 10

/**
 * Trigger the next server page when the rendered window approaches the end
 * of the loaded list. Each cursor is attempted at most once (a failed load
 * leaves hasMore set but does not retry until the cursor changes, e.g. via
 * a filter refetch), so scrolling cannot spam the snapshot endpoint.
 */
export function useJobListLoadMore(
  workspaceId: string,
  rowCount: number,
  lastRenderedIndex: number
): void {
  const hasMore = useJobStore((state) => state.hasMore)
  const loadingMore = useJobStore((state) => state.loadingMore)
  const attemptedCursorRef = useRef<string | null>(null)

  useEffect(() => {
    if (!hasMore || loadingMore || rowCount === 0) return
    if (lastRenderedIndex < rowCount - LOAD_AHEAD_ROWS) return
    const cursor = useJobStore.getState().nextCursor
    if (!cursor) return
    const attemptKey = `${workspaceId}:${cursor}`
    if (attemptedCursorRef.current === attemptKey) return
    attemptedCursorRef.current = attemptKey
    void useJobStore.getState().loadMoreJobs(workspaceId)
  }, [hasMore, loadingMore, rowCount, lastRenderedIndex, workspaceId])
}
