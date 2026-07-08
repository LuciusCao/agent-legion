import type { JobSummary } from '../../../jobTypes'
import type { JobState } from '../state'
import { isCurrentWorkspace, normalizeJobs } from './fetchStateHelpers'

const baseReset =
  (ws: string, keepJobs: boolean) =>
  (state: JobState): Partial<JobState> => ({
    error: null,
    isLoading: true,
    ...normalizeJobs(
      keepJobs && isCurrentWorkspace(state, ws) ? state.jobs : []
    ),
    jobsWorkspaceId: ws,
    selectedIds: isCurrentWorkspace(state, ws)
      ? state.selectedIds
      : new Set<string>(),
  })

export const resetForWorkspace = (ws: string) => baseReset(ws, false)
export const startJobFetch = resetForWorkspace

export const finishJobFetch =
  (ws: string, jobs: JobSummary[]) =>
  (state: JobState): Partial<JobState> =>
    state.jobsWorkspaceId === ws
      ? { ...normalizeJobs(jobs), error: null, isLoading: false }
      : {}

export const failJobFetch =
  (ws: string, msg: string) =>
  (state: JobState): Partial<JobState> =>
    state.jobsWorkspaceId === ws
      ? { error: msg, isLoading: false, ...normalizeJobs([]) }
      : {}
