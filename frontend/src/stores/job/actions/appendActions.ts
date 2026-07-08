import type { JobSummary } from '../../../jobTypes'
import type { JobStoreSet } from '../state'
import { appendJobsSnapshotUpdate } from './appendState'

export function appendActions(set: JobStoreSet) {
  return {
    appendJobsSnapshot: (workspaceId: string, jobs: JobSummary[]) =>
      set((state) => appendJobsSnapshotUpdate(state, workspaceId, jobs)),
  }
}
