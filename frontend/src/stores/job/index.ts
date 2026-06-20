import { create } from 'zustand'
import type { JobState } from './state'
import { fetchActions } from './actions/fetchActions'
import { selectionActions } from './actions/selectionActions'
import { batchActions } from './actions/batchActions'

export const useJobStore = create<JobState>((set, get) => ({
  jobs: [],
  isLoading: false,
  error: null,
  selectedIds: new Set(),
  expandedId: null,
  statusFilter: 'all',
  searchQuery: '',
  selectMode: false,
  batchDeleteLoading: false,
  batchPackageLoading: false,
  batchRerunLoading: false,
  batchRunToLoading: false,
  continueLoading: false,

  ...fetchActions(set, get),
  ...selectionActions(set, get),
  ...batchActions(set, get),
}))
