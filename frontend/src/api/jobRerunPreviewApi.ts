import { api } from './core'
import { targetBody, type BatchJobTarget } from './batchTarget'
import type {
  BatchRerunPreviewResult,
  JobBatchRerunPreviewRequest,
} from '../types/jobTypes'

/** Read-only eligible/total counts for a batch rerun selection. The target
 * body must be identical to what the confirm would send (selection filter +
 * exclusions in allMatching mode), or the count would lie. */
export async function previewBatchRerunJobs(
  workspaceId: string,
  target: BatchJobTarget,
  mode:
    | { nodeKey: string }
    | { fromFailedNode: true }
    | { failureCategory: string }
): Promise<BatchRerunPreviewResult> {
  const body: JobBatchRerunPreviewRequest = {
    ...targetBody(target),
    from_failed_node: false,
  }
  if ('nodeKey' in mode) body.node_key = mode.nodeKey
  else if ('fromFailedNode' in mode) body.from_failed_node = true
  else body.failure_category = mode.failureCategory
  return api<BatchRerunPreviewResult>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch-rerun/preview`,
    { method: 'POST', body: JSON.stringify(body) }
  )
}
