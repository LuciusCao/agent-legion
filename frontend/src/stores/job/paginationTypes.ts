import type { JobFacetsResponse, JobSummary } from '../../types/jobTypes'

/**
 * Server-side pagination state for the workspace job list. The store only
 * holds the loaded pages; `totalJobs`/facet counts describe the full
 * server-side result set for the active filter.
 */
export interface JobPaginationState {
  nextCursor: string | null
  hasMore: boolean
  totalJobs: number | null
  facets: JobFacetsResponse | null
  loadingMore: boolean
  setJobsPage: (
    workspaceId: string,
    revision: number,
    jobs: JobSummary[],
    total: number | null | undefined,
    nextCursor: string | null | undefined
  ) => void
  setFacets: (workspaceId: string, facets: JobFacetsResponse) => void
  loadMoreJobs: (workspaceId: string) => Promise<void>
  refreshFirstPage: (workspaceId: string) => Promise<void>
}

export const initialPaginationState = {
  nextCursor: null as string | null,
  hasMore: false,
  totalJobs: null as number | null,
  facets: null,
  loadingMore: false,
}
