import { create } from 'zustand'
import type { JobState } from './state'
import { fetchActions } from './actions/fetchActions'
import { selectionActions } from './actions/selectionActions'
import { batchActions } from './actions/batchActions'
import { upgradeActions } from './actions/upgradeActions'

export const useJobStore = create<JobState>((set, get) => ({
  jobs: [],
  jobsWorkspaceId: null,
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
  batchUpgradeWorkflowLoading: false,

  ...fetchActions(set),
  ...selectionActions(set, get),
  ...batchActions(set, get),
  ...upgradeActions(set, get),
}))
