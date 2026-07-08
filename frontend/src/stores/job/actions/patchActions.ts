import type { JobSummary } from '../../../jobTypes'
import type { JobStoreSet } from '../state'
import { applyJobPatchBatchUpdate } from './patchState'

export function patchActions(set: JobStoreSet) {
  return {
    applyJobPatchBatch: (
      workspaceId: string,
      revision: number,
      patchJobs: JobSummary[],
      deletedJobIds: string[]
    ) =>
      set(
        (state) =>
          applyJobPatchBatchUpdate(
            state,
            workspaceId,
            revision,
            patchJobs,
            deletedJobIds
          ) ?? {}
      ),
  }
}
