import type { QueryClient } from '@tanstack/react-query'
import { fetchJobFacets, fetchJobsSnapshot } from '../api'
import { toJobListFilterParams, useJobStore } from '../stores/jobStore'
import { queryKeys } from '../lib/queryKeys'
import type { WorkspaceStats } from '../types/workspaceTypes'

/**
 * Load the first page of the server-filtered job list plus the facet counts.
 * The remaining pages stream in via loadMoreJobs as the user scrolls. If the
 * filter changes mid-load, a refreshFirstPage owns the list, so bail out.
 */
export async function loadWorkspaceJobsSnapshot(
  queryClient: QueryClient,
  workspaceId: string,
  isStale: () => boolean
): Promise<void> {
  const filterConfig = useJobStore.getState().filterConfig
  const params = toJobListFilterParams(filterConfig)
  const first = await fetchJobsSnapshot(workspaceId, 500, undefined, params)
  if (isStale() || useJobStore.getState().filterConfig !== filterConfig) return
  queryClient.setQueryData<WorkspaceStats | undefined>(
    queryKeys.workspaceStats(workspaceId),
    (old) => ({ ...old, job_stats: first.stats ?? {} }) as WorkspaceStats
  )
  useJobStore
    .getState()
    .setJobsPage(
      workspaceId,
      first.revision,
      first.jobs,
      first.total,
      first.next_cursor
    )
  const facets = await fetchJobFacets(workspaceId, params).catch(() => null)
  // facets 只是计数面板：失败独立降级（保持旧值/缺失），不向调用方抛出——
  // 列表已写入，抛出会被 createLoadSnapshot 的 catch 当整页失败调
  // failJobFetch 清空刚写入的列表（#1189 codex P1-b）。
  if (!facets) return
  if (isStale() || useJobStore.getState().filterConfig !== filterConfig) return
  useJobStore.getState().setFacets(workspaceId, facets)
}
