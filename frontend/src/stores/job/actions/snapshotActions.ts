import type { JobSummary } from '../../../jobTypes'
import type { JobStoreSet } from '../state'
import { setJobsSnapshotUpdate } from './snapshotState'

export function snapshotActions(set: JobStoreSet) {
  return {
    setJobsSnapshot: (
      workspaceId: string,
      revision: number,
      jobs: JobSummary[]
    ) =>
      set((state) => setJobsSnapshotUpdate(state, workspaceId, revision, jobs)),
  }
}
