// Job list = materialized view of the SSE snapshot+patch+revision sync protocol: a deliberately retained hand-written sync layer (react-query migration, hybrid end state). Core invariants: monotonic revision guard and incrementally maintained filterLogic derived state (see derivedStateInvariant.test.ts). Peripheral data fetching goes through react-query; do not re-add full-list fetch actions here.
import { create } from 'zustand'
import type { JobState } from './state'
import { fetchActions } from './actions/fetch'
import { snapshotActions } from './actions/snapshotActions'
import { appendActions } from './actions/appendActions'
import { patchActions } from './actions/patchActions'
import { paginationActions } from './actions/paginationActions'
import { selectionActions } from './actions/selectionActions'
import { selectionModeActions } from './actions/selectionModeActions'
import { batchActions } from './actions/batchActions'
import { pauseActions } from './actions/pauseActions'
import { rerunByFailureActions } from './actions/rerunByFailureActions'
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
  ...paginationActions(set, get),
  ...selectionActions(set, get),
  ...selectionModeActions(set, get),
  ...batchActions(set, get),
  ...pauseActions(set, get),
  ...rerunByFailureActions(set, get),
  ...clearPackedActions(set, get),
  ...upgradeActions(set, get),
}))
