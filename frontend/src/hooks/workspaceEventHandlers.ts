import { fetchJobsSnapshot } from '../api'
import { useJobStore } from '../stores/jobStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import type { JobSummary } from '../types'
import { mergeWorkspaceEventStats } from './workspaceEventRefresh'

export interface BaseEventPayload {
  type: string
  workspace_id: string
  stats?: Record<string, number>
}
export interface JobPatchBatchPayload {
  type: 'job_patch_batch'
  workspace_id: string
  revision: number
  stats?: Record<string, number>
  jobs: JobSummary[]
  deleted_job_ids: string[]
}

export async function loadWorkspaceJobsSnapshot(
  workspaceId: string,
  isStale: () => boolean
): Promise<void> {
  const first = await fetchJobsSnapshot(workspaceId, 200, undefined)
  if (isStale()) return
  useWorkspaceStore.getState().setWorkspaceStats(workspaceId, {
    ...useWorkspaceStore.getState().workspaceStats[workspaceId],
    job_stats: first.stats,
  })
  useJobStore
    .getState()
    .setJobsSnapshot(workspaceId, first.revision, first.jobs)

  let cursor = first.next_cursor
  while (cursor) {
    const snapshot = await fetchJobsSnapshot(workspaceId, 200, cursor)
    if (isStale()) return
    useJobStore.getState().appendJobsSnapshot(workspaceId, snapshot.jobs)
    if (!snapshot.next_cursor) break
    cursor = snapshot.next_cursor
  }
}

export function handleWorkspaceEvent(
  event: MessageEvent,
  workspaceId: string,
  statsOnly: boolean,
  scheduleJobRefresh: () => void,
  loadSnapshot: () => void,
  refreshStats: () => void
): void {
  if (!event.data || event.data.startsWith(':heartbeat')) return
  try {
    const payload = JSON.parse(event.data) as BaseEventPayload
    if (payload.workspace_id !== workspaceId) return
    if (payload.stats) {
      mergeWorkspaceEventStats(workspaceId, payload.stats)
    }
    if (payload.type === 'job_patch_batch') {
      if (statsOnly) return
      const patch = JSON.parse(event.data) as JobPatchBatchPayload
      useJobStore
        .getState()
        .applyJobPatchBatch(
          workspaceId,
          patch.revision,
          patch.jobs,
          patch.deleted_job_ids || []
        )
      return
    }
    if (payload.type === 'resync_required') {
      if (statsOnly) {
        refreshStats()
      } else {
        loadSnapshot()
      }
      return
    }
    if (payload.type === 'job_updated') {
      scheduleJobRefresh()
    }
  } catch {
    // ignore invalid payloads
  }
}
