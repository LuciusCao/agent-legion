export type JobStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'paused'
  | 'awaiting_approval'

export interface JobFilterConfig {
  status: JobStatus | null
  search: string
  workflowVersion: number | 'none' | null
  activeNodeKey: string | null
  paused: boolean | null
}
