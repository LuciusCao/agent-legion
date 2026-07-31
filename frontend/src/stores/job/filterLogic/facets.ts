import type { JobFacetsResponse } from '../../../types/jobTypes'
import type { FilterCounts, WorkflowVersionOptions } from './types'

/**
 * Derive filter-bar data from server facets. Facet counts already apply all
 * other filters (exclude-own-dimension) server-side, so each dimension maps
 * directly onto the client FilterCounts shape; `all` is the dimension sum
 * (jobs matching every filter except that dimension), mirroring the old
 * client-side incremental counts. Results are memoized per facets object so
 * zustand selectors keep reference stability.
 */
let cachedFacets: JobFacetsResponse | null = null
let cachedCounts: FilterCounts | null = null
let cachedNodeKeys: Set<string> | null = null
let cachedVersionOptions: WorkflowVersionOptions | null = null

function derive(facets: JobFacetsResponse): void {
  if (facets === cachedFacets) return
  cachedFacets = facets
  cachedCounts = {
    status: withAll(facets.status_counts),
    workflowVersion: withAll(facets.version_counts),
    activeNodeKey: withAll(facets.node_counts),
  }
  cachedNodeKeys = new Set(
    Object.keys(facets.node_counts).filter((key) => key !== '')
  )
  const versionOptions: number[] = []
  let hasMissingVersion = false
  for (const key of Object.keys(facets.version_counts)) {
    if (key === 'none') hasMissingVersion = true
    else versionOptions.push(Number(key))
  }
  versionOptions.sort((a, b) => b - a)
  cachedVersionOptions = { versionOptions, hasMissingVersion }
}

function withAll(counts: Record<string, number>): Record<string, number> {
  let all = 0
  for (const value of Object.values(counts)) all += value
  return { ...counts, all }
}

export function filterCountsFromFacets(
  facets: JobFacetsResponse
): FilterCounts {
  derive(facets)
  return cachedCounts as FilterCounts
}

export function nodeKeysFromFacets(facets: JobFacetsResponse): Set<string> {
  derive(facets)
  return cachedNodeKeys as Set<string>
}

export function versionOptionsFromFacets(
  facets: JobFacetsResponse
): WorkflowVersionOptions {
  derive(facets)
  return cachedVersionOptions as WorkflowVersionOptions
}
