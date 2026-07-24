import { create } from 'zustand'
import type { JobState } from './state'
import { fetchActions } from './actions/fetchActions'
import { snapshotActions } from './actions/snapshotActions'
import { appendActions } from './actions/appendActions'
import { patchActions } from './actions/patchActions'
import { selectionActions } from './actions/selectionActions'
import { batchActions } from './actions/batchActions'
import { clearPackedActions } from './actions/clearPackedActions'
import { upgradeActions } from './actions/upgradeActions'
import { initialJobDataState } from './initialState'

export const useJobStore = create<JobState>((set, get) => ({
  ...initialJobDataState,
  batchDeleteLoading: false,
  batchPackageLoading: false,
  batchClearPackedLoading: false,
  batchRerunLoading: false,
  batchRunToLoading: false,
  continueLoading: false,
  batchUpgradeWorkflowLoading: false,
  ...fetchActions(set),
  ...snapshotActions(set),
  ...appendActions(set),
  ...patchActions(set),
  ...selectionActions(set, get),
  ...batchActions(set, get),
  ...clearPackedActions(set, get),
  ...upgradeActions(set, get),
}))
