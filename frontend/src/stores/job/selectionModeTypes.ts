import type { JobListFilterParams } from '../../types/jobTypes'

export type SelectionMode = 'explicit' | 'allMatching'

/**
 * Filter-based batch selection state. In 'allMatching' mode the selection
 * is every job matching `selectionFilter` server-side, minus `excludedIds`;
 * `selectionCount` is the matched total captured when the mode was entered
 * (null when unknown, e.g. before the facets request resolves).
 */
export interface JobSelectionModeState {
  selectionMode: SelectionMode
  selectionFilter: JobListFilterParams | null
  excludedIds: Set<string>
  selectionCount: number | null
  refreshSelectionCount: (workspaceId: string) => Promise<void>
}

export const initialSelectionModeState = {
  selectionMode: 'explicit' as const,
  selectionFilter: null as JobListFilterParams | null,
  excludedIds: new Set<string>(),
  selectionCount: null as number | null,
}
