import { useEffect, useRef } from 'react'
import { useJobStore } from '../stores/jobStore'
import type { JobFilterConfig } from '../stores/job/state'

const SEARCH_DEBOUNCE_MS = 400

/**
 * Server-side filtering: any filterConfig change refetches the first page
 * (and facets) with the new params. Search input is debounced; the initial
 * mount and workspace switches are skipped because the SSE snapshot load
 * already fetches with the current filter.
 */
export function useJobFilterRefetch(workspaceId: string | undefined): void {
  const filterConfig = useJobStore((state) => state.filterConfig)
  const previousRef = useRef<{
    workspaceId: string | undefined
    config: JobFilterConfig
  }>({ workspaceId, config: filterConfig })

  useEffect(() => {
    const previous = previousRef.current
    previousRef.current = { workspaceId, config: filterConfig }
    if (!workspaceId || previous.workspaceId !== workspaceId) return
    if (previous.config === filterConfig) return
    const delay =
      previous.config.search !== filterConfig.search ? SEARCH_DEBOUNCE_MS : 0
    const timer = setTimeout(() => {
      void useJobStore.getState().refreshFirstPage(workspaceId)
    }, delay)
    return () => clearTimeout(timer)
  }, [workspaceId, filterConfig])
}
