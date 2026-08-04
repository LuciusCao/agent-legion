import { createOptionAccumulator } from './filterLogic/optionAccumulator'
import { computeFilterCounts } from './filterLogic/incrementalFilters'
import { initialPaginationState } from './paginationTypes'
import { initialSelectionModeState } from './selectionModeTypes'

export const initialFilterConfig = {
  status: null,
  search: '',
  workflowVersion: null,
  activeNodeKey: null,
} as const

export const initialJobDataState = {
  jobs: [],
  jobsById: {},
  jobIds: [],
  jobIndexById: {},
  revision: 0,
  filteredJobIds: [],
  filterCounts: computeFilterCounts([], {}, initialFilterConfig),
  optionAccumulator: createOptionAccumulator([]),
  jobsWorkspaceId: null,
  isLoading: false,
  error: null,
  selectedIds: new Set<string>(),
  expandedId: null,
  filterConfig: { ...initialFilterConfig },
  selectMode: false,
  ...initialPaginationState,
  ...initialSelectionModeState,
}
